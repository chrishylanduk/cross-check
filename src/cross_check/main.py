"""FastAPI backend for Cross-check."""

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

import aiofiles
import filetype
from dotenv import load_dotenv
from openinference.instrumentation.openai import OpenAIInstrumentor
from phoenix.otel import register as phoenix_register
from fastapi import FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from markitdown import MarkItDown
from pathvalidate import sanitize_filename
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .analysis import (
    Chunk,
    InconsistencyResult,
    TopicInfo,
    check_topic_inconsistencies,
    chunk_documents,
    embed_chunks,
    run_topic_model,
)

# Load environment variables from .env file (for local development)
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Prototype password configuration
PROTOTYPE_PASSWORD_ENABLED = (
    os.getenv("DISABLE_PROTOTYPE_PASSWORD", "").lower() != "true"
)
PROTOTYPE_PASSWORD = os.getenv("PROTOTYPE_PASSWORD", "")

# Validate prototype password configuration at startup
if PROTOTYPE_PASSWORD_ENABLED and not PROTOTYPE_PASSWORD:
    logger.error(
        "PROTOTYPE_PASSWORD environment variable must be set, "
        "or set DISABLE_PROTOTYPE_PASSWORD=true to disable password protection"
    )
    sys.exit(1)

# Generate a secret token for password validation
PROTOTYPE_PASSWORD_HASH = (
    hashlib.sha256(PROTOTYPE_PASSWORD.encode()).hexdigest()
    if PROTOTYPE_PASSWORD
    else None
)

# Initialise rate limiter
limiter = Limiter(key_func=get_remote_address)

PHOENIX_ENDPOINT = os.getenv("PHOENIX_ENDPOINT", "")


def _configure_tracing() -> None:
    """Set up OpenTelemetry export to Arize Phoenix (if PHOENIX_ENDPOINT is set)."""
    if not PHOENIX_ENDPOINT:
        return
    tracer_provider = phoenix_register(
        project_name="cross-check",
        endpoint=f"{PHOENIX_ENDPOINT}/v1/traces",
    )
    OpenAIInstrumentor().instrument(tracer_provider=tracer_provider)
    logger.info(f"OpenTelemetry tracing → {PHOENIX_ENDPOINT}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Restore persisted sessions, clean up expired ones, and start background cleanup."""
    _configure_tracing()
    logger.info("=" * 60)
    logger.info("Cross-check API starting")
    logger.info(
        f"Prototype password: {'ENABLED' if PROTOTYPE_PASSWORD_ENABLED else 'DISABLED'}"
    )
    logger.info(f"CORS allowed origins: {', '.join(CORS_ORIGINS)}")
    logger.info(f"Session timeout: {SESSION_TIMEOUT}s ({SESSION_TIMEOUT // 3600}h)")
    logger.info(f"Data directory: {DATA_DIR}")
    logger.info(f"Max file size: {MAX_FILE_SIZE / 1024 / 1024:.0f}MB")
    logger.info(
        f"Max storage per session: {MAX_STORAGE_PER_SESSION / 1024 / 1024:.0f}MB"
    )

    # Restore sessions that haven't expired yet
    now = time.time()
    restored = 0
    for session_dir in DATA_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        sid = session_dir.name
        if not SESSION_ID_RE.match(sid):
            logger.warning(
                f"Skipping directory with unexpected name on restore: {sid[:20]!r}"
            )
            continue
        data = load_session_json(session_dir)
        if data is None:
            logger.warning(f"Skipping {sid[:8]}...: missing or invalid session.json")
            continue
        if now - data["created_at"] > SESSION_TIMEOUT:
            continue  # Expired — cleanup loop will remove it
        sessions[sid] = {
            "created_at": data["created_at"],
            "finalised": data["finalised"],
        }
        restored += 1
    logger.info(f"Restored {restored} active session(s) from disk")

    # Remove anything already past expiry
    cleanup_orphaned_files()

    # Start background eviction loop
    asyncio.create_task(session_cleanup_loop())
    logger.info("Session cleanup loop started (runs every 5 minutes)")
    logger.info("=" * 60)

    yield


app = FastAPI(
    title="Cross-check API",
    description="AI-assisted content audit tool",
    version="0.0.1",
    lifespan=lifespan,
    # Disable docs in production for security
    docs_url="/docs" if os.getenv("ENVIRONMENT") == "development" else None,
    redoc_url="/redoc" if os.getenv("ENVIRONMENT") == "development" else None,
)


# Pydantic models
class PasswordValidationRequest(BaseModel):
    """Request model for password validation."""

    password: str


# Prototype password middleware
@app.middleware("http")
async def prototype_password_middleware(request: Request, call_next):
    """Check prototype password on all requests (except auth endpoints)."""
    # Skip password check if disabled
    if not PROTOTYPE_PASSWORD_ENABLED:
        return await call_next(request)

    # Allow auth endpoints and health check
    if request.url.path in ["/api/auth/validate", "/health", "/"]:
        return await call_next(request)

    # Check for valid auth token in header
    auth_token = request.headers.get("X-Prototype-Auth")
    if auth_token and auth_token == PROTOTYPE_PASSWORD_HASH:
        return await call_next(request)

    # Unauthorized
    return JSONResponse(
        status_code=401,
        content={"detail": "Prototype password required"},
    )


# Add security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)

    # Security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"

    # HSTS in production only
    if os.getenv("ENVIRONMENT") == "production":
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )

    return response


def _handle_rate_limit(request: Request, exc: Exception) -> Response:
    if isinstance(exc, RateLimitExceeded):
        return _rate_limit_exceeded_handler(request, exc)
    raise exc


# Add rate limit handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _handle_rate_limit)

# Configure CORS - support both local development and production
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def session_cleanup_loop():
    """Background task: evict sessions past their fixed 24-hour expiry every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        now = time.time()
        expired = [
            sid
            for sid, session in list(sessions.items())
            if now - session["created_at"] > SESSION_TIMEOUT
        ]
        for sid in expired:
            logger.info(f"Cleanup loop: evicting expired session {sid[:8]}...")
            cleanup_session_files(sid)
            sessions.pop(sid, None)
        if expired:
            logger.info(f"Cleanup loop: evicted {len(expired)} expired session(s)")

        # Remove analysis jobs older than 24 hours
        expired_jobs = [
            jid
            for jid, job in list(analysis_jobs.items())
            if now - job["created_at"] > SESSION_TIMEOUT
        ]
        for jid in expired_jobs:
            analysis_jobs.pop(jid, None)
        if expired_jobs:
            logger.info(
                f"Cleanup loop: evicted {len(expired_jobs)} expired analysis job(s)"
            )

        cleanup_orphaned_files()


# Data directory for collections (ephemeral disk storage)
# Project root = src/cross_check/main.py -> ../../.. = project root
DATA_DIR = Path(__file__).parent.parent.parent / "data" / "collections"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Security constants
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB per file
MAX_FILENAME_LENGTH = 255  # Standard filesystem limit
MAX_FILES_PER_UPLOAD = 100  # Prevent DoS
MAX_STORAGE_PER_SESSION = 50 * 1024 * 1024  # 50MB per session
SESSION_TIMEOUT = 86400  # 24 hours in seconds

# token_urlsafe(32) produces 43 base64url characters — reject anything else on restore
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "text/plain",
    "text/html",
    "text/csv",
    "text/markdown",
}

# In-memory session store (ephemeral)
sessions: Dict[str, dict] = {}

# In-memory analysis job store (ephemeral — jobs are not persisted across restarts)
analysis_jobs: Dict[str, dict] = {}

# Initialise MarkItDown converter
md_converter = MarkItDown()


def write_session_json(session_id: str, session: dict) -> None:
    """Persist session metadata to disk so restarts don't lose active sessions."""
    json_path = DATA_DIR / session_id / "session.json"
    try:
        with open(json_path, "w") as f:
            json.dump(
                {
                    "created_at": session["created_at"],
                    "finalised": session["finalised"],
                },
                f,
            )
    except Exception as e:
        logger.error(f"Failed to write session.json for {session_id[:8]}...: {e}")


def load_session_json(session_dir: Path) -> Optional[dict]:
    """
    Load and validate session metadata from disk.
    Returns None if the file is missing, malformed, or contains invalid values.
    """
    try:
        with open(session_dir / "session.json") as f:
            data = json.load(f)
        created_at = data.get("created_at")
        finalised = data.get("finalised", False)
        # Reject if types are wrong
        if not isinstance(created_at, (int, float)) or not isinstance(finalised, bool):
            return None
        now = time.time()
        # Reject future timestamps (tampered) or impossibly old ones (> 30× timeout)
        if created_at > now or created_at < now - SESSION_TIMEOUT * 30:
            return None
        return {"created_at": float(created_at), "finalised": finalised}
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def create_session() -> str:
    """Create a new session, persist metadata to disk, and return the session ID."""
    session_id = secrets.token_urlsafe(32)
    session = {"created_at": time.time(), "finalised": False}
    sessions[session_id] = session
    session_dir = DATA_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    write_session_json(session_id, session)
    return session_id


def validate_session(session_id: str, request: Request | None = None) -> None:
    """Validate session exists and has not passed its fixed 24-hour expiry."""
    client_ip = get_remote_address(request) if request else "unknown"

    if not session_id:
        logger.warning(
            f"Session validation failed: no session ID | Client: {client_ip}"
        )
        raise HTTPException(status_code=401, detail="Session ID required")

    if session_id not in sessions:
        logger.warning(
            f"Session validation failed: unknown ID {session_id[:8]}... | Client: {client_ip}"
        )
        raise HTTPException(status_code=401, detail="Invalid session ID")

    session = sessions[session_id]
    age = time.time() - session["created_at"]
    if age > SESSION_TIMEOUT:
        logger.info(f"Session expired: {session_id[:8]}... (age {int(age)}s)")
        cleanup_session_files(session_id)
        sessions.pop(session_id, None)
        raise HTTPException(status_code=401, detail="Session expired")

    logger.debug(f"Session validated: {session_id[:8]}...")


def cleanup_session_files(session_id: str) -> None:
    """Delete all files and the directory for a session."""
    session_dir = DATA_DIR / session_id
    if session_dir.exists():
        try:
            for file in session_dir.iterdir():
                file.unlink()
            session_dir.rmdir()
            logger.info(f"Cleaned up files for session: {session_id[:8]}...")
        except Exception as e:
            logger.error(f"Failed to cleanup session {session_id[:8]}...: {e}")


def cleanup_orphaned_files() -> None:
    """Remove session directories that have no in-memory session and have passed expiry."""
    if not DATA_DIR.exists():
        return

    now = time.time()
    cleaned_count = 0

    for session_dir in DATA_DIR.iterdir():
        if not session_dir.is_dir() or session_dir.name in sessions:
            continue
        # Use created_at from session.json; fall back to directory mtime
        data = load_session_json(session_dir)
        age = now - (data["created_at"] if data else session_dir.stat().st_mtime)
        if age <= SESSION_TIMEOUT:
            continue
        try:
            for file in session_dir.iterdir():
                file.unlink()
            session_dir.rmdir()
            cleaned_count += 1
            logger.info(
                f"Removed expired orphan: {session_dir.name[:8]}... (age {int(age)}s)"
            )
        except Exception as e:
            logger.error(f"Failed to cleanup orphan {session_dir.name[:8]}...: {e}")

    if cleaned_count:
        logger.info(f"Orphan cleanup: removed {cleaned_count} directory/ies")


def get_session_storage_usage(session_id: str) -> int:
    """Calculate total storage used by a session, excluding internal metadata."""
    session_dir = DATA_DIR / session_id
    if not session_dir.exists():
        return 0
    total = 0
    try:
        for file in session_dir.iterdir():
            if file.is_file() and file.name != "session.json":
                total += file.stat().st_size
    except Exception as e:
        logger.error(f"Error calculating storage for session {session_id[:8]}...: {e}")
    return total


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Cross-check API",
        "version": "0.0.1",
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.post("/api/auth/validate")
@limiter.limit("10/minute")
async def validate_password(
    request: Request, password_request: PasswordValidationRequest
):
    """Validate prototype password and return auth token."""
    if not PROTOTYPE_PASSWORD_ENABLED:
        return {"valid": True, "token": None, "message": "Password protection disabled"}

    # Hash the provided password
    provided_hash = hashlib.sha256(password_request.password.encode()).hexdigest()

    # Check if it matches
    if provided_hash == PROTOTYPE_PASSWORD_HASH:
        logger.info(
            f"Successful prototype password validation from {get_remote_address(request)}"
        )
        return {
            "valid": True,
            "token": PROTOTYPE_PASSWORD_HASH,
            "message": "Password valid",
        }

    logger.warning(
        f"Failed prototype password attempt from {get_remote_address(request)}"
    )
    return JSONResponse(
        status_code=401,
        content={"valid": False, "token": None, "message": "Invalid password"},
    )


@app.post("/api/session")
@limiter.limit("10/minute")
async def create_session_endpoint(request: Request):
    """Create a new session for a user."""
    client_ip = get_remote_address(request)
    origin = request.headers.get("origin", "unknown")

    session_id = create_session()
    logger.info(
        f"Session created: {session_id[:8]}... | "
        f"Client: {client_ip} | Origin: {origin} | "
        f"Total active sessions: {len(sessions)}"
    )
    expires_at = sessions[session_id]["created_at"] + SESSION_TIMEOUT
    return {
        "session_id": session_id,
        "expires_at": expires_at,
    }


@app.post("/api/upload")
@limiter.limit("20/minute")
async def upload_files(
    request: Request,
    files: List[UploadFile] = File(...),
    x_session_id: str = Header(..., alias="X-Session-ID"),
):
    """Upload content files to user's collection."""
    client_ip = get_remote_address(request)
    logger.info(
        f"Upload request | Session: {x_session_id[:8] if x_session_id else 'None'}... | "
        f"Client: {client_ip} | Files: {len(files) if files else 0}"
    )

    # Validate session
    validate_session(x_session_id, request)

    session = sessions[x_session_id]

    # Check if collection is finalised
    if session.get("finalised", False):
        raise HTTPException(
            status_code=400,
            detail="Collection is finalised. Cannot upload more files. Start a new session to create a different collection.",
        )

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    # Limit number of files per upload
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_FILES_PER_UPLOAD} files per upload",
        )

    # Check current storage usage (from disk)
    current_usage = get_session_storage_usage(x_session_id)

    # Get session directory
    session_dir = DATA_DIR / x_session_id

    # Process uploaded files
    saved_files = []
    rejected_files = []
    total_new_size = 0

    for file in files:
        if not file.filename:
            continue

        logger.info(f"Processing file: {file.filename}")

        # Skip files with names that are too long
        if len(file.filename) > MAX_FILENAME_LENGTH:
            logger.warning(f"Filename too long: {file.filename}")
            rejected_files.append(
                {"name": file.filename, "reason": "Filename is too long"}
            )
            continue

        # Read file content
        content = await file.read()
        logger.info(f"File {file.filename}: size={len(content)} bytes")

        # Validate file size
        if len(content) > MAX_FILE_SIZE:
            logger.warning(
                f"File {file.filename} exceeds size limit: {len(content)} bytes"
            )
            rejected_files.append(
                {"name": file.filename, "reason": "File exceeds the 50MB size limit"}
            )
            continue

        # Check storage quota (abort entire request — not a per-file skip)
        if current_usage + total_new_size + len(content) > MAX_STORAGE_PER_SESSION:
            logger.warning(f"Storage quota exceeded for session {x_session_id}")
            raise HTTPException(
                status_code=507,
                detail=f"Storage quota exceeded. Maximum {MAX_STORAGE_PER_SESSION / 1024 / 1024}MB per session",
            )

        # Validate MIME type (hybrid approach)
        kind = filetype.guess(content)

        if kind is not None:
            # Binary file detected by magic number
            mime_type = kind.mime
            logger.info(
                f"File {file.filename}: detected MIME type={mime_type} (magic number)"
            )
        else:
            # Fallback to extension-based detection for text files
            guessed_mime, _ = mimetypes.guess_type(file.filename)
            if guessed_mime is None:
                logger.warning(f"File {file.filename}: Unable to determine MIME type")
                rejected_files.append(
                    {"name": file.filename, "reason": "File type not recognised"}
                )
                continue
            mime_type = guessed_mime
            logger.info(
                f"File {file.filename}: detected MIME type={mime_type} (extension)"
            )

        if mime_type not in ALLOWED_MIME_TYPES:
            logger.warning(
                f"File {file.filename}: MIME type {mime_type} not in allowed list"
            )
            rejected_files.append(
                {"name": file.filename, "reason": "File type not supported"}
            )
            continue

        # Sanitise filename to prevent issues
        safe_filename = sanitize_filename(file.filename)
        if not safe_filename:
            raise HTTPException(status_code=400, detail="Invalid filename")

        # Write content to temporary file for conversion
        temp_path = session_dir / f"temp_{secrets.token_hex(8)}_{safe_filename}"
        try:
            async with aiofiles.open(temp_path, "wb") as f:
                await f.write(content)

            # Convert to markdown for security
            try:
                result = md_converter.convert(str(temp_path))
                markdown_content = result.text_content

                # Validate markdown output
                if not markdown_content or not markdown_content.strip():
                    raise ValueError("Conversion resulted in empty content")

            except Exception as conv_error:
                raise HTTPException(
                    status_code=422,
                    detail=f"Failed to convert file to markdown: {type(conv_error).__name__}",
                )

            # Save as markdown to disk
            md_filename = Path(safe_filename).stem + ".md"
            md_path = session_dir / md_filename

            async with aiofiles.open(md_path, "w", encoding="utf-8") as f:
                await f.write(markdown_content)

            file_size = md_path.stat().st_size
            total_new_size += file_size
            saved_files.append({"name": md_filename, "size": file_size})

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to process file: {type(e).__name__}",
            )
        finally:
            # Clean up temporary file
            if temp_path.exists():
                temp_path.unlink()

    return {
        "message": "Files uploaded successfully",
        "file_count": len(saved_files),
        "files": saved_files,
        "rejected_files": rejected_files,
        "storage_used": current_usage + total_new_size,
        "storage_limit": MAX_STORAGE_PER_SESSION,
    }


@app.get("/api/collection")
@limiter.limit("60/minute")
async def get_collection(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
):
    """Get user's collection files (from ephemeral disk storage)."""
    logger.info(
        f"Collection request received with session: {x_session_id[:8] if x_session_id else 'None'}..."
    )

    # Validate session
    validate_session(x_session_id, request)

    session = sessions[x_session_id]
    session_dir = DATA_DIR / x_session_id

    if not session_dir.exists():
        return {
            "files": [],
            "file_count": 0,
            "storage_used": 0,
            "finalised": session.get("finalised", False),
        }

    files = []
    total_size = 0

    try:
        for file_path in session_dir.iterdir():
            if (
                file_path.is_file()
                and not file_path.name.startswith("temp_")
                and file_path.name != "session.json"
            ):
                size = file_path.stat().st_size
                files.append(
                    {
                        "name": file_path.name,
                        "size": size,
                        "modified": file_path.stat().st_mtime,
                    }
                )
                total_size += size
    except Exception as e:
        logger.error(f"Error reading collection for session {x_session_id[:8]}...: {e}")
        raise HTTPException(status_code=500, detail="Failed to read collection")

    return {
        "files": sorted(files, key=lambda x: x["modified"], reverse=True),
        "file_count": len(files),
        "storage_used": total_size,
        "storage_limit": MAX_STORAGE_PER_SESSION,
        "finalised": session.get("finalised", False),
    }


@app.delete("/api/collection/{filename}")
@limiter.limit("60/minute")
async def delete_file(
    request: Request,
    filename: str,
    x_session_id: str = Header(..., alias="X-Session-ID"),
):
    """Delete a specific file from the collection."""
    client_ip = get_remote_address(request)
    logger.info(
        f"Delete file request | Session: {x_session_id[:8]}... | File: {filename} | Client: {client_ip}"
    )

    # Validate session
    validate_session(x_session_id, request)

    session = sessions[x_session_id]

    # Check if collection is finalised
    if session.get("finalised", False):
        raise HTTPException(
            status_code=400,
            detail="Collection is finalised. Cannot delete files. Start a new session to upload different files.",
        )

    # Sanitize filename
    safe_filename = sanitize_filename(filename)
    if not safe_filename or safe_filename != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    file_path = DATA_DIR / x_session_id / safe_filename

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    try:
        file_path.unlink()
        logger.info(f"Deleted file: {safe_filename} from session {x_session_id[:8]}...")
        return {"message": "File deleted successfully", "filename": safe_filename}
    except Exception as e:
        logger.error(f"Error deleting file {safe_filename}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete file")


@app.delete("/api/collection")
@limiter.limit("20/minute")
async def clear_collection(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
):
    """Clear all files from the collection."""
    client_ip = get_remote_address(request)
    logger.info(
        f"Clear collection request | Session: {x_session_id[:8]}... | Client: {client_ip}"
    )

    # Validate session
    validate_session(x_session_id, request)

    session = sessions[x_session_id]

    # Check if collection is finalised
    if session.get("finalised", False):
        raise HTTPException(
            status_code=400,
            detail="Collection is finalised. Cannot clear files. Start a new session instead.",
        )

    session_dir = DATA_DIR / x_session_id

    if not session_dir.exists():
        return {"message": "Collection already empty", "files_deleted": 0}

    deleted_count = 0
    try:
        for file_path in session_dir.iterdir():
            if (
                file_path.is_file()
                and not file_path.name.startswith("temp_")
                and file_path.name != "session.json"
            ):
                file_path.unlink()
                deleted_count += 1

        logger.info(f"Cleared {deleted_count} files from session {x_session_id[:8]}...")
        return {
            "message": "Collection cleared successfully",
            "files_deleted": deleted_count,
        }
    except Exception as e:
        logger.error(f"Error clearing collection: {e}")
        raise HTTPException(status_code=500, detail="Failed to clear collection")


@app.post("/api/collection/finalise")
@limiter.limit("10/minute")
async def finalise_collection(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
):
    """Finalize the collection for analysis."""
    client_ip = get_remote_address(request)
    logger.info(
        f"Finalize collection request | Session: {x_session_id[:8]}... | Client: {client_ip}"
    )

    # Validate session
    validate_session(x_session_id, request)

    session = sessions[x_session_id]

    # Check if already finalised
    if session.get("finalised", False):
        return {"message": "Collection already finalised", "finalised": True}

    # Check if collection has files
    session_dir = DATA_DIR / x_session_id
    file_count = 0
    if session_dir.exists():
        file_count = sum(
            1
            for f in session_dir.iterdir()
            if f.is_file()
            and not f.name.startswith("temp_")
            and f.name != "session.json"
        )

    if file_count == 0:
        raise HTTPException(
            status_code=400, detail="Cannot finalise an empty collection"
        )

    # Mark as finalised in memory and on disk
    session["finalised"] = True
    write_session_json(x_session_id, session)
    logger.info(
        f"Collection finalised | Session: {x_session_id[:8]}... | Files: {file_count}"
    )

    return {
        "message": "Collection finalised successfully",
        "finalised": True,
        "file_count": file_count,
    }


# ---------------------------------------------------------------------------
# Analysis endpoints
# ---------------------------------------------------------------------------


async def _run_topic_discovery(job_id: str, session_id: str) -> None:
    """Background task: chunk documents, embed, and run BERTopic."""
    try:
        session_dir = DATA_DIR / session_id
        chunks = chunk_documents(session_dir)
        embeddings = embed_chunks(chunks)
        topics = run_topic_model(chunks, embeddings)

        # Serialise topics into the job state (store chunk_indices to retrieve later)
        analysis_jobs[job_id]["topics"] = [
            {**t.model_dump(), "check_status": None, "result": None} for t in topics
        ]
        analysis_jobs[job_id]["chunks"] = [c.model_dump() for c in chunks]
        analysis_jobs[job_id]["status"] = "topics_ready"
        logger.info(
            f"Topic discovery complete for job {job_id[:8]}...: {len(topics)} topics"
        )
    except Exception as exc:
        logger.exception(f"Topic discovery failed for job {job_id[:8]}...")
        analysis_jobs[job_id]["status"] = "error"
        analysis_jobs[job_id]["error"] = str(exc)


async def _run_topic_check(job_id: str, topic_id: int) -> None:
    """Background task: run LLM inconsistency check for a single topic."""
    try:
        job = analysis_jobs[job_id]
        topic = TopicInfo(**next(t for t in job["topics"] if t["id"] == topic_id))
        chunks_raw = job["chunks"]

        all_chunks = [Chunk(**c) for c in chunks_raw]
        result: InconsistencyResult = await check_topic_inconsistencies(
            topic, all_chunks
        )

        for t in job["topics"]:
            if t["id"] == topic_id:
                t["check_status"] = "complete"
                t["result"] = result.model_dump()
                break

        logger.info(
            f"Topic check complete for job {job_id[:8]}... topic {topic_id}: "
            f"{'issues found' if result.has_inconsistencies else 'no issues'}"
        )
    except Exception as exc:
        logger.exception(f"Topic check failed for job {job_id[:8]}... topic {topic_id}")
        for t in analysis_jobs[job_id]["topics"]:
            if t["id"] == topic_id:
                t["check_status"] = "error"
                t["error"] = str(exc)
                break


@app.post("/api/analysis/inconsistencies")
@limiter.limit("5/minute")
async def start_inconsistency_analysis(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Start an inconsistency analysis job for a finalised collection."""
    validate_session(x_session_id, request)

    session = sessions[x_session_id]
    if not session.get("finalised"):
        raise HTTPException(
            status_code=400, detail="Collection must be finalised before analysis"
        )

    job_id = secrets.token_urlsafe(16)
    analysis_jobs[job_id] = {
        "session_id": x_session_id,
        "status": "discovering",
        "topics": [],
        "chunks": [],
        "created_at": time.time(),
        "error": None,
    }

    asyncio.create_task(_run_topic_discovery(job_id, x_session_id))
    logger.info(
        f"Analysis job {job_id[:8]}... started for session {x_session_id[:8]}..."
    )
    return {"job_id": job_id}


@app.get("/api/analysis/{job_id}")
@limiter.limit("30/minute")
async def get_analysis_job(
    request: Request,
    job_id: str,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Poll the status and results of an analysis job."""
    validate_session(x_session_id, request)

    job = analysis_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    if job["session_id"] != x_session_id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Strip chunk_indices (internal index) from the response; keep topic_chunks
    topics_out = []
    for t in job["topics"]:
        topics_out.append({k: v for k, v in t.items() if k != "chunk_indices"})

    return {
        "status": job["status"],
        "topics": topics_out,
        "error": job["error"],
    }


@app.post("/api/analysis/{job_id}/topics/{topic_id}/check")
@limiter.limit("20/minute")
async def check_topic(
    request: Request,
    job_id: str,
    topic_id: int,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Trigger an LLM inconsistency check for a specific topic in an analysis job."""
    validate_session(x_session_id, request)

    job = analysis_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    if job["session_id"] != x_session_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if job["status"] != "topics_ready":
        raise HTTPException(status_code=400, detail="Topics not yet ready")

    topic = next((t for t in job["topics"] if t["id"] == topic_id), None)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    if topic.get("check_status") in ("checking", "complete"):
        return {"message": "Already checking or complete"}

    topic["check_status"] = "checking"
    asyncio.create_task(_run_topic_check(job_id, topic_id))
    logger.info(f"Topic check triggered: job {job_id[:8]}... topic {topic_id}")
    return {"message": "Check started"}

"""FastAPI backend for Cross-check."""

import asyncio
import hashlib
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
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
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
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
    """Initialise DB, clean up expired sessions, and start background cleanup."""
    _configure_tracing()
    _init_db()
    logger.info("=" * 60)
    logger.info("Cross-check API starting")
    logger.info(
        f"Prototype password: {'ENABLED' if PROTOTYPE_PASSWORD_ENABLED else 'DISABLED'}"
    )
    logger.info(f"CORS allowed origins: {', '.join(CORS_ORIGINS)}")
    logger.info(f"Session timeout: {SESSION_TIMEOUT}s ({SESSION_TIMEOUT // 3600}h)")
    logger.info(f"Data directory: {DATA_DIR}")
    logger.info(f"Database: {DB_PATH}")
    logger.info(f"Max file size: {MAX_FILE_SIZE / 1024 / 1024:.0f}MB")
    logger.info(
        f"Max storage per session: {MAX_STORAGE_PER_SESSION / 1024 / 1024:.0f}MB"
    )

    # Evict sessions that expired while the server was offline
    now = time.time()
    expired_ids = _db_get_expired_session_ids(now - SESSION_TIMEOUT)
    for sid in expired_ids:
        cleanup_session_files(sid)
    if expired_ids:
        logger.info(f"Evicted {len(expired_ids)} session(s) that expired at rest")

    active = _db_count_sessions()
    logger.info(f"Active sessions: {active}")

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
    if auth_token and PROTOTYPE_PASSWORD_HASH and auth_token == PROTOTYPE_PASSWORD_HASH:
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
        expired = _db_get_expired_session_ids(now - SESSION_TIMEOUT)
        for sid in expired:
            logger.info(f"Cleanup loop: evicting expired session {sid[:8]}...")
            cleanup_session_files(sid)
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
_DATA_ROOT = Path(__file__).parent.parent.parent / "data"
DATA_DIR = _DATA_ROOT / "collections"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# SQLite database for session metadata
DB_PATH = _DATA_ROOT / "sessions.db"


def _db_conn() -> sqlite3.Connection:
    """Return a new SQLite connection with safe defaults."""
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_db() -> None:
    """Create the sessions table if it does not exist."""
    with _db_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY
                    CHECK(length(session_id) = 43),
                created_at REAL NOT NULL
                    CHECK(created_at > 0),
                finalised INTEGER NOT NULL DEFAULT 0
                    CHECK(finalised IN (0, 1))
            )
        """)


def _db_create_session(session_id: str, created_at: float) -> None:
    with _db_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, created_at, finalised) VALUES (?, ?, 0)",
            (session_id, created_at),
        )


def _db_get_session(session_id: str) -> Optional[dict]:
    """Return session dict or None if not found."""
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT created_at, finalised FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if row is None:
        return None
    return {"created_at": float(row["created_at"]), "finalised": bool(row["finalised"])}


def _db_set_finalised(session_id: str) -> None:
    with _db_conn() as conn:
        conn.execute(
            "UPDATE sessions SET finalised = 1 WHERE session_id = ?",
            (session_id,),
        )


def _db_delete_session(session_id: str) -> None:
    with _db_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


def _db_get_expired_session_ids(before: float) -> List[str]:
    """Return session IDs whose created_at is before the given timestamp."""
    with _db_conn() as conn:
        rows = conn.execute(
            "SELECT session_id FROM sessions WHERE created_at < ?",
            (before,),
        ).fetchall()
    return [row["session_id"] for row in rows]


def _db_count_sessions() -> int:
    with _db_conn() as conn:
        row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
    return row[0] if row else 0


# Security constants
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB per file
MAX_FILENAME_LENGTH = 255  # Standard filesystem limit
MAX_FILES_PER_UPLOAD = 5000  # Prevent DoS
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

# In-memory analysis job store (ephemeral — jobs are not persisted across restarts)
analysis_jobs: Dict[str, dict] = {}

# Initialise MarkItDown converter
md_converter = MarkItDown()


MAX_FOOTER_CUTOFF_LENGTH = 500


def _strip_before_first_h1(content: str) -> str:
    """Remove all content before the first markdown H1 heading."""
    for i, line in enumerate(content.split("\n")):
        if line.startswith("# "):
            return "\n".join(content.split("\n")[i:]).lstrip("\n")
    return content


def _strip_after_last_occurrence(content: str, marker: str) -> str:
    """Remove everything from the last occurrence of marker onwards (inclusive)."""
    pos = content.rfind(marker)
    if pos == -1:
        return content
    return content[:pos].rstrip("\n")


def create_session() -> str:
    """Create a new session, persist to SQLite, and return the session ID."""
    session_id = secrets.token_urlsafe(32)
    created_at = time.time()
    session_dir = DATA_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    _db_create_session(session_id, created_at)
    return session_id


def validate_session(session_id: str, request: Request | None = None) -> None:
    """Validate session exists in the DB and has not passed its 24-hour expiry."""
    client_ip = get_remote_address(request) if request else "unknown"

    if not session_id:
        logger.warning(
            f"Session validation failed: no session ID | Client: {client_ip}"
        )
        raise HTTPException(status_code=401, detail="Session ID required")

    if not SESSION_ID_RE.match(session_id):
        logger.warning(f"Session validation failed: malformed ID | Client: {client_ip}")
        raise HTTPException(status_code=401, detail="Invalid session ID")

    session = _db_get_session(session_id)
    if session is None:
        logger.warning(
            f"Session validation failed: unknown ID {session_id[:8]}... | Client: {client_ip}"
        )
        raise HTTPException(status_code=401, detail="Invalid session ID")

    age = time.time() - session["created_at"]
    if age > SESSION_TIMEOUT:
        logger.info(f"Session expired: {session_id[:8]}... (age {int(age)}s)")
        cleanup_session_files(session_id)
        raise HTTPException(status_code=401, detail="Session expired")

    logger.debug(f"Session validated: {session_id[:8]}...")


def cleanup_session_files(session_id: str) -> None:
    """Delete the session row from SQLite and all files on disk."""
    _db_delete_session(session_id)
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
    """Remove session directories that have no DB entry or have passed expiry."""
    if not DATA_DIR.exists():
        return

    now = time.time()
    cleaned_count = 0

    for session_dir in DATA_DIR.iterdir():
        if not session_dir.is_dir():
            continue
        sid = session_dir.name
        if not SESSION_ID_RE.match(sid):
            continue
        session = _db_get_session(sid)
        # Skip directories that belong to an active, unexpired session
        if session is not None and now - session["created_at"] <= SESSION_TIMEOUT:
            continue
        age = now - (session["created_at"] if session else session_dir.stat().st_mtime)
        try:
            for file in session_dir.iterdir():
                file.unlink()
            session_dir.rmdir()
            cleaned_count += 1
            logger.info(f"Removed expired orphan: {sid[:8]}... (age {int(age)}s)")
        except Exception as e:
            logger.error(f"Failed to cleanup orphan {sid[:8]}...: {e}")

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
            if file.is_file():
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
        f"Total active sessions: {_db_count_sessions()}"
    )
    session = _db_get_session(session_id)
    assert session is not None
    expires_at = session["created_at"] + SESSION_TIMEOUT
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
    strip_before_h1: bool = Form(False),
    footer_cutoff: str = Form(""),
):
    """Upload content files to user's collection."""
    client_ip = get_remote_address(request)
    logger.info(
        f"Upload request | Session: {x_session_id[:8] if x_session_id else 'None'}... | "
        f"Client: {client_ip} | Files: {len(files) if files else 0}"
    )

    # Validate session
    validate_session(x_session_id, request)

    session = _db_get_session(x_session_id)

    # Check if collection is finalised
    if session and session["finalised"]:
        raise HTTPException(
            status_code=400,
            detail="Collection is finalised. Cannot upload more files. Start a new session to create a different collection.",
        )

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    # Validate processing options
    footer_cutoff = footer_cutoff.strip()
    if len(footer_cutoff) > MAX_FOOTER_CUTOFF_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Footer cutoff text must be {MAX_FOOTER_CUTOFF_LENGTH} characters or fewer",
        )

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

                # Apply HTML-specific post-processing
                if mime_type == "text/html":
                    if strip_before_h1:
                        markdown_content = _strip_before_first_h1(markdown_content)
                    if footer_cutoff:
                        markdown_content = _strip_after_last_occurrence(
                            markdown_content, footer_cutoff
                        )

                # Re-validate after processing (options could strip all content)
                if not markdown_content or not markdown_content.strip():
                    raise ValueError(
                        "Processing options removed all content from the file"
                    )

            except Exception as conv_error:
                raise HTTPException(
                    status_code=422,
                    detail=f"Failed to convert file to markdown: {type(conv_error).__name__}",
                )

            # Save as markdown to disk
            md_filename = safe_filename + ".md"
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

    session = _db_get_session(x_session_id)
    session_dir = DATA_DIR / x_session_id

    if not session_dir.exists():
        return {
            "files": [],
            "file_count": 0,
            "storage_used": 0,
            "finalised": session["finalised"] if session else False,
        }

    files = []
    total_size = 0

    try:
        for file_path in session_dir.iterdir():
            if file_path.is_file() and not file_path.name.startswith("temp_"):
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
        "finalised": session["finalised"] if session else False,
    }


@app.get("/api/collection/{filename}")
@limiter.limit("60/minute")
async def get_file_content(
    request: Request,
    filename: str,
    x_session_id: str = Header(..., alias="X-Session-ID"),
):
    """Return the markdown content of a specific file in the collection."""
    validate_session(x_session_id, request)

    safe_filename = sanitize_filename(filename)
    if not safe_filename or safe_filename != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not safe_filename.endswith(".md"):
        raise HTTPException(
            status_code=400, detail="Only markdown files can be retrieved"
        )

    session_dir = DATA_DIR / x_session_id
    file_path = session_dir / safe_filename

    # Prevent path traversal
    try:
        file_path.resolve().relative_to(session_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    if file_path.stat().st_size > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large to display")

    content = file_path.read_text(encoding="utf-8")
    return Response(content=content, media_type="text/plain; charset=utf-8")


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

    session = _db_get_session(x_session_id)

    # Check if collection is finalised
    if session and session["finalised"]:
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

    session = _db_get_session(x_session_id)

    # Check if collection is finalised
    if session and session["finalised"]:
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
            if file_path.is_file() and not file_path.name.startswith("temp_"):
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

    session = _db_get_session(x_session_id)

    # Check if already finalised
    if session and session["finalised"]:
        return {"message": "Collection already finalised", "finalised": True}

    # Check if collection has files
    session_dir = DATA_DIR / x_session_id
    file_count = 0
    if session_dir.exists():
        file_count = sum(
            1
            for f in session_dir.iterdir()
            if f.is_file() and not f.name.startswith("temp_")
        )

    if file_count == 0:
        raise HTTPException(
            status_code=400, detail="Cannot finalise an empty collection"
        )

    # Mark as finalised in SQLite
    _db_set_finalised(x_session_id)
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

# Jobs are keyed by "{session_id}:{analysis_type}" so there is exactly one
# job per analysis type per session. No separate job ID is needed.

# Limit concurrent LLM calls across all topic checks to avoid hammering the API.
LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "5"))
_llm_semaphore: asyncio.Semaphore | None = None


def _get_llm_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(LLM_CONCURRENCY)
    return _llm_semaphore


def _job_key(session_id: str, analysis_type: str) -> str:
    return f"{session_id}:{analysis_type}"


@app.get("/api/issues")
@limiter.limit("30/minute")
async def get_issues(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Return all issues found across all analysis modules for the session."""
    validate_session(x_session_id, request)

    issues = []
    modules: Dict[str, dict] = {}

    job = analysis_jobs.get(_job_key(x_session_id, "inconsistencies"))
    if job:
        topics = job.get("topics", [])
        total = len(topics)
        checked = sum(1 for t in topics if t.get("check_status") == "complete")
        in_progress = sum(1 for t in topics if t.get("check_status") == "checking")
        issue_count = 0

        for topic in topics:
            if topic.get("check_status") != "complete":
                continue
            result = topic.get("result") or {}
            if not result.get("has_inconsistencies"):
                continue
            for item in result.get("inconsistencies", []):
                issues.append(
                    {
                        "source": "inconsistencies",
                        "topic_label": topic["label"],
                        **item,
                    }
                )
                issue_count += 1

        modules["inconsistencies"] = {
            "status": job["status"],
            "total_topics": total,
            "checked_topics": checked,
            "in_progress_topics": in_progress,
            "issue_count": issue_count,
        }

    return {"issues": issues, "modules": modules}


async def _run_topic_discovery(session_id: str, analysis_type: str) -> None:
    """Background task: chunk documents, embed, and run BERTopic."""
    key = _job_key(session_id, analysis_type)
    try:
        session_dir = DATA_DIR / session_id
        chunks = chunk_documents(session_dir)
        embeddings = embed_chunks(chunks)
        topics = run_topic_model(chunks, embeddings)

        analysis_jobs[key]["topics"] = [
            {**t.model_dump(), "check_status": None, "result": None} for t in topics
        ]
        analysis_jobs[key]["chunks"] = [c.model_dump() for c in chunks]
        analysis_jobs[key]["status"] = "topics_ready"
        logger.info(f"Topic discovery complete for {key[:16]}...: {len(topics)} topics")
    except Exception as exc:
        logger.exception(f"Topic discovery failed for {key[:16]}...")
        analysis_jobs[key]["status"] = "error"
        analysis_jobs[key]["error"] = str(exc)


async def _run_topic_check(session_id: str, analysis_type: str, topic_id: int) -> None:
    """Background task: run LLM inconsistency check for a single topic."""
    key = _job_key(session_id, analysis_type)
    try:
        job = analysis_jobs[key]
        topic = TopicInfo(**next(t for t in job["topics"] if t["id"] == topic_id))
        all_chunks = [Chunk(**c) for c in job["chunks"]]
        async with _get_llm_semaphore():
            result: InconsistencyResult = await check_topic_inconsistencies(
                topic, all_chunks
            )

        for t in job["topics"]:
            if t["id"] == topic_id:
                t["check_status"] = "complete"
                t["result"] = result.model_dump()
                break

        logger.info(
            f"Topic check complete: {key[:16]}... topic {topic_id}: "
            f"{'issues found' if result.has_inconsistencies else 'no issues'}"
        )
    except Exception as exc:
        logger.exception(f"Topic check failed: {key[:16]}... topic {topic_id}")
        for t in analysis_jobs[key]["topics"]:
            if t["id"] == topic_id:
                t["check_status"] = "error"
                t["error"] = str(exc)
                break


def _get_ready_job(session_id: str, analysis_type: str) -> dict:
    """Return the job if it exists and topics are ready, else raise."""
    job = analysis_jobs.get(_job_key(session_id, analysis_type))
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")
    if job["status"] != "topics_ready":
        raise HTTPException(status_code=400, detail="Topics not yet ready")
    return job


@app.post("/api/analysis/inconsistencies")
@limiter.limit("5/minute")
async def start_inconsistency_analysis(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Start (or restart) an inconsistency analysis for a finalised collection."""
    validate_session(x_session_id, request)

    session = _db_get_session(x_session_id)
    if not session or not session["finalised"]:
        raise HTTPException(
            status_code=400, detail="Collection must be finalised before analysis"
        )

    analysis_jobs[_job_key(x_session_id, "inconsistencies")] = {
        "session_id": x_session_id,
        "status": "discovering",
        "topics": [],
        "chunks": [],
        "created_at": time.time(),
        "error": None,
    }

    asyncio.create_task(_run_topic_discovery(x_session_id, "inconsistencies"))
    logger.info(f"Inconsistencies analysis started for session {x_session_id[:8]}...")
    return {"status": "started"}


@app.get("/api/analysis/inconsistencies")
@limiter.limit("30/minute")
async def get_inconsistency_analysis(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Poll the status and results of the inconsistency analysis."""
    validate_session(x_session_id, request)

    job = analysis_jobs.get(_job_key(x_session_id, "inconsistencies"))
    if job is None:
        raise HTTPException(status_code=404, detail="Analysis job not found")

    topics_out = [
        {k: v for k, v in t.items() if k != "chunk_indices"} for t in job["topics"]
    ]
    return {"status": job["status"], "topics": topics_out, "error": job["error"]}


class BatchCheckRequest(BaseModel):
    """Request model for batch topic checks."""

    topic_ids: List[int]


@app.post("/api/analysis/inconsistencies/topics/check-all")
@limiter.limit("5/minute")
async def check_all_topics(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Trigger checks for all unchecked or previously errored topics."""
    validate_session(x_session_id, request)
    job = _get_ready_job(x_session_id, "inconsistencies")

    started = []
    for topic in job["topics"]:
        if topic.get("check_status") in (None, "error"):
            topic["check_status"] = "checking"
            asyncio.create_task(
                _run_topic_check(x_session_id, "inconsistencies", topic["id"])
            )
            started.append(topic["id"])

    logger.info(
        f"Check-all triggered for session {x_session_id[:8]}...: {len(started)} topic(s)"
    )
    return {"message": f"Started {len(started)} checks", "started": started}


@app.post("/api/analysis/inconsistencies/topics/check-batch")
@limiter.limit("10/minute")
async def check_topics_batch(
    request: Request,
    body: BatchCheckRequest,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Trigger parallel LLM inconsistency checks for multiple topics."""
    validate_session(x_session_id, request)
    job = _get_ready_job(x_session_id, "inconsistencies")

    if len(body.topic_ids) > 25:
        raise HTTPException(status_code=400, detail="Maximum 25 topics per batch")

    started = []
    for topic_id in body.topic_ids:
        topic = next((t for t in job["topics"] if t["id"] == topic_id), None)
        if topic is None or topic.get("check_status") in ("checking", "complete"):
            continue
        topic["check_status"] = "checking"
        asyncio.create_task(_run_topic_check(x_session_id, "inconsistencies", topic_id))
        started.append(topic_id)

    logger.info(
        f"Batch check triggered for session {x_session_id[:8]}...: {len(started)} topic(s)"
    )
    return {"message": f"Started {len(started)} checks", "started": started}


@app.post("/api/analysis/inconsistencies/topics/{topic_id}/check")
@limiter.limit("20/minute")
async def check_topic(
    request: Request,
    topic_id: int,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Trigger an LLM inconsistency check for a specific topic."""
    validate_session(x_session_id, request)
    job = _get_ready_job(x_session_id, "inconsistencies")

    topic = next((t for t in job["topics"] if t["id"] == topic_id), None)
    if topic is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    if topic.get("check_status") in ("checking", "complete"):
        return {"message": "Already checking or complete"}

    topic["check_status"] = "checking"
    asyncio.create_task(_run_topic_check(x_session_id, "inconsistencies", topic_id))
    logger.info(
        f"Topic check triggered: session {x_session_id[:8]}... topic {topic_id}"
    )
    return {"message": "Check started"}

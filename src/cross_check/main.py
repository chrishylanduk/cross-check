"""FastAPI backend for Cross-check."""

import asyncio
import base64
import hashlib
import httpx
import io
import json
import logging
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import time
import tomllib
import urllib.parse
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Dict, List, Optional

import aiofiles
import filetype
import jwt
from jwt import PyJWKClient
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
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
    ComplianceResult,
    InconsistencyResult,
    TopicInfo,
    check_page_compliance,
    check_topic_inconsistencies,
    chunk_documents,
    embed_chunks,
    get_embedding_model,
    run_topic_model,
    summarise_issues,
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

# Clerk auth configuration
CLERK_SECRET_KEY = os.getenv("CLERK_SECRET_KEY", "")
CLERK_PUBLISHABLE_KEY = os.getenv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "")
CLERK_ENABLED = bool(CLERK_SECRET_KEY)

_CLERK_PK_RE = re.compile(r"^pk_(live|test)_[A-Za-z0-9+/=]+$")

# Validate auth configuration at startup
if CLERK_ENABLED and PROTOTYPE_PASSWORD:
    logger.error(
        "PROTOTYPE_PASSWORD and Clerk auth cannot both be configured. "
        "Remove PROTOTYPE_PASSWORD when using Clerk."
    )
    sys.exit(1)
if CLERK_ENABLED:
    if not CLERK_PUBLISHABLE_KEY:
        logger.error(
            "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY must be set when CLERK_SECRET_KEY is provided"
        )
        sys.exit(1)
    if not _CLERK_PK_RE.match(CLERK_PUBLISHABLE_KEY):
        logger.error("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY format is invalid")
        sys.exit(1)
elif PROTOTYPE_PASSWORD_ENABLED:
    if not PROTOTYPE_PASSWORD:
        logger.error(
            "PROTOTYPE_PASSWORD must be set, "
            "or set DISABLE_PROTOTYPE_PASSWORD=true to disable password protection, "
            "or configure Clerk auth with CLERK_SECRET_KEY + NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"
        )
        sys.exit(1)
    if len(PROTOTYPE_PASSWORD) <= 6:
        logger.error("PROTOTYPE_PASSWORD must be more than 6 characters")
        sys.exit(1)

# Hash used only for password verification — never exposed externally
PROTOTYPE_PASSWORD_HASH = (
    hashlib.sha256(PROTOTYPE_PASSWORD.encode()).hexdigest()
    if PROTOTYPE_PASSWORD
    else None
)
# Random tokens issued on successful auth — separate from the password hash
VALID_AUTH_TOKENS: set[str] = set()

# ---------------------------------------------------------------------------
# Clerk JWT verification
# ---------------------------------------------------------------------------

_jwks_client: PyJWKClient | None = None


def _clerk_jwks_url() -> str:
    """Derive the JWKS URL from the Clerk publishable key."""
    raw = CLERK_PUBLISHABLE_KEY.split("_", 2)[2]
    # Pad to a multiple of 4 for standard base64 decoding
    padded = raw + "=" * (4 - len(raw) % 4)
    domain = base64.b64decode(padded).decode("utf-8").rstrip("$")
    return f"https://{domain}/.well-known/jwks.json"


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(
            _clerk_jwks_url(),
            cache_jwk_set=True,
            lifespan=3600,
        )
    return _jwks_client


def _decode_clerk_token(token: str) -> dict | None:
    """Verify a Clerk session JWT and return its claims, or None on failure."""
    try:
        client = _get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        issuer = f"https://{_clerk_jwks_url().split('/')[2]}"
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"verify_aud": False},
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Email domain allowlist
# ---------------------------------------------------------------------------

ALLOWED_EMAIL_DOMAINS: list[str] = [
    d.strip().lower()
    for d in os.getenv("ALLOWED_EMAIL_DOMAINS", "").split(",")
    if d.strip()
]


def _is_email_domain_allowed(email: str) -> bool:
    """Return True if the email's domain matches an entry in ALLOWED_EMAIL_DOMAINS.

    Prefix an entry with '@' for an exact-domain match only (e.g. '@example.com'
    allows user@example.com but not user@sub.example.com).
    Without '@', the entry also allows all subdomains (e.g. 'example.org'
    allows user@example.org and user@sub.example.org).
    If ALLOWED_EMAIL_DOMAINS is empty, all domains are allowed.
    """
    if not ALLOWED_EMAIL_DOMAINS:
        return True
    domain = email.rsplit("@", 1)[-1].lower()
    for allowed in ALLOWED_EMAIL_DOMAINS:
        if allowed.startswith("@"):
            if domain == allowed[1:]:
                return True
        elif domain == allowed or domain.endswith("." + allowed):
            return True
    return False


# Initialise rate limiter
limiter = Limiter(key_func=get_remote_address)

PHOENIX_ENDPOINT = os.getenv("PHOENIX_ENDPOINT", "")


_OPENINFERENCE_INSTRUMENTORS = [
    ("openinference.instrumentation.openai", "OpenAIInstrumentor"),
    ("openinference.instrumentation.anthropic", "AnthropicInstrumentor"),
    ("openinference.instrumentation.google_genai", "GoogleGenAIInstrumentor"),
    ("openinference.instrumentation.mistralai", "MistralAIInstrumentor"),
]


def _instrument_providers(tracer_provider: object) -> None:
    """Instrument whichever OpenInference provider packages are installed."""
    import importlib

    for module_name, class_name in _OPENINFERENCE_INSTRUMENTORS:
        try:
            module = importlib.import_module(module_name)
            getattr(module, class_name)().instrument(tracer_provider=tracer_provider)
            logger.debug(f"Instrumented {class_name}")
        except ImportError:
            pass


def _configure_tracing() -> None:
    """Set up OpenTelemetry export to Arize Phoenix (if PHOENIX_ENDPOINT is set)."""
    if not PHOENIX_ENDPOINT:
        return
    from phoenix.otel import register as phoenix_register

    tracer_provider = phoenix_register(
        project_name="cross-check",
        endpoint=f"{PHOENIX_ENDPOINT}/v1/traces",
    )
    _instrument_providers(tracer_provider)
    logger.info(f"OpenTelemetry tracing → {PHOENIX_ENDPOINT}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise DB, clean up expired sessions, and start background cleanup."""
    _configure_tracing()
    _init_db()
    logger.info("=" * 60)
    logger.info("Cross-check API starting")
    if CLERK_ENABLED:
        logger.info("Auth mode: Clerk (JWT)")
        if ALLOWED_EMAIL_DOMAINS:
            logger.info(
                f"Email domain allowlist: {len(ALLOWED_EMAIL_DOMAINS)} domain(s)"
            )
        else:
            logger.warning(
                "ALLOWED_EMAIL_DOMAINS is not set — all verified emails are permitted"
            )
    else:
        logger.info(
            f"Auth mode: prototype password {'ENABLED' if PROTOTYPE_PASSWORD_ENABLED else 'DISABLED'}"
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

    # Pre-warm the embedding model so it is loaded into memory before the first request
    loop = asyncio.get_event_loop()
    logger.info("Loading embedding model…")
    await loop.run_in_executor(None, get_embedding_model)
    logger.info("Embedding model ready")

    # Download demo files from B2 (blocking — must complete before serving begins).
    # File conversion and topic pre-computation continue in the background after startup.
    if B2_DEMO_ENABLED:
        logger.info("Downloading demo files from B2…")
        await _warm_demo_cache()
        asyncio.create_task(_process_demo_zip())
        logger.info(
            "Demo conversion + topic pre-computation running in background "
            "(watch for 'Fully ready' log below)"
        )

    # Start background eviction loop
    asyncio.create_task(session_cleanup_loop())
    logger.info("=" * 60)
    logger.info(
        "Cross-check API accepting requests"
        + (" — demo cache warming in background" if B2_DEMO_ENABLED else "")
    )
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


# Auth middleware — handles both Clerk JWT mode and prototype password mode
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Enforce authentication on all requests except health and root."""
    # Always allow health check, root, and public config (no user data)
    if request.url.path in ["/health", "/", "/api/config"]:
        return await call_next(request)

    if CLERK_ENABLED:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )
        claims = _decode_clerk_token(auth_header[7:])
        if claims is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"},
            )
        if ALLOWED_EMAIL_DOMAINS:
            # Fail closed: if email is absent from claims (not added to Clerk session token), deny access.
            email = claims.get("email", "")
            if not email or not _is_email_domain_allowed(email):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Access denied"},
                )
        return await call_next(request)

    # Prototype password mode
    if not PROTOTYPE_PASSWORD_ENABLED:
        return await call_next(request)

    # Allow the password validation endpoint itself
    if request.url.path == "/api/auth/validate":
        return await call_next(request)

    auth_token = request.headers.get("X-Prototype-Auth")
    if auth_token and auth_token in VALID_AUTH_TOKENS:
        return await call_next(request)

    return JSONResponse(
        status_code=401,
        content={"detail": "Authentication required"},
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
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_DATA_ROOT = _PROJECT_ROOT / "data"
DATA_DIR = _DATA_ROOT / "collections"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Directory for pre-built content guideline presets (committed to the repo)
GUIDELINES_DIR = _PROJECT_ROOT / "guidelines"

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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS compliance_guidelines (
                session_id TEXT PRIMARY KEY,
                guidelines TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(session_id)
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
        conn.execute(
            "DELETE FROM compliance_guidelines WHERE session_id = ?", (session_id,)
        )
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


def _db_get_compliance_guidelines(session_id: str) -> Optional[str]:
    with _db_conn() as conn:
        row = conn.execute(
            "SELECT guidelines FROM compliance_guidelines WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return row["guidelines"] if row else None


def _db_set_compliance_guidelines(session_id: str, guidelines: str) -> None:
    with _db_conn() as conn:
        conn.execute(
            "INSERT INTO compliance_guidelines (session_id, guidelines) VALUES (?, ?)"
            " ON CONFLICT(session_id) DO UPDATE SET guidelines = excluded.guidelines",
            (session_id, guidelines),
        )


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


def _is_ignored_path(name: str) -> bool:
    """Return True for macOS metadata entries and hidden files/directories."""
    parts = name.replace("\\", "/").split("/")
    return any(p.startswith(".") or p == "__MACOSX" for p in parts if p)


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


def _get_url_map(session_id: str) -> Dict[str, str]:
    """Return a {filename: url} map for all files that have metadata."""
    session_dir = DATA_DIR / session_id
    url_map: Dict[str, str] = {}
    try:
        for meta_path in session_dir.glob("*.metadata"):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if meta.get("url"):
                    url_map[meta_path.name.removesuffix(".metadata")] = meta["url"]
            except Exception:  # nosec B110
                pass
    except Exception:  # nosec B110
        pass
    return url_map


def _extract_og_url(html_bytes: bytes) -> str | None:
    """Extract og:url from HTML meta tag, trying common encodings."""
    for encoding in ("utf-8", "latin-1"):
        try:
            html = html_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        return None
    match = re.search(
        r'<meta\s[^>]*property=["\']og:url["\']\s[^>]*content=["\']([^"\']+)["\']',
        html,
        re.IGNORECASE,
    ) or re.search(
        r'<meta\s[^>]*content=["\']([^"\']+)["\']\s[^>]*property=["\']og:url["\']',
        html,
        re.IGNORECASE,
    )
    if not match:
        return None
    url = match.group(1).strip()
    # Only allow http/https to prevent javascript: and data: URI XSS
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    return url


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


@app.get("/api/config")
async def config():
    """Public feature flags for the frontend."""
    return {"demo_available": B2_DEMO_ENABLED}


@app.post("/api/auth/validate")
@limiter.limit("10/minute")
async def validate_password(
    request: Request, password_request: PasswordValidationRequest
):
    if CLERK_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")
    """Validate prototype password and return auth token."""
    if not PROTOTYPE_PASSWORD_ENABLED:
        return {"valid": True, "token": None, "message": "Password protection disabled"}  # nosec B105

    # Hash the provided password
    provided_hash = hashlib.sha256(password_request.password.encode()).hexdigest()

    # Check if it matches
    if provided_hash == PROTOTYPE_PASSWORD_HASH:
        logger.info(
            f"Successful prototype password validation from {get_remote_address(request)}"
        )
        auth_token = secrets.token_urlsafe(32)
        VALID_AUTH_TOKENS.add(auth_token)
        return {
            "valid": True,
            "token": auth_token,
            "message": "Password valid",
        }

    logger.warning(
        f"Failed prototype password attempt from {get_remote_address(request)}"
    )
    return JSONResponse(
        status_code=401,
        content={"valid": False, "token": None, "message": "Invalid password"},  # nosec B105
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


async def _run_upload_processing(
    job_id: str,
    session_id: str,
    session_dir: Path,
    pending: list[dict],
    strip_before_h1: bool,
    footer_cutoff: str,
    current_usage: int,
) -> None:
    """Convert uploaded temp files to markdown concurrently, updating _upload_jobs[job_id]."""
    job = _upload_jobs[job_id]
    job["total"] = len(pending)
    loop = asyncio.get_event_loop()
    sem = asyncio.Semaphore(os.cpu_count() or 4)

    saved_files: list[dict] = []
    rejected_files: list[dict] = []
    total_new_size = 0

    async def _process_one(item: dict) -> None:
        nonlocal total_new_size
        temp_path = Path(item["temp_path"])
        original_name = item["original_name"]
        safe_filename = item["safe_filename"]
        mime_type = item["mime_type"]
        content_len = item["content_len"]

        if current_usage + total_new_size + content_len > MAX_STORAGE_PER_SESSION:
            rejected_files.append(
                {"name": original_name, "reason": "Storage quota exceeded"}
            )
            job["processed"] += 1
            return

        async with sem:
            try:
                result = await loop.run_in_executor(
                    None, md_converter.convert, str(temp_path)
                )
                markdown_content = result.text_content
                markdown_content = markdown_content.replace(" ", " ")

                if not markdown_content or not markdown_content.strip():
                    raise ValueError("Conversion produced empty content")

                if mime_type == "text/html":
                    if strip_before_h1:
                        markdown_content = _strip_before_first_h1(markdown_content)
                    if footer_cutoff:
                        markdown_content = _strip_after_last_occurrence(
                            markdown_content, footer_cutoff
                        )

                if not markdown_content or not markdown_content.strip():
                    rejected_files.append(
                        {
                            "name": original_name,
                            "reason": "Processing options removed all content",
                        }
                    )
                    job["processed"] += 1
                    return

                md_filename = safe_filename + ".md"
                md_path = session_dir / md_filename
                async with aiofiles.open(md_path, "w", encoding="utf-8") as f:
                    await f.write(markdown_content)

                metadata: dict = {}
                if mime_type == "text/html":
                    og_url = _extract_og_url(temp_path.read_bytes())
                    if og_url:
                        metadata["url"] = og_url
                async with aiofiles.open(
                    str(md_path) + ".metadata", "w", encoding="utf-8"
                ) as f:
                    await f.write(json.dumps(metadata))

                file_size = md_path.stat().st_size
                total_new_size += file_size
                saved_files.append({"name": md_filename, "size": file_size})

            except Exception as e:
                rejected_files.append(
                    {
                        "name": original_name,
                        "reason": f"Processing failed: {type(e).__name__}",
                    }
                )
            finally:
                if temp_path.exists():
                    temp_path.unlink()

        job["processed"] += 1

    await asyncio.gather(*[_process_one(item) for item in pending])

    logger.info(
        f"Upload complete | Session: {session_id[:8]}... | "
        f"Saved: {len(saved_files)} | Rejected: {len(rejected_files)}"
    )
    job.update(
        {
            "status": "complete",
            "file_count": len(saved_files),
            "files": saved_files,
            "rejected_files": rejected_files,
            "storage_used": current_usage + total_new_size,
            "storage_limit": MAX_STORAGE_PER_SESSION,
        }
    )


@app.post("/api/upload")
@limiter.limit("20/minute")
async def upload_files(
    request: Request,
    background_tasks: BackgroundTasks,
    files: List[UploadFile] = File(...),
    x_session_id: str = Header(..., alias="X-Session-ID"),
    strip_before_h1: bool = Form(False),
    footer_cutoff: str = Form(""),
):
    """Upload content files to user's collection. Returns a job_id to poll via /api/upload/status/{job_id}."""
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

    current_usage = get_session_storage_usage(x_session_id)
    session_dir = DATA_DIR / x_session_id

    # Read and validate all files from the request body synchronously before returning.
    # Conversion happens in the background so the HTTP connection is freed immediately.
    pending: list[dict] = []
    immediate_rejected: list[dict] = []
    running_size = 0

    for file in files:
        if not file.filename or _is_ignored_path(file.filename):
            continue

        if len(file.filename) > MAX_FILENAME_LENGTH:
            immediate_rejected.append(
                {"name": file.filename, "reason": "Filename is too long"}
            )
            continue

        content = await file.read()

        if len(content) > MAX_FILE_SIZE:
            immediate_rejected.append(
                {"name": file.filename, "reason": "File exceeds the 50MB size limit"}
            )
            continue

        if current_usage + running_size + len(content) > MAX_STORAGE_PER_SESSION:
            raise HTTPException(
                status_code=507,
                detail=f"Storage quota exceeded. Maximum {MAX_STORAGE_PER_SESSION / 1024 / 1024}MB per session",
            )

        kind = filetype.guess(content)
        if kind is not None:
            mime_type = kind.mime
        else:
            guessed_mime, _ = mimetypes.guess_type(file.filename)
            if guessed_mime is None:
                immediate_rejected.append(
                    {"name": file.filename, "reason": "File type not recognised"}
                )
                continue
            mime_type = guessed_mime

        if mime_type not in ALLOWED_MIME_TYPES:
            immediate_rejected.append(
                {"name": file.filename, "reason": "File type not supported"}
            )
            continue

        raw_filename = file.filename.replace("\\", "/")
        path_parts = [p for p in raw_filename.split("/") if p]
        joined = "-".join(path_parts) if len(path_parts) > 1 else raw_filename
        safe_filename = sanitize_filename(joined)
        if not safe_filename:
            raise HTTPException(status_code=400, detail="Invalid filename")

        temp_path = session_dir / f"temp_{secrets.token_hex(8)}_{safe_filename}"
        async with aiofiles.open(temp_path, "wb") as f:
            await f.write(content)

        running_size += len(content)
        pending.append(
            {
                "temp_path": str(temp_path),
                "original_name": file.filename,
                "safe_filename": safe_filename,
                "mime_type": mime_type,
                "content_len": len(content),
            }
        )

    logger.info(
        f"Upload received | Session: {x_session_id[:8]}... | "
        f"Queued: {len(pending)} | Pre-rejected: {len(immediate_rejected)}"
    )

    job_id = secrets.token_hex(16)
    _upload_jobs[job_id] = {
        "status": "processing",
        "processed": 0,
        "total": len(pending),
        "rejected_files": immediate_rejected,
        "_session_id": x_session_id,
    }

    background_tasks.add_task(
        _run_upload_processing,
        job_id,
        x_session_id,
        session_dir,
        pending,
        strip_before_h1,
        footer_cutoff,
        current_usage,
    )

    return {"job_id": job_id, "status": "processing"}


@app.get("/api/upload/status/{job_id}")
async def upload_status(
    request: Request,
    job_id: str,
    x_session_id: str = Header(..., alias="X-Session-ID"),
):
    """Poll the status of an upload job started by /api/upload."""
    validate_session(x_session_id, request)
    job = _upload_jobs.get(job_id)
    if job is None or job.get("_session_id") != x_session_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return {k: v for k, v in job.items() if not k.startswith("_")}


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
            if (
                file_path.is_file()
                and not file_path.name.startswith("temp_")
                and not file_path.name.endswith(".metadata")
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
# B2 demo integration
# ---------------------------------------------------------------------------

B2_KEY_ID = os.getenv("B2_KEY_ID", "")
B2_APPLICATION_KEY = os.getenv("B2_APPLICATION_KEY", "")
B2_BUCKET_NAME = os.getenv("B2_BUCKET_NAME", "")
B2_DEMO_ENABLED = bool(B2_KEY_ID and B2_APPLICATION_KEY and B2_BUCKET_NAME)

_B2_API_URL = "https://api.backblazeb2.com"
_B2_DEMO_CONFIG = "demo.toml"

_DEMO_CACHE_DIR = _DATA_ROOT / "demo_cache"
_DEMO_ZIP_CACHE = _DEMO_CACHE_DIR / "demo.zip"
_DEMO_TOML_CACHE = _DEMO_CACHE_DIR / "demo.toml"
_DEMO_PROCESSED_DIR = _DEMO_CACHE_DIR / "processed"
_DEMO_TOPICS_CACHE = _DEMO_CACHE_DIR / "demo_topics.json"

# SHA-256 of sorted demo filenames — set once after startup pre-processing completes.
# Used to detect unmodified demo sessions eligible for the pre-computed topic cache.
_demo_fileset_hash: str | None = None

# In-memory stores for background jobs: job_id -> status dict
_demo_jobs: dict[str, dict] = {}
_upload_jobs: dict[str, dict] = {}


async def _b2_authorize(client: httpx.AsyncClient) -> dict:
    resp = await client.get(
        f"{_B2_API_URL}/b2api/v2/b2_authorize_account",
        auth=(B2_KEY_ID, B2_APPLICATION_KEY),
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


async def _b2_download(client: httpx.AsyncClient, auth: dict, file_name: str) -> bytes:
    resp = await client.get(
        f"{auth['downloadUrl']}/file/{B2_BUCKET_NAME}/{urllib.parse.quote(file_name, safe='/')}",
        headers={"Authorization": auth["authorizationToken"]},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.content


async def _warm_demo_cache() -> None:
    """Download demo files from B2 and cache to disk for fast subsequent loads."""
    _DEMO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        async with httpx.AsyncClient() as client:
            auth = await _b2_authorize(client)
            try:
                toml_bytes = await _b2_download(client, auth, _B2_DEMO_CONFIG)
                _DEMO_TOML_CACHE.write_bytes(toml_bytes)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    _DEMO_TOML_CACHE.unlink(missing_ok=True)
                else:
                    raise
            zip_bytes = await _b2_download(client, auth, "demo.zip")
            _DEMO_ZIP_CACHE.write_bytes(zip_bytes)
        logger.info(f"Demo cache ready ({_DEMO_ZIP_CACHE.stat().st_size // 1024} KB)")
    except Exception as exc:
        logger.warning(
            f"Demo cache warm failed: {exc} — files will be fetched from B2 on demand"
        )


async def _process_demo_zip() -> None:
    """Convert all files in the cached demo zip to markdown once at startup.

    Subsequent demo loads copy from _DEMO_PROCESSED_DIR instead of converting,
    making them near-instant regardless of how many files are in the archive.
    Conversions run concurrently, bounded by CPU count.
    """
    if not _DEMO_ZIP_CACHE.exists():
        return

    _DEMO_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    strip_before_h1 = False
    footer_cutoff = ""
    if _DEMO_TOML_CACHE.exists():
        try:
            config = tomllib.loads(_DEMO_TOML_CACHE.read_text("utf-8"))
            strip_before_h1 = bool(config.get("strip_before_h1", False))
            footer_cutoff = str(config.get("footer_cutoff", ""))[
                :MAX_FOOTER_CUTOFF_LENGTH
            ]
        except Exception:
            pass

    try:
        zf = zipfile.ZipFile(io.BytesIO(_DEMO_ZIP_CACHE.read_bytes()))
    except zipfile.BadZipFile:
        logger.warning("Demo zip is invalid — skipping pre-processing")
        return

    loop = asyncio.get_event_loop()
    entries = [
        e for e in zf.infolist() if not e.is_dir() and not _is_ignored_path(e.filename)
    ]
    sem = asyncio.Semaphore(os.cpu_count() or 4)

    async def _convert_one(entry: zipfile.ZipInfo) -> bool:
        file_name = entry.filename
        parts = [p for p in file_name.replace("\\", "/").split("/") if p]
        joined = "-".join(parts) if len(parts) > 1 else file_name
        safe_filename = sanitize_filename(joined)
        if not safe_filename:
            return False

        md_path = _DEMO_PROCESSED_DIR / (safe_filename + ".md")
        if md_path.exists():
            return True  # already processed from a previous startup

        content = zf.read(entry)
        if len(content) > MAX_FILE_SIZE:
            return False

        kind = filetype.guess(content)
        mime_type = (
            kind.mime
            if kind is not None
            else (mimetypes.guess_type(file_name)[0] or "")
        )
        if not mime_type or mime_type not in ALLOWED_MIME_TYPES:
            return False

        temp_path = _DEMO_PROCESSED_DIR / f"temp_{secrets.token_hex(8)}_{safe_filename}"
        async with sem:
            try:
                async with aiofiles.open(temp_path, "wb") as f:
                    await f.write(content)

                result = await loop.run_in_executor(
                    None, md_converter.convert, str(temp_path)
                )
                markdown_content = result.text_content
                if not markdown_content or not markdown_content.strip():
                    return False

                if mime_type == "text/html":
                    if strip_before_h1:
                        markdown_content = _strip_before_first_h1(markdown_content)
                    if footer_cutoff:
                        markdown_content = _strip_after_last_occurrence(
                            markdown_content, footer_cutoff
                        )
                if not markdown_content or not markdown_content.strip():
                    return False

                async with aiofiles.open(md_path, "w", encoding="utf-8") as f:
                    await f.write(markdown_content)

                metadata: dict = {}
                if mime_type == "text/html":
                    og_url = _extract_og_url(content)
                    if og_url:
                        metadata["url"] = og_url
                async with aiofiles.open(
                    str(md_path) + ".metadata", "w", encoding="utf-8"
                ) as f:
                    await f.write(json.dumps(metadata))

                return True
            except Exception as exc:
                logger.warning(f"Demo pre-process failed for {file_name}: {exc}")
                return False
            finally:
                if temp_path.exists():
                    temp_path.unlink()

    results = await asyncio.gather(*[_convert_one(e) for e in entries])
    saved = sum(1 for r in results if r)
    logger.info(
        f"Demo pre-processing complete: {saved}/{len(entries)} files ready in cache"
    )
    await _precompute_demo_topics()


async def _precompute_demo_topics() -> None:
    """Chunk, embed, and run BERTopic on the demo markdown files once at startup.

    Subsequent inconsistency analyses on unmodified demo sessions skip topic
    discovery entirely and load results from _DEMO_TOPICS_CACHE instead.
    """
    global _demo_fileset_hash

    if not _DEMO_PROCESSED_DIR.exists():
        logger.info("=" * 60)
        logger.info("Cross-check fully ready (no demo files)")
        logger.info("=" * 60)
        return
    md_files = sorted(_DEMO_PROCESSED_DIR.glob("*.md"))
    if not md_files:
        logger.info("=" * 60)
        logger.info("Cross-check fully ready (demo directory empty)")
        logger.info("=" * 60)
        return

    _demo_fileset_hash = hashlib.sha256(
        "|".join(f.name for f in md_files).encode()
    ).hexdigest()

    # Validate existing cache — reuse if the fileset hasn't changed.
    if _DEMO_TOPICS_CACHE.exists():
        try:
            cached = json.loads(_DEMO_TOPICS_CACHE.read_text(encoding="utf-8"))
            if cached.get("fileset_hash") == _demo_fileset_hash:
                logger.info("=" * 60)
                logger.info(
                    f"Cross-check fully ready — demo topic cache valid "
                    f"({len(cached['topics'])} topics, {len(cached['chunks'])} chunks)"
                )
                logger.info("=" * 60)
                return
        except Exception:
            pass
        _DEMO_TOPICS_CACHE.unlink(missing_ok=True)

    logger.info(
        f"Pre-computing demo topics for {len(md_files)} markdown files "
        "(chunking → embedding → BERTopic)…"
    )
    loop = asyncio.get_event_loop()
    try:
        async with _get_analysis_semaphore():
            logger.info("Demo topics: chunking…")
            chunks = await loop.run_in_executor(
                None, chunk_documents, _DEMO_PROCESSED_DIR
            )
            logger.info(f"Demo topics: embedding {len(chunks)} chunks…")
            embeddings = await loop.run_in_executor(None, embed_chunks, chunks)
            logger.info("Demo topics: running BERTopic…")
            topics = await loop.run_in_executor(
                None, run_topic_model, chunks, embeddings
            )

        _DEMO_TOPICS_CACHE.write_text(
            json.dumps(
                {
                    "fileset_hash": _demo_fileset_hash,
                    "chunks": [c.model_dump() for c in chunks],
                    "topics": [
                        {**t.model_dump(), "check_status": None, "result": None}
                        for t in topics
                    ],
                }
            ),
            encoding="utf-8",
        )
        logger.info("=" * 60)
        logger.info(
            f"Cross-check fully ready — demo topics cached "
            f"({len(topics)} topics, {len(chunks)} chunks); "
            "unmodified demo sessions will skip topic discovery"
        )
        logger.info("=" * 60)
    except Exception as exc:
        logger.warning(f"Demo topics pre-computation failed: {exc}")
        logger.info("=" * 60)
        logger.info(
            "Cross-check fully ready (demo topic cache unavailable — will compute on demand)"
        )
        logger.info("=" * 60)


def _session_matches_demo(session_id: str) -> bool:
    """Return True if the session's markdown files exactly match the demo fileset."""
    if _demo_fileset_hash is None:
        return False
    session_dir = DATA_DIR / session_id
    session_files = sorted(f.name for f in session_dir.glob("*.md"))
    session_hash = hashlib.sha256("|".join(session_files).encode()).hexdigest()
    return session_hash == _demo_fileset_hash


def _load_demo_topics_into_job(key: str) -> bool:
    """Populate analysis_jobs[key] from the pre-computed demo cache. Returns True on success."""
    if not _DEMO_TOPICS_CACHE.exists():
        return False
    try:
        cached = json.loads(_DEMO_TOPICS_CACHE.read_text(encoding="utf-8"))
        if cached.get("fileset_hash") != _demo_fileset_hash:
            return False
        analysis_jobs[key].update(
            {
                "status": "topics_ready",
                "chunk_count": len(cached["chunks"]),
                "chunks": cached["chunks"],
                "topics": cached["topics"],
            }
        )
        return True
    except Exception as exc:
        logger.warning(f"Failed to load demo topics cache: {exc}")
        return False


async def _run_demo_load_from_cache(
    job_id: str,
    session_id: str,
    session_dir: Path,
    current_usage: int,
) -> None:
    """Copy pre-processed markdown files from _DEMO_PROCESSED_DIR into the session directory."""
    job = _demo_jobs[job_id]

    md_files = sorted(_DEMO_PROCESSED_DIR.glob("*.md"))
    job["total"] = len(md_files)

    # Check quota up front using cached file sizes
    total_size = sum(p.stat().st_size for p in md_files)
    if current_usage + total_size > MAX_STORAGE_PER_SESSION:
        job.update(
            {
                "status": "error",
                "error": f"Storage quota exceeded. Maximum {MAX_STORAGE_PER_SESSION // 1024 // 1024}MB per session",
            }
        )
        return

    async def _copy_one(md_path: Path) -> dict:
        content = md_path.read_bytes()
        dest = session_dir / md_path.name
        async with aiofiles.open(dest, "wb") as f:
            await f.write(content)

        meta_src = Path(str(md_path) + ".metadata")
        meta_dest = session_dir / (md_path.name + ".metadata")
        if meta_src.exists():
            async with aiofiles.open(meta_dest, "wb") as f:
                await f.write(meta_src.read_bytes())
        else:
            async with aiofiles.open(meta_dest, "w", encoding="utf-8") as f:
                await f.write("{}")

        job["processed"] += 1
        return {"name": md_path.name, "size": dest.stat().st_size}

    saved_files = await asyncio.gather(*[_copy_one(p) for p in md_files])

    logger.info(
        f"Demo load (cache) complete | Session: {session_id[:8]}... | Loaded: {len(saved_files)}"
    )
    job.update(
        {
            "status": "complete",
            "file_count": len(saved_files),
            "files": list(saved_files),
            "rejected_files": [],
            "storage_used": current_usage + total_size,
            "storage_limit": MAX_STORAGE_PER_SESSION,
        }
    )


async def _run_demo_load(
    job_id: str,
    session_id: str,
    session_dir: Path,
    zip_bytes: bytes,
    strip_before_h1: bool,
    footer_cutoff: str,
    current_usage: int,
) -> None:
    """Process demo zip in the background, updating _demo_jobs[job_id] as work progresses."""
    job = _demo_jobs[job_id]
    saved_files: list[dict] = []
    rejected_files: list[dict] = []
    total_new_size = 0

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        job.update({"status": "error", "error": "Demo archive is not a valid zip file"})
        return

    entries = [
        e for e in zf.infolist() if not e.is_dir() and not _is_ignored_path(e.filename)
    ]
    job["total"] = len(entries)
    loop = asyncio.get_event_loop()

    for entry in entries:
        file_name = entry.filename
        parts = [p for p in file_name.replace("\\", "/").split("/") if p]
        joined = "-".join(parts) if len(parts) > 1 else file_name
        safe_filename = sanitize_filename(joined)
        if not safe_filename:
            rejected_files.append({"name": file_name, "reason": "Invalid filename"})
            job["processed"] += 1
            continue

        content = zf.read(entry)

        if len(content) > MAX_FILE_SIZE:
            rejected_files.append(
                {"name": file_name, "reason": "File exceeds the 50MB size limit"}
            )
            job["processed"] += 1
            continue

        if current_usage + total_new_size + len(content) > MAX_STORAGE_PER_SESSION:
            job.update(
                {
                    "status": "error",
                    "error": f"Storage quota exceeded. Maximum {MAX_STORAGE_PER_SESSION // 1024 // 1024}MB per session",
                }
            )
            return

        kind = filetype.guess(content)
        if kind is not None:
            mime_type = kind.mime
        else:
            guessed_mime, _ = mimetypes.guess_type(file_name)
            if guessed_mime is None:
                rejected_files.append(
                    {"name": file_name, "reason": "File type not recognised"}
                )
                job["processed"] += 1
                continue
            mime_type = guessed_mime

        if mime_type not in ALLOWED_MIME_TYPES:
            rejected_files.append(
                {"name": file_name, "reason": "File type not supported"}
            )
            job["processed"] += 1
            continue

        temp_path = session_dir / f"temp_{secrets.token_hex(8)}_{safe_filename}"
        try:
            async with aiofiles.open(temp_path, "wb") as f:
                await f.write(content)

            # md_converter.convert is blocking — run in thread pool
            result = await loop.run_in_executor(
                None, md_converter.convert, str(temp_path)
            )
            markdown_content = result.text_content
            markdown_content = markdown_content.replace(" ", " ")

            if not markdown_content or not markdown_content.strip():
                raise ValueError("Conversion produced empty content")

            if mime_type == "text/html":
                if strip_before_h1:
                    markdown_content = _strip_before_first_h1(markdown_content)
                if footer_cutoff:
                    markdown_content = _strip_after_last_occurrence(
                        markdown_content, footer_cutoff
                    )

            if not markdown_content or not markdown_content.strip():
                rejected_files.append(
                    {
                        "name": file_name,
                        "reason": "Processing options removed all content",
                    }
                )
                job["processed"] += 1
                continue

            md_filename = safe_filename + ".md"
            md_path = session_dir / md_filename

            async with aiofiles.open(md_path, "w", encoding="utf-8") as f:
                await f.write(markdown_content)

            metadata: dict = {}
            if mime_type == "text/html":
                og_url = _extract_og_url(content)
                if og_url:
                    metadata["url"] = og_url
            meta_path = session_dir / (md_filename + ".metadata")
            async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(metadata))

            file_size = md_path.stat().st_size
            total_new_size += file_size
            saved_files.append({"name": md_filename, "size": file_size})

        except Exception as e:
            rejected_files.append(
                {"name": file_name, "reason": f"Processing failed: {type(e).__name__}"}
            )
        finally:
            if temp_path.exists():
                temp_path.unlink()

        job["processed"] += 1

    logger.info(
        f"Demo load complete | Session: {session_id[:8]}... | "
        f"Loaded: {len(saved_files)} | Rejected: {len(rejected_files)}"
    )
    job.update(
        {
            "status": "complete",
            "file_count": len(saved_files),
            "files": saved_files,
            "rejected_files": rejected_files,
            "storage_used": current_usage + total_new_size,
            "storage_limit": MAX_STORAGE_PER_SESSION,
        }
    )


@app.post("/api/demo/load")
@limiter.limit("3/minute")
async def load_demo_files(
    request: Request,
    background_tasks: BackgroundTasks,
    x_session_id: str = Header(..., alias="X-Session-ID"),
):
    """Start a background job to load demo files. Poll /api/demo/status/{job_id} for progress."""
    if not B2_DEMO_ENABLED:
        raise HTTPException(status_code=404, detail="Demo files not configured")

    validate_session(x_session_id, request)
    session = _db_get_session(x_session_id)
    if session and session["finalised"]:
        raise HTTPException(
            status_code=400,
            detail="Collection is finalised. Start a new session to load demo files.",
        )

    session_dir = DATA_DIR / x_session_id
    current_usage = get_session_storage_usage(x_session_id)

    job_id = secrets.token_hex(16)
    _demo_jobs[job_id] = {
        "status": "processing",
        "processed": 0,
        "total": 0,
        "_session_id": x_session_id,
    }

    # Fast path: startup has already converted all files — just copy them
    if _DEMO_PROCESSED_DIR.exists() and any(_DEMO_PROCESSED_DIR.glob("*.md")):
        background_tasks.add_task(
            _run_demo_load_from_cache,
            job_id,
            x_session_id,
            session_dir,
            current_usage,
        )
        return {"job_id": job_id, "status": "processing"}

    # Slow path: processed cache not ready yet — convert from zip
    strip_before_h1 = False
    footer_cutoff = ""

    if _DEMO_ZIP_CACHE.exists():
        zip_bytes = _DEMO_ZIP_CACHE.read_bytes()
        if _DEMO_TOML_CACHE.exists():
            try:
                config = tomllib.loads(_DEMO_TOML_CACHE.read_text("utf-8"))
                strip_before_h1 = bool(config.get("strip_before_h1", False))
                footer_cutoff = str(config.get("footer_cutoff", ""))[
                    :MAX_FOOTER_CUTOFF_LENGTH
                ]
            except Exception:
                pass
    else:
        try:
            async with httpx.AsyncClient() as client:
                auth = await _b2_authorize(client)
                try:
                    config_bytes = await _b2_download(client, auth, _B2_DEMO_CONFIG)
                    config = tomllib.loads(config_bytes.decode("utf-8"))
                    strip_before_h1 = bool(config.get("strip_before_h1", False))
                    footer_cutoff = str(config.get("footer_cutoff", ""))[
                        :MAX_FOOTER_CUTOFF_LENGTH
                    ]
                except httpx.HTTPStatusError as e:
                    if e.response.status_code != 404:
                        logger.warning(f"Could not read {_B2_DEMO_CONFIG}: {e}")
                except Exception:
                    pass
                zip_bytes = await _b2_download(client, auth, "demo.zip")
        except httpx.HTTPStatusError as e:
            logger.error(f"B2 request failed: {e}")
            raise HTTPException(
                status_code=502, detail="Failed to load demo files from storage"
            )
        except Exception as e:
            logger.error(f"Demo load failed: {type(e).__name__}: {e}")
            raise HTTPException(
                status_code=502, detail="Failed to load demo files from storage"
            )

    background_tasks.add_task(
        _run_demo_load,
        job_id,
        x_session_id,
        session_dir,
        zip_bytes,
        strip_before_h1,
        footer_cutoff,
        current_usage,
    )

    return {"job_id": job_id, "status": "processing"}


@app.get("/api/demo/status/{job_id}")
async def demo_load_status(
    request: Request,
    job_id: str,
    x_session_id: str = Header(..., alias="X-Session-ID"),
):
    """Poll the status of a demo load job started by /api/demo/load."""
    validate_session(x_session_id, request)
    job = _demo_jobs.get(job_id)
    if job is None or job.get("_session_id") != x_session_id:
        raise HTTPException(status_code=404, detail="Job not found")
    return {k: v for k, v in job.items() if not k.startswith("_")}


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


# Cap simultaneous embedding + BERTopic runs — each is memory-intensive.
# Default 1 is safe for small single-server deployments; raise via ANALYSIS_CONCURRENCY.
ANALYSIS_CONCURRENCY = int(os.getenv("ANALYSIS_CONCURRENCY", "1"))
_analysis_semaphore: asyncio.Semaphore | None = None


def _get_analysis_semaphore() -> asyncio.Semaphore:
    global _analysis_semaphore
    if _analysis_semaphore is None:
        _analysis_semaphore = asyncio.Semaphore(ANALYSIS_CONCURRENCY)
    return _analysis_semaphore


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

    compliance_job = analysis_jobs.get(_job_key(x_session_id, "compliance"))
    if compliance_job:
        pages = compliance_job.get("pages", [])
        total = len(pages)
        checked = sum(1 for p in pages if p.get("check_status") == "complete")
        in_progress = sum(1 for p in pages if p.get("check_status") == "checking")
        issue_count = 0
        for page in pages:
            if page.get("check_status") != "complete":
                continue
            result = page.get("result") or {}
            if not result.get("has_issues"):
                continue
            for item in result.get("issues", []):
                issues.append(
                    {
                        "source": "compliance",
                        "topic_label": page["filename"],
                        "type": "compliance_violation",
                        "documents_involved": [page["filename"]],
                        **item,
                    }
                )
                issue_count += 1
        modules["compliance"] = {
            "status": compliance_job["status"],
            "total_topics": total,
            "checked_topics": checked,
            "in_progress_topics": in_progress,
            "issue_count": issue_count,
        }

    return {"issues": issues, "modules": modules, "url_map": _get_url_map(x_session_id)}


@app.post("/api/issues/{module}/summarise")
@limiter.limit("10/minute")
async def summarise_module_issues(
    request: Request,
    module: str,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Generate an LLM summary of all found issues for the given module."""
    validate_session(x_session_id, request)

    if module not in ("inconsistencies", "compliance"):
        raise HTTPException(status_code=400, detail="Invalid module")

    job = analysis_jobs.get(_job_key(x_session_id, module))
    if not job:
        raise HTTPException(status_code=404, detail="No analysis job found")

    issues: list[dict] = []
    if module == "inconsistencies":
        for topic in job.get("topics", []):
            if topic.get("check_status") != "complete":
                continue
            for item in (topic.get("result") or {}).get("inconsistencies", []):
                issues.append({"description": item.get("description", "")})
    else:
        for page in job.get("pages", []):
            if page.get("check_status") != "complete":
                continue
            for item in (page.get("result") or {}).get("issues", []):
                issues.append({"description": item.get("description", "")})

    if not issues:
        return {"summary": "No issues to summarise."}

    guidelines = (
        _db_get_compliance_guidelines(x_session_id) if module == "compliance" else None
    )
    summary = await summarise_issues(issues, guidelines=guidelines)
    return {"summary": summary}


async def _run_topic_discovery(session_id: str, analysis_type: str) -> None:
    """Background task: chunk documents, embed, and run BERTopic."""
    key = _job_key(session_id, analysis_type)
    try:
        loop = asyncio.get_event_loop()
        session_dir = DATA_DIR / session_id
        # All three functions are CPU/IO-bound synchronous calls. Running them in
        # the thread pool keeps the event loop free to serve other users' requests
        # while analysis is in progress. The semaphore prevents concurrent BERTopic
        # runs from exhausting RAM on a small server.
        async with _get_analysis_semaphore():
            analysis_jobs[key]["phase"] = "chunking"
            logger.info(f"Topic discovery: chunking | {key[:16]}...")
            chunks = await loop.run_in_executor(None, chunk_documents, session_dir)
            analysis_jobs[key]["chunk_count"] = len(chunks)
            analysis_jobs[key]["phase"] = "embedding"
            logger.info(
                f"Topic discovery: embedding {len(chunks)} chunks | {key[:16]}..."
            )
            embeddings = await loop.run_in_executor(None, embed_chunks, chunks)
            analysis_jobs[key]["phase"] = "modelling"
            logger.info(f"Topic discovery: running BERTopic | {key[:16]}...")
            topics = await loop.run_in_executor(
                None, run_topic_model, chunks, embeddings
            )

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

    key = _job_key(x_session_id, "inconsistencies")
    analysis_jobs[key] = {
        "session_id": x_session_id,
        "status": "discovering",
        "phase": None,
        "topics": [],
        "chunks": [],
        "created_at": time.time(),
        "error": None,
    }

    # Fast path: unmodified demo session — serve pre-computed topics instantly.
    if _session_matches_demo(x_session_id) and _load_demo_topics_into_job(key):
        logger.info(
            f"Inconsistencies analysis: demo cache hit for session {x_session_id[:8]}... "
            f"({len(analysis_jobs[key]['topics'])} topics loaded instantly)"
        )
        return {"status": "started"}

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
    return {
        "status": job["status"],
        "phase": job.get("phase"),
        "chunk_count": job.get("chunk_count"),
        "topics": topics_out,
        "error": job["error"],
        "url_map": _get_url_map(x_session_id),
    }


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


# ---------------------------------------------------------------------------
# Compliance guidelines endpoints
# ---------------------------------------------------------------------------


class ComplianceGuidelinesRequest(BaseModel):
    guidelines: str


@app.get("/api/compliance/guidelines")
@limiter.limit("30/minute")
async def get_compliance_guidelines(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Return the compliance guidelines for this session, or 404 if not set."""
    validate_session(x_session_id, request)
    guidelines = _db_get_compliance_guidelines(x_session_id)
    if guidelines is None:
        raise HTTPException(status_code=404, detail="No compliance guidelines set")
    return {"guidelines": guidelines}


@app.post("/api/compliance/guidelines")
@limiter.limit("20/minute")
async def set_compliance_guidelines(
    request: Request,
    body: ComplianceGuidelinesRequest,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Save compliance guidelines and clear any existing compliance job."""
    validate_session(x_session_id, request)

    MAX_GUIDELINES_LENGTH = 50_000

    stripped = body.guidelines.strip()
    if not stripped:
        raise HTTPException(status_code=400, detail="Guidelines must not be empty")
    if len(stripped) > MAX_GUIDELINES_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Guidelines must not exceed {MAX_GUIDELINES_LENGTH:,} characters",
        )

    _db_set_compliance_guidelines(x_session_id, stripped)
    analysis_jobs.pop(_job_key(x_session_id, "compliance"), None)
    logger.info(f"Compliance guidelines updated for session {x_session_id[:8]}...")
    return {"message": "Guidelines saved", "guidelines": stripped}


# ---------------------------------------------------------------------------
# Guideline presets endpoints (served from the guidelines/ directory)
# ---------------------------------------------------------------------------

_ALLOWED_GUIDELINE_EXTENSIONS = {".txt", ".md"}


@app.get("/api/guidelines")
@limiter.limit("60/minute")
async def list_guidelines(request: Request) -> dict:
    """List available guideline preset files from the guidelines/ directory."""
    if not GUIDELINES_DIR.exists():
        return {"guidelines": []}
    presets = []
    for path in sorted(GUIDELINES_DIR.iterdir()):
        if path.is_file() and path.suffix.lower() in _ALLOWED_GUIDELINE_EXTENSIONS:
            label = path.stem.replace("-", " ").replace("_", " ").title()
            presets.append({"filename": path.name, "label": label})
    return {"guidelines": presets}


@app.get("/api/guidelines/{filename}")
@limiter.limit("60/minute")
async def get_guideline(request: Request, filename: str) -> Response:
    """Return the content of a specific guideline preset file."""
    safe_filename = sanitize_filename(filename)
    if not safe_filename or safe_filename != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = GUIDELINES_DIR / safe_filename
    # Prevent path traversal
    try:
        path.resolve().relative_to(GUIDELINES_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid filename")

    if path.suffix.lower() not in _ALLOWED_GUIDELINE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="File type not supported")

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Guideline not found")

    content = path.read_text(encoding="utf-8")
    return Response(content=content, media_type="text/plain; charset=utf-8")


# ---------------------------------------------------------------------------
# Compliance analysis endpoints
# ---------------------------------------------------------------------------


def _get_compliance_ready_job(session_id: str) -> dict:
    """Return the compliance job if pages are ready, else raise."""
    job = analysis_jobs.get(_job_key(session_id, "compliance"))
    if job is None:
        raise HTTPException(status_code=404, detail="Compliance job not found")
    if job["status"] != "pages_ready":
        raise HTTPException(status_code=400, detail="Pages not yet ready")
    return job


@app.post("/api/analysis/compliance")
@limiter.limit("5/minute")
async def start_compliance_analysis(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Create a compliance analysis job listing all pages in the collection."""
    validate_session(x_session_id, request)

    session = _db_get_session(x_session_id)
    if not session or not session["finalised"]:
        raise HTTPException(
            status_code=400, detail="Collection must be finalised before analysis"
        )

    guidelines = _db_get_compliance_guidelines(x_session_id)
    if not guidelines:
        raise HTTPException(
            status_code=400, detail="Set compliance guidelines before starting analysis"
        )

    session_dir = DATA_DIR / x_session_id
    pages = []
    for idx, md_file in enumerate(sorted(session_dir.glob("*.md"))):
        metadata_path = session_dir / (md_file.name + ".metadata")
        url = None
        if metadata_path.exists():
            try:
                meta = json.loads(metadata_path.read_text(encoding="utf-8"))
                url = meta.get("url")
            except Exception:  # nosec B110
                pass
        pages.append(
            {
                "id": idx,
                "filename": md_file.name,
                "url": url,
                "check_status": None,
                "result": None,
                "error": None,
            }
        )

    if not pages:
        raise HTTPException(status_code=400, detail="No pages found in collection")

    analysis_jobs[_job_key(x_session_id, "compliance")] = {
        "session_id": x_session_id,
        "status": "pages_ready",
        "pages": pages,
        "created_at": time.time(),
        "error": None,
    }

    logger.info(
        f"Compliance analysis started for session {x_session_id[:8]}...: {len(pages)} page(s)"
    )
    return {"status": "started", "page_count": len(pages)}


@app.get("/api/analysis/compliance")
@limiter.limit("30/minute")
async def get_compliance_analysis(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Poll the status and results of the compliance analysis."""
    validate_session(x_session_id, request)

    job = analysis_jobs.get(_job_key(x_session_id, "compliance"))
    if job is None:
        raise HTTPException(status_code=404, detail="No compliance job found")

    return {"status": job["status"], "pages": job["pages"], "error": job["error"]}


class BatchPageCheckRequest(BaseModel):
    page_ids: List[int]


@app.post("/api/analysis/compliance/pages/check-all")
@limiter.limit("5/minute")
async def check_all_pages(
    request: Request,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Trigger compliance checks for all unchecked or errored pages."""
    validate_session(x_session_id, request)
    job = _get_compliance_ready_job(x_session_id)

    started = []
    for page in job["pages"]:
        if page.get("check_status") in (None, "error"):
            page["check_status"] = "checking"
            asyncio.create_task(_run_page_check(x_session_id, page["id"]))
            started.append(page["id"])

    logger.info(
        f"Compliance check-all for session {x_session_id[:8]}...: {len(started)} page(s)"
    )
    return {"message": f"Started {len(started)} checks", "started": started}


@app.post("/api/analysis/compliance/pages/check-batch")
@limiter.limit("10/minute")
async def check_pages_batch(
    request: Request,
    body: BatchPageCheckRequest,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Trigger parallel compliance checks for a batch of pages."""
    validate_session(x_session_id, request)
    job = _get_compliance_ready_job(x_session_id)

    if len(body.page_ids) > 25:
        raise HTTPException(status_code=400, detail="Maximum 25 pages per batch")

    started = []
    for page_id in body.page_ids:
        page = next((p for p in job["pages"] if p["id"] == page_id), None)
        if page is None or page.get("check_status") in ("checking", "complete"):
            continue
        page["check_status"] = "checking"
        asyncio.create_task(_run_page_check(x_session_id, page_id))
        started.append(page_id)

    logger.info(
        f"Compliance batch check for session {x_session_id[:8]}...: {len(started)} page(s)"
    )
    return {"message": f"Started {len(started)} checks", "started": started}


@app.post("/api/analysis/compliance/pages/{page_id}/check")
@limiter.limit("20/minute")
async def check_page(
    request: Request,
    page_id: int,
    x_session_id: str = Header(..., alias="X-Session-ID"),
) -> dict:
    """Trigger a compliance check for a specific page."""
    validate_session(x_session_id, request)
    job = _get_compliance_ready_job(x_session_id)

    page = next((p for p in job["pages"] if p["id"] == page_id), None)
    if page is None:
        raise HTTPException(status_code=404, detail="Page not found")
    if page.get("check_status") in ("checking", "complete"):
        return {"message": "Already checking or complete"}

    page["check_status"] = "checking"
    asyncio.create_task(_run_page_check(x_session_id, page_id))
    logger.info(
        f"Compliance page check triggered: session {x_session_id[:8]}... page {page_id}"
    )
    return {"message": "Check started"}


async def _run_page_check(session_id: str, page_id: int) -> None:
    """Background task: run LLM compliance check for a single page."""
    key = _job_key(session_id, "compliance")
    try:
        job = analysis_jobs[key]
        page = next(p for p in job["pages"] if p["id"] == page_id)
        session_dir = DATA_DIR / session_id
        content = (session_dir / page["filename"]).read_text(encoding="utf-8")
        guidelines = _db_get_compliance_guidelines(session_id)
        if not guidelines:
            raise ValueError("Compliance guidelines have been cleared")
        async with _get_llm_semaphore():
            result: ComplianceResult = await check_page_compliance(
                page["filename"], content, guidelines
            )
        page["check_status"] = "complete"
        page["result"] = result.model_dump()
        logger.info(
            f"Compliance check complete: {key[:16]}... page {page_id}: "
            f"{'issues found' if result.has_issues else 'no issues'}"
        )
    except Exception as exc:
        logger.exception(f"Compliance check failed: {key[:16]}... page {page_id}")
        try:
            for p in analysis_jobs[key]["pages"]:
                if p["id"] == page_id:
                    p["check_status"] = "error"
                    p["error"] = str(exc)
                    break
        except KeyError:
            pass

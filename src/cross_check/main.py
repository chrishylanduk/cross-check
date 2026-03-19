"""FastAPI backend for Cross-check."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="Cross-check API",
    description="AI-assisted content audit tool",
    version="0.0.1",
)

# Configure CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # Next.js default port
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


@app.get("/api/audit")
async def get_audit_info():
    """Placeholder endpoint for content audit functionality."""
    return {
        "message": "Content audit endpoint - coming soon",
        "features": [
            "Consistency checking",
            "Clarity analysis",
            "Compliance verification",
            "Completeness assessment",
        ],
    }

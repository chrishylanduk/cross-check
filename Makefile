.PHONY: help install dev dev-frontend dev-backend dev-phoenix test test-backend test-frontend clean

help:
	@echo "Cross-check - AI-assisted content audit tool"
	@echo ""
	@echo "Available commands:"
	@echo "  make install        - Install all dependencies (frontend + backend)"
	@echo "  make dev            - Run both frontend and backend (in separate terminals)"
	@echo "  make dev-frontend   - Run Next.js frontend on http://localhost:3000"
	@echo "  make dev-backend    - Run FastAPI backend on http://localhost:8000"
	@echo "  make dev-phoenix    - Run Arize Phoenix UI on http://localhost:6006"
	@echo "  make test           - Run all tests (backend + frontend)"
	@echo "  make test-backend   - Run Python tests only"
	@echo "  make test-frontend  - Run TypeScript/frontend tests only"
	@echo "  make clean          - Clean build artefacts"

install:
	@echo "Installing Python dependencies..."
	uv sync
	@echo "Installing frontend dependencies..."
	cd frontend && npm install
	@echo "✓ All dependencies installed"

test-backend:
	uv run pytest tests/

test-frontend:
	cd frontend && npm test

test: test-backend test-frontend

dev-frontend:
	cd frontend && npm run dev

dev-backend:
	uv run uvicorn src.cross_check.main:app --reload --port 8000

dev-phoenix:
	uv run phoenix serve

dev:
	@echo "Run these commands in separate terminals:"
	@echo "  Terminal 1: make dev-backend"
	@echo "  Terminal 2: make dev-frontend"
	@echo "  Terminal 3: make dev-phoenix"

clean:
	rm -rf frontend/node_modules frontend/.next
	rm -rf .venv
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

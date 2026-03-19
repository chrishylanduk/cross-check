.PHONY: help install dev dev-frontend dev-backend clean

help:
	@echo "Cross-check - AI-assisted content audit tool"
	@echo ""
	@echo "Available commands:"
	@echo "  make install      - Install all dependencies (frontend + backend)"
	@echo "  make dev          - Run both frontend and backend (in separate terminals)"
	@echo "  make dev-frontend - Run Next.js frontend on http://localhost:3000"
	@echo "  make dev-backend  - Run FastAPI backend on http://localhost:8000"
	@echo "  make clean        - Clean build artifacts"

install:
	@echo "Installing Python dependencies..."
	uv sync
	@echo "Installing frontend dependencies..."
	cd frontend && npm install
	@echo "✓ All dependencies installed"

dev-frontend:
	cd frontend && npm run dev

dev-backend:
	uv run uvicorn src.cross_check.main:app --reload --port 8000

dev:
	@echo "Run these commands in separate terminals:"
	@echo "  Terminal 1: make dev-backend"
	@echo "  Terminal 2: make dev-frontend"

clean:
	rm -rf frontend/node_modules frontend/.next
	rm -rf .venv
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

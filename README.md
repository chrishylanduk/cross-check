# Cross-check

Automatically recommend improvements to a large collection of written content (e.g. a website or intranet) to improve its consistency, clarity, compliance and completeness. Save hours or days compared to a manual content audit.

## Tech Stack

- **Frontend**: Next.js (Pages Router) with GOV.UK Frontend styling
- **Backend**: FastAPI (Python)
- **Styling**: govuk-frontend components (without crown/Transport font)

## Getting started

### Requirements

- Python 3.13+ installed
- Node.js 18+ and npm installed
- a `.env` file with the [required environment variables](#required-environment-variables)

### Installing dependencies

Install both frontend and backend dependencies:

```shell
make install
```

Or install them separately:

```shell
# Python backend dependencies
uv sync

# Frontend dependencies
cd frontend && npm install
```

## Running the development servers

You need to run both the frontend and backend servers. Open two terminal windows:

**Terminal 1 - Backend (FastAPI)**:
```shell
make dev-backend
```
The API will be available at http://localhost:8000

**Terminal 2 - Frontend (Next.js)**:
```shell
make dev-frontend
```
The app will be available at http://localhost:3000

### Available make commands

Run `make help` to see all available commands:
- `make install` - Install all dependencies
- `make dev-frontend` - Run Next.js frontend
- `make dev-backend` - Run FastAPI backend
- `make clean` - Clean build artifacts

## Self-hosting with your own AI provider

Cross-check is designed to be self-hosted with any LLM provider. Set `ANALYSIS_MODEL` to a [pydantic-ai model string](https://ai.pydantic.dev/models/) and the matching API key:

| Provider | `ANALYSIS_MODEL` example | API key variable |
|----------|--------------------------|-----------------|
| OpenAI | `openai:gpt-4.1-mini` | `OPENAI_API_KEY` |
| Anthropic | `anthropic:claude-3-5-haiku-latest` | `ANTHROPIC_API_KEY` |
| Google | `google-gla:gemini-2.0-flash` | `GOOGLE_API_KEY` |
| AWS Bedrock | `bedrock:us.amazon.nova-lite-v1:0` | AWS credential chain |
| Groq | `groq:llama-3.3-70b-versatile` | `GROQ_API_KEY` |

### Using a local or OpenAI-compatible endpoint

For Ollama, vLLM, LM Studio, or any OpenAI-compatible server, set `OPENAI_BASE_URL` instead of using a provider prefix:

```shell
OPENAI_BASE_URL=http://localhost:11434/v1
ANALYSIS_MODEL=llama3.2
OPENAI_API_KEY=ollama   # required by the client but not validated locally
```

### Customising the data usage notice

The "Before you upload content" notice tells users which AI provider will process their data. Override the defaults to match your deployment:

| Variable | Description | Default |
|----------|-------------|---------|
| `AI_PROVIDER_NAME` | Provider name shown to users | `OpenAI` |
| `AI_PRIVACY_POLICY_URL` | Link to the provider's privacy policy | OpenAI EU privacy policy |

Set `AI_PRIVACY_POLICY_URL` to an empty string (or leave it unset) when using a local model with no external privacy policy — the notice will show the provider name as plain text without a link.

These are read at **runtime** by the frontend server, so changing them only requires a container restart — no rebuild needed.

## Required environment variables

To run this project, you need a `.env` file with environment variables.
Copy `.env.example` to `.env` and customise as needed:

```shell
cp .env.example .env
```

Key variables:

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `PROTOTYPE_PASSWORD` | Password to access the app | - | Yes* |
| `DISABLE_PROTOTYPE_PASSWORD` | Set to `true` to disable password | - | No |
| `CORS_ORIGINS` | Comma-separated allowed origins | `http://localhost:3000` | No |
| `NEXT_PUBLIC_API_BASE` | Backend API URL | `http://localhost:8000` | No |
| `PORT` | Backend server port | `8000` | No |

*Either `PROTOTYPE_PASSWORD` must be set, or `DISABLE_PROTOTYPE_PASSWORD=true`

The `.env` file is ignored by git for security.

## Docker Deployment

This project uses hardened Chainguard base images for security.

### Build and run with Docker Compose

```shell
docker compose up --build
```

The application will be available at:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000

### Platform Deployment (Railway, etc.)

When deploying to platforms like Railway:

1. **Backend service**:
   - Set `CORS_ORIGINS` to your frontend URL(s)
   - Platform will set `PORT` automatically
   - Set `PERSISTENT_STORAGE_WARNING_DISABLED=true`

2. **Frontend service**:
   - Set `NEXT_PUBLIC_API_BASE` to your backend service URL

3. **Storage**:
   - Files are stored on ephemeral disk (free on Railway, 100GB limit)
   - Sessions expire after 1 hour, files are cleaned up automatically
   - Orphaned files (from server restarts) are cleaned up on startup
   - No persistent volumes needed for this use case

### Build individual containers

```shell
# Backend
docker build -f Dockerfile.backend -t cross-check-backend .

# Frontend
docker build -f frontend/Dockerfile -t cross-check-frontend ./frontend
```


### Requirements

- Python 3.13+ installed
- a `.env` file with the [required environment variables](#required-environment-variables)

To install the contributing requirements, open your terminal and enter:
```shell
uv sync --group dev
```

## Pre-commit hooks

This project uses pre-commit hooks for code quality checks. After installing the dev dependencies, set up pre-commit:

```shell
uv run pre-commit install
```

This will automatically run code formatting, linting, and security checks before each commit. To run the checks manually:

```shell
uv run pre-commit run --all-files
```

## Acknowledgements

This project structure is based on the `chris-hyland-copier` template, which in turn is derived from `govcookiecutter`.

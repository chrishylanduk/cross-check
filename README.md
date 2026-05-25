# Cross-check

> **This is a personal learning and development project, and is a very early prototype.** Much of this code is AI-generated. It should not be used for production purposes or with sensitive data without independent review.

Automatically recommend improvements to a large collection of written content (e.g. a website or intranet) to improve its consistency, clarity, compliance and completeness. Save hours or days compared to a manual content audit.

## Tech Stack

- **Frontend**: Next.js (Pages Router) with GOV.UK Frontend styling
- **Backend**: FastAPI (Python)
- **Styling**: govuk-frontend components (without crown/Transport font)

## Getting started

### Requirements

- Python 3.13+
- Node.js 18+ and npm
- A `.env` file — copy `.env.example` and fill in your values:

```shell
cp .env.example .env
```

### Installing dependencies

```shell
make install
```

### Running the development servers

Open two terminal windows:

**Terminal 1 — Backend:**
```shell
make dev-backend
```
API available at http://localhost:8000

**Terminal 2 — Frontend:**
```shell
make dev-frontend
```
App available at http://localhost:3000

Run `make help` to see all available commands.

---

## Authentication

The app requires exactly one authentication method to be configured. It will refuse to start if neither is set, or if both are set simultaneously.

### Option A — Prototype password (simple, default)

A single shared password that gates access to the app. Set in `.env`:

```shell
PROTOTYPE_PASSWORD=your-secure-password-here   # must be more than 6 characters
```

To disable password protection entirely (e.g. behind a VPN):

```shell
DISABLE_PROTOTYPE_PASSWORD=true
```

### Option B — Clerk email auth (recommended for shared deployments)

Users sign in with their email address and receive a one-time verification code — no password needed. Suitable for demos and shared environments where you want individual accountability and domain-based access control.

#### 1. Create a Clerk account

Sign up at [dashboard.clerk.com](https://dashboard.clerk.com) and create an application.

#### 2. Configure sign-in method

In the Clerk dashboard:

- **User & Authentication → Email, Phone, Username**: enable **Email verification code** only. Disable passwords and all social providers.
- **User & Authentication → Personal information**: uncheck everything except email (no name, phone, etc.). This makes sign-up as frictionless as sign-in.

#### 3. Add email to the session token

By default, Clerk JWTs do not include the user's email address. Cross-check needs it for domain allowlisting and enforces access at the server before any page is served.

In **Sessions → Customize session token**, add:

```json
{
  "email": "{{user.primary_email_address}}"
}
```

> **Important:** If `ALLOWED_EMAIL_DOMAINS` is set and this step is skipped, all users will be denied access (fail-closed behaviour).

#### 4. Set session lifetime

In **Sessions → Session lifetime**, set:
- **Session duration**: 8 hours (covers a working day; users re-verify via email code overnight)
- **Inactivity timeout**: 2 hours (optional, signs out idle sessions)

The JWT token lifetime can stay at the default (60 seconds) — the SDK refreshes it silently.

#### 5. Set environment variables

Add to `.env` (remove `PROTOTYPE_PASSWORD`):

```shell
CLERK_SECRET_KEY=sk_live_...
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_live_...
```

#### 6. Restrict access by email domain (optional)

Set `ALLOWED_EMAIL_DOMAINS` to a comma-separated list of permitted domains:

```shell
ALLOWED_EMAIL_DOMAINS=example.org,@example.com
```

- `example.org` — allows `user@example.org` and `user@sub.example.org` (exact + all subdomains)
- `@example.com` — allows `user@example.com` only (exact match, no subdomains)

Leave unset to allow all verified Clerk users.

Domain enforcement happens server-side in Next.js middleware before any content is served. Users from non-allowed domains are redirected to an access-denied page immediately after sign-in.

---

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

For Ollama, vLLM, LM Studio, or any OpenAI-compatible server:

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

Set `AI_PRIVACY_POLICY_URL` to an empty string when using a local model — the notice will show the provider name as plain text without a link.

These are read at runtime by the frontend server — changing them only requires a restart, no rebuild needed.

---

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
   - If using Clerk, set `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` here too

3. **Storage**:
   - Files are stored on ephemeral disk (free on Railway, 100GB limit)
   - Sessions expire after 1 hour, files are cleaned up automatically
   - No persistent volumes needed for this use case

### Build individual containers

```shell
# Backend
docker build -f Dockerfile.backend -t cross-check-backend .

# Frontend
docker build -f frontend/Dockerfile -t cross-check-frontend ./frontend
```

---

## Contributing

To install development dependencies:

```shell
uv sync --group dev
```

### Pre-commit hooks

```shell
uv run pre-commit install
```

Runs formatting, linting, and security checks before each commit. To run manually:

```shell
uv run pre-commit run --all-files
```

## Acknowledgements

This project structure is based on the `chris-hyland-copier` template, which in turn is derived from `govcookiecutter`.

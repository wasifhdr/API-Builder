# API Builder

Turn browsing into an API: record a session in a real Chromium window, mark the data you want
extracted, and the app republishes the workflow as a parameterized JSON HTTP API with an
auto-generated OpenAPI spec, Google login, tiered subscriptions, and manual bKash payment
verification.

Full design: [docs/BLUEPRINT.md](docs/BLUEPRINT.md). Build order: [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md).

This is a single-machine app by design: everything (Postgres/Redis in Docker, FastAPI, the
worker, Vite, and optionally a local LLM) runs on one Windows box with an NVIDIA GPU. It's not
meant to be deployed to multiple machines.

## Prerequisites

- **Windows 11**, since the recorder needs a real, visible Chromium window on the desktop.
- **Docker Desktop** (Postgres + Redis).
- **Python 3.12** and [**uv**](https://docs.astral.sh/uv/).
- **Node.js 20+** and npm.
- A **Google Cloud OAuth 2.0 Client ID** (Web application) with authorized redirect URI
  `http://localhost:3000/api/auth/callback/google`.
- Optional: an **NVIDIA GPU** (6 GB+ VRAM) if you want real LLM-enriched OpenAPI descriptions
  instead of the deterministic template fallback (the app works fully without this — see
  [models/README.md](models/README.md) and §6 of the Blueprint).

## Setup from a clean clone

```powershell
git clone <this-repo> "API Builder V3"
cd "API Builder V3"
```

### 1. Environment

```powershell
Copy-Item .env.example .env
```

Edit `.env` and fill in:

- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — from your Google Cloud OAuth client.
- `ADMIN_EMAILS` — comma-separated emails that get auto-promoted to `role=admin` on first login.
- `AUTH_STATE_FERNET_KEY` — generate one:
  ```powershell
  cd backend
  uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
  cd ..
  ```
- `BKASH_RECEIVE_MSISDN` / `SMS_WEBHOOK_TOKEN` — only matter once you're testing billing; any
  placeholder value works otherwise.

Everything else in `.env.example` already has a sensible default for local dev (including the
remapped Postgres/Redis host ports — see the comment in `docker-compose.yml` if you're curious
why they're not 5432/6379).

### 2. Infrastructure

```powershell
docker compose up -d
```

### 3. Backend

```powershell
cd backend
uv sync
uv run playwright install chromium
uv run alembic upgrade head
cd ..
```

### 4. Frontend

```powershell
cd frontend
npm install
cd ..
```

### 5. (Optional) Local LLM

Only needed for LLM-enriched OpenAPI descriptions — the app is fully functional without it
(`LLM_ENABLED=false` falls back to a deterministic template). Follow
[models/README.md](models/README.md) to get `llama/llama-server.exe` and a `.gguf` model in place,
then run `scripts\run-llama.ps1` whenever you want it live.

### 6. Run it

```powershell
scripts\dev.ps1
```

This starts uvicorn (reload), the worker, and the Vite dev server, each in its own window.
Visit **http://localhost:3000**, sign in with Google, and you're in.

## Everyday commands

```powershell
docker compose up -d                 # postgres + redis
scripts\run-llama.ps1                # optional local LLM
scripts\dev.ps1                      # uvicorn + worker + vite
cd backend; uv run pytest            # backend test suite
cd backend; uv run ruff check app    # lint
cd backend; uv run alembic upgrade head
scripts\e2e.ps1                      # full end-to-end smoke test (see below)
```

## End-to-end smoke test

`scripts\e2e.ps1` drives the *real* running system — no mocks — through the entire pipeline: it
speaks the actual WebSocket recording protocol to record a workflow against a local static
fixture site, publishes it, executes it through the public `/v1/run/{slug}` endpoint, and asserts
the extracted JSON matches the page exactly. It creates and cleans up its own throwaway test user.

Requires Postgres/Redis, the backend, and the worker to already be running (`scripts\dev.ps1`).
A real headful Chromium window will briefly open — that's expected, it's the same recorder every
real recording session uses.

```powershell
scripts\e2e.ps1
```

## Project layout

```
├─ docs/            BLUEPRINT.md (design contract), IMPLEMENTATION_PLAN.md (build order)
├─ scripts/         dev.ps1, run-llama.ps1, e2e.ps1
├─ models/          .gguf model file (gitignored) — see models/README.md
├─ llama/           llama-server.exe (gitignored)
├─ data/            profiles/ (recorder browser profiles), failures/ (replay failure artifacts)
├─ backend/         FastAPI app + worker (app/), tests, alembic migrations
└─ frontend/        React + Vite + Tailwind v4
```

`CLAUDE.md` documents the hard architectural rules (Playwright only in the worker, never in
FastAPI or Docker; FastAPI↔worker talk only via Redis; async everywhere; etc.) for anyone
extending this codebase.

# API Builder

Web app that records a user's browser session (Playwright), lets them mark data to extract, and
republishes the workflow as a parameterized JSON HTTP API with an auto-generated OpenAPI spec
(local llama.cpp), Google OAuth, tiered subscriptions, and manual bKash payment verification.
Single-machine deployment: Windows 11 laptop, RTX 4050 6 GB.

## Read first

- **[docs/BLUEPRINT.md](docs/BLUEPRINT.md)** — the design contract: architecture, DB schema,
  recording/replay pipeline, LLM integration, billing. Read the sections referenced by the phase
  you're implementing. When in doubt, the Blueprint wins.
- **[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)** — phased build order with
  acceptance criteria. Work one phase per session, in order. Commit per phase.
- **[docs/DESIGN.md](docs/DESIGN.md)** — frontend visual design system ("Warm Editorial"): theme
  tokens, component recipes, per-page styling guide. Read before any frontend/UI work.

## Stack & processes

React+Vite+Tailwind v4 (:3000, proxies `/api` → :8000) · FastAPI (:8000) · custom asyncio worker
(`python -m app.workers.main`) owning ALL Playwright + LLM jobs · Postgres 16 + Redis 7 in Docker ·
llama-server native CUDA (:8080).

## Commands

```powershell
docker compose up -d                 # postgres + redis
scripts\run-llama.ps1                # optional; app must work without it (LLM_ENABLED=false)
scripts\dev.ps1                      # uvicorn + worker + vite
cd backend; uv run pytest            # tests
cd backend; uv run ruff check app    # lint
cd backend; uv run alembic upgrade head
```

## Hard rules / gotchas

- **Playwright runs ONLY in the worker process** — never in FastAPI, never in Docker (headful
  recorder needs the Windows desktop; worker guards the Proactor event-loop policy).
- FastAPI ↔ worker talk **only via Redis** (Streams for jobs, pub/sub for live recording events);
  the WS endpoint is a dumb bridge.
- DB: SQLAlchemy 2.0 typed style, async + **asyncpg**; enums `native_enum=False` storing `.value`;
  JSONB columns are replaced, never mutated in place; money `Numeric(10,2)` BDT; timestamps UTC
  tz-aware; daily quotas use the **Asia/Dhaka** calendar day.
- OAuth redirect `http://localhost:3000/api/auth/callback/google` is served by FastAPI **through
  the Vite proxy** — frontend port must stay 3000, proxy must have `ws: true`.
- Tailwind is **v4**: CSS-first (`@import "tailwindcss";` + `@tailwindcss/vite`), no
  `tailwind.config.js` — don't scaffold v3-style.
- Headless replay browsers always launch with `--disable-gpu` (llama.cpp owns the VRAM);
  LLM job concurrency is 1; spec generation failure must never block publishing (fallback spec).
- Secrets only via `app/config.py` / `.env` (gitignored). Never commit `.env`, `models/`, `data/`.

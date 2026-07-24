# Implementation Plan

Instructions for the implementing agent (Sonnet). Work **one phase per session**, in order — each
phase is a vertical slice that leaves the app runnable. Before coding a phase, read the Blueprint
sections listed under it ([BLUEPRINT.md](BLUEPRINT.md)).

**Definition of done for every phase:** acceptance criteria pass · `ruff check` clean ·
`alembic upgrade head` clean from scratch · `scripts/dev.ps1` boots everything without errors ·
commit with message `Phase N: <summary>`.

Global rules:
- Windows-native Python; never move Playwright into Docker or into the FastAPI process.
- Async everywhere (SQLAlchemy async + asyncpg, redis.asyncio, Playwright async API).
- JSONB columns: replace the whole value, never mutate in place.
- Secrets only via `app/config.py` (pydantic-settings); never hardcode.
- When a contract is ambiguous, the Blueprint wins over convenience.

---

## Phase 0 — Scaffold & infrastructure

**Blueprint:** §1, §13, §14

- [ ] `git init` + `.gitignore` (`.env`, `data/`, `node_modules/`,
      `__pycache__/`, `.venv/`, `frontend/dist/`)
- [ ] `docker-compose.yml` (postgres:16 + redis:7-alpine per §13), `.env.example` (full listing §13)
- [ ] `backend/` with `uv` project (deps list §13), `app/config.py`, `app/main.py` with
      `GET /api/health` → `{status, db, redis}` (actually pings both)
- [ ] `frontend/` Vite + React + TS + Tailwind v4 (`@tailwindcss/vite`; port 3000; proxy per §7 —
      Tailwind v4 is CSS-first: no `tailwind.config.js`, just `@import "tailwindcss";`)
- [ ] `scripts/dev.ps1` — starts uvicorn (reload), worker placeholder, and `npm run dev`, each in
      its own window; prints reminder to run `docker compose up -d`
- [ ] `python -m playwright install chromium`

**Accept:** `docker compose up -d` then `dev.ps1`; browsing to `http://localhost:3000/api/health`
returns ok **through the Vite proxy**.

## Phase 1 — Auth & users

**Blueprint:** §3 (User), §7

- [ ] `User` model; alembic async setup (`alembic init -t async`); first migration
- [ ] OAuth login/callback/logout per §7 (httpx code exchange, `google-auth` ID-token verification,
      `oauth:state` in Redis, Redis sessions, `ab_session` cookie)
- [ ] `deps.current_user` (cookie → Redis → DB), `deps.require_admin`; admin allowlist promotion
- [ ] `GET /api/me`, `PATCH /api/me/settings` (validate known keys only)
- [ ] Frontend: `useSession` hook, login button on Landing, Dashboard shell + Settings page with
      the saved-logins toggle (copy explains what it does per §4.9)

**Accept:** full Google login round-trip in the browser lands on `/dashboard`; `/api/me` shows the
profile; your email gets `role=admin`; logout clears the session.

## Phase 2 — Full schema, plans & quotas

**Blueprint:** §3 (all), §8

- [ ] All remaining models + migration (verify partial unique index on subscriptions in the DDL)
- [ ] `services/plans.py` (tier config map: limits, prices from env), effective-tier resolution
      dependency, `services/quota.py` (Dhaka-day key, INCR/DECR-on-reject, PG fallback count)
- [ ] `GET /api/billing/plans`; Dashboard quota meter + tier badge
- [ ] Tests: quota edges (limit hit, midnight rollover via injected clock, Redis-flush fallback),
      tier resolution (no sub / active / expired)

**Accept:** `pytest` green; fresh-DB `alembic upgrade head` clean; quota meter renders live values.

## Phase 3 — Recording pipeline (steps only)

**Blueprint:** §1.3, §2 (#1 #2), §4.1–4.5, §5.4

- [ ] Worker skeleton exactly per §5.4; `handlers.record_session` (launch → inject → record → save)
- [ ] `recorder/injected.js`: record mode only (click / fill / press / navigation), selector
      candidates per §4.3; `recorder/selectors.py` mirrors the ranking for tests
- [ ] Redis pub/sub session plumbing + heartbeat; FastAPI WS bridge (`api/ws.py`) with ownership
      check; `POST /api/recordings` behind `require_creation_quota`
- [ ] Save path: worker writes steps → workflow `status=draft`, publishes `saved`
- [ ] Frontend Recorder page: start form (name + URL), live step list, undo, save, status banner,
      bring-to-front; `useRecorder` WS hook with reconnect
- [ ] Idle (10 min) and hard (30 min) timeouts; cancel command

**Accept:** record "open rokomari.com → search a term → Enter" from the UI; steps appear live;
saved workflow row contains goto/fill/press steps with selector candidate arrays; quota decremented;
killing the worker mid-session surfaces `died` in the panel within ~20 s.

## Phase 4 — Element picking, extraction, parameters

**Blueprint:** §4.6–4.9

- [ ] Pick mode in `injected.js` (overlay, capture-phase interception, similar-count) + mode
      switching (§4.4)
- [ ] Extraction builder UI (root + fields table, take/transform dropdowns) driven by pick results;
      `test_extraction` round-trip showing sample JSON
- [ ] `recorder/extraction.py` (single `page.evaluate` pass + Python transforms),
      `schema_infer.py` (genson + §4.8 post-processing)
- [ ] Parameter marking (`mark_param` end-to-end, panel edit of name/type/required/example)
- [ ] Saved-logins mode: persistent profile dir, `storage_state` capture, Fernet encrypt/decrypt
      helpers in `core/security.py`; workflow saved as `ready` when it has extraction
- [ ] WorkflowEditor page for post-hoc edits of params/extraction/name
- [ ] Tests: selector ranking; extraction against `tests/fixtures/site/` served by a local HTTP
      server fixture

**Accept:** end-to-end: record a search on a real site, pick a results list, see a sample JSON of
≥ 5 items with correct field names/types, mark the search term as `query`, save → workflow `ready`
with `output_schema` + `sample_output` + encrypted auth state populated.

## Phase 5 — Publish & execute (the product moment)

**Blueprint:** §5 (all), §3 (CustomApi, ApiKey, ApiExecution)

- [ ] `POST /api/workflows/{id}/publish` → `services/publish.py` (snapshot, slug, CustomApi row,
      enqueue `jobs:llm`)
- [ ] `recorder/replay.py` per §5.2 (candidate fallback, param substitution, `--disable-gpu`,
      storage_state injection, failure artifacts); `handlers.execute_api` (result SET + PUBLISH,
      execution row lifecycle)
- [ ] Public app mount (`/v1`, CORS `*`): `GET /v1/run/{slug}` (sync-wait per §5.1, 202 path,
      `Prefer: respond-async`), `GET /v1/executions/{id}`; API-key auth (hash lookup), grant check,
      param coercion (422), per-key rate limit, result cache
- [ ] Key management UI + endpoints (plaintext shown once); ApiDetail page (publish flow,
      executions log with failure artifact links)
- [ ] Tests: replay against fixture site (happy path, param substitution, selector-fallback,
      failure artifact); DSL coercion

**Accept:**
`curl -H "X-API-Key: ab_..." "http://localhost:8000/v1/run/<slug>?query=python"` returns extracted
JSON; second call with `cache_ttl>0` returns `meta.cached=true`; a bogus selector edit produces a
502 with a screenshot in `data/failures/`.

## Phase 6 — OpenAPI generation & docs page

**Blueprint:** §6 (all)

- [ ] `llm/spec_builder.py` (deterministic 3.1 skeleton) — **build and test this before any LLM code**
- [ ] `llm/client.py`, `enrich.py`, `prompts.py` per §6.2–6.3 (dynamic enrichment schema);
      `handlers.generate_spec` with validate → retry-once → fallback (§6.4); `LLM_ENABLED` flag
- [ ] `GET /v1/apis/{slug}/openapi.json`; `regenerate-spec` endpoint + button
- [ ] ApiDocs page (Scalar CDN embed); spec status chip on ApiDetail
- [ ] Tests: spec_builder output validates for single- and list-mode workflows; enrichment merge
      with a mocked client; fallback path with client raising

**Accept:** publishing generates a spec that passes `openapi-spec-validator`; docs page renders and
"try it" works against `/v1/run/{slug}`; with `LLM_ENABLED=false`, publishing still yields a
valid (template-prose) spec.

## Phase 7 — Billing (bKash) & subscription lifecycle

**Blueprint:** §8, §9

- [ ] Intents + submit-trx endpoints (24 h expiry), Billing page with plan cards, send-money
      instructions, TrxID form, status polling
- [ ] Webhook endpoint (token auth, dedupe, regex parse, raw always stored)
- [ ] `services/sms_matcher.py` per §9.3 (FOR UPDATE, order-independent, apply-effects for
      subscriptions), wired to both triggers
- [ ] Admin pages: transactions (verify/reject with note → same apply-effects), SMS feed, users
      (tier override)
- [ ] Periodic sweep: expire subscriptions + stale intents; tier gates now enforced everywhere
      (§8 — quota numbers, sharing gates)
- [ ] Tests: matcher (exact, overpaid, underpaid→flag, duplicate TrxID, SMS-before-submit and
      submit-before-SMS), parser against 3–4 real bKash SMS wordings, webhook dedupe

**Accept:** simulated webhook POST (curl) auto-verifies a submitted matching intent and the user's
tier flips to Pro (quota 50 visible); manual admin verify works with no webhook involved; expiry
sweep downgrades a back-dated subscription.

## Phase 8 — Sharing & monetization

**Blueprint:** §10

- [ ] Invites CRUD + `/invite/{token}` accept flow (free → instant grant; priced → payment intent
      → grant on verify)
- [ ] Grants management UI (owner: list/revoke; consumer: "shared with me" on Dashboard)
- [ ] Call-time enforcement per §10 (incl. owner-tier-lapsed 403) — add tests
- [ ] Docs access for grantees; visibility/pricing controls on ApiDetail gated by tier

**Accept:** with a second Google account: accept an invite, create a key, call the API, open its
docs; for a priced API the grant appears only after (admin-)verified payment; owner downgraded to
free → grantee calls return 403; renewal restores access with no data changes.

## Phase 9 — Hardening (stretch)

- [ ] E2E smoke script (`scripts/e2e.ps1`): fixture site → record via WS protocol directly →
      publish → execute → assert JSON
- [ ] Result-size truncation, execution-log retention (keep last 200/API), failure-artifact GC
- [ ] Nicer errors: recorder warnings for iframes/popups (§15), 429 payloads with reset time
- [ ] README.md: setup from clean clone (docker, uv, playwright install, .env)

---

### Testing conventions

- `pytest-asyncio` (mode=auto). Unit-test services against real Postgres/Redis from compose
  (test database `apibuilder_test`, truncate between tests) — skip elaborate mocking.
- Replay/extraction tests run against `tests/fixtures/site/` (static HTML with a search form +
  result list, including one page whose "primary" selector is missing so candidate fallback is
  exercised) served by a session-scoped local HTTP server fixture. No external-network tests.
- LLM calls are mocked in tests; one optional `@pytest.mark.llm` integration test hits the
  configured hosted LLM.

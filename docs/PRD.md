# API Builder — Product Requirements Document

**Version:** 1.0 · **Status:** describes the shipped v1 (Phases 0–9, T1–T7, W1–W6) plus the
committed roadmap · **Owner:** Wasif Haider (2232829642) · **Context:** CSE226.1 course project, single-machine
deployment.

This PRD is the *product* view: who it's for, what it must do, and how success is judged. The
*engineering* contract lives in [BLUEPRINT.md](BLUEPRINT.md) (architecture, schema, pipelines),
[MONETIZATION_PLAN.md](MONETIZATION_PLAN.md) (wallet & pricing), and
[MULTI_TENANCY_PLAN.md](MULTI_TENANCY_PLAN.md) (accounts & admin). Where this document and those
disagree on a technical contract, **they win**.

---

## 1. Problem

A large amount of useful data sits behind ordinary web UIs that expose no API: university portals,
local marketplaces, government listings, book stores, job boards. Getting that data
programmatically today means one of:

| Option | Why it fails the target user |
|---|---|
| Write a scraper (Playwright/Selenium/BeautifulSoup) | Requires a programmer, a runtime to host it, and ongoing maintenance when selectors drift. |
| Use a commercial scraping SaaS | Priced in USD with card-only billing — effectively unavailable to students and small businesses in Bangladesh. |
| No-code scrapers (browser extensions) | Produce CSV exports, not callable APIs; can't handle a login, a form, or a parameterized query. |
| Copy/paste manually | Doesn't scale and can't be automated. |

The recurring shape of the need is not "download this page once" — it is **"let me call this
multi-step browsing flow, with a parameter, from my own code."** A search on a site is a workflow:
navigate → type a query → submit → read the results. Every one of those steps is trivial for a
human and expensive to automate.

**The insight this product is built on:** the user already knows how to perform the workflow — they
do it in a browser every day. If the system can *watch them do it once* and then *replay it on
demand with different inputs*, no code is required at any point.

---

## 2. Product summary

**API Builder turns manual browsing into a callable JSON API.**

The user records a real browser session, points at the data they want, and publishes the result as
an HTTP endpoint with an auto-generated OpenAPI spec. The endpoint accepts the parameters the user
marked during recording, replays the browsing flow headlessly, extracts the marked data, and
returns JSON.

```
Record a session  →  Mark parameters & data  →  Publish  →  GET /v1/run/{slug}?query=physics
   (headful             (click on the page,        (slug +      → {"data": [...], "meta": {...}}
    Chromium)            name the fields)           OpenAPI)
```

Around that core sit the things that make it a product rather than a script: accounts, tiered
plans, a prepaid BDT wallet, per-call metering, invite-based sharing with creator earnings, and an
admin console.

### Positioning statement

> For non-programmers and developers who need data from sites that have no API, API Builder is a
> browser-recording tool that publishes reusable, parameterized JSON APIs — unlike scraping
> frameworks, it requires no code, and unlike scraping SaaS, it bills in BDT through bKash.

---

## 3. Goals & non-goals

### 3.1 Product goals

| # | Goal | Success looks like |
|---|---|---|
| G1 | A non-programmer can publish a working API from a real site | Record → publish → first successful `curl` in under 10 minutes, no documentation read |
| G2 | Published APIs are self-describing | Every published API has a valid OpenAPI 3.1 spec and a live "try it" docs page |
| G3 | Replays are reliable enough to depend on | ≥ 90 % success rate on a stable target site over a week of scheduled calls |
| G4 | Authenticated flows work | A workflow behind a login replays without the user re-entering credentials |
| G5 | Money works locally | A user can pay, get access, and (as a creator) earn — entirely in BDT via bKash, with no card |
| G6 | Compute cost is bounded | No user can consume unbounded replay capacity on the single host |

### 3.2 Explicit non-goals (v1)

- **Not** a general-purpose crawler — no site-wide crawling, no infinite pagination, no sitemap
  discovery.
- **Not** an anti-bot circumvention tool. CAPTCHA solving, fingerprint spoofing, and proxy
  rotation are out of scope; blocked sites surface a clear error with a screenshot.
- **Not** multi-tenant infrastructure. "Multi-tenancy" here means role-based access control over
  one shared deployment; there are no organizations or teams.
- **Not** horizontally scalable in v1. One laptop hosts everything; the queue design permits more
  workers later, but no distributed complexity is added now.
- **Not** an in-browser recorder. The recorded browser is a real window on the user's desktop;
  streaming it into the web page (CDP screencast) is a v2 idea.
- **No** refunds/chargebacks flow, **no** email verification, **no** proration on plan changes.
- Legal responsibility for scraping a given site rests with the user; the product shows a
  disclaimer and does not police target sites.

---

## 4. Users

### 4.1 Primary persona — "the builder"

A student or small-business owner in Bangladesh who needs structured data from a site they use
regularly (course portal, supplier catalogue, price list). They can use a browser expertly and may
write a little Python, but will not maintain a scraper. They pay in BDT via bKash. **They are the
one who records.**

Needs: get a working endpoint quickly; not have it silently break; understand what it costs.

### 4.2 Secondary persona — "the consumer"

Someone the builder shares an API with — a classmate, a colleague, a customer. They never record
anything. They accept an invite, mint an API key, read the docs page, and call the endpoint.

Needs: know the price before calling; get a key without asking anyone; see a spec they can paste
into their tooling.

### 4.3 Operator persona — "the super admin"

The platform owner (seeded from an email allowlist). Verifies bKash recharges that the SMS matcher
couldn't auto-match, approves cashouts, edits tier pricing at runtime, moderates users and APIs,
and reads the audit log. Sits above all tiers and quotas.

---

## 5. Core user journeys

### J1 — Build an API (the product moment)

1. User signs in (Google or email/password), claims a username on first login.
2. Clicks **New API**, enters a name and a start URL. The system checks the daily creation quota.
3. The worker opens a **real Chromium window** on the desktop. The React page beside it is a
   control panel showing the live step list.
4. The user browses normally: navigates, types into a search box, clicks. Each interaction appears
   in the step list within a second; a step can be undone.
5. The user switches to **pick mode** and clicks the data they want. The system offers an
   extraction wizard: confirm the repeating container, then name each field. An LLM suggests field
   names and types; a selector compiler turns each pick into ranked, stable selectors.
6. The user marks the search term as a **parameter** (`query`, string, required, example value).
7. **Test extraction** shows sample JSON immediately.
8. Save → the workflow is `ready` (with an inferred output schema and, optionally, an encrypted
   `storage_state` snapshot for logged-in sites).
9. **Publish** → a slug, a public endpoint, and an OpenAPI spec generated in the background.

**Acceptance:** recording a search on a live site yields a sample of ≥ 5 items with correct field
names and types, and the published endpoint returns the same shape.

### J2 — Call an API

1. Consumer creates an API key (shown in plaintext exactly once).
2. `GET /v1/run/{slug}?query=physics` with `X-API-Key`.
3. Within `SYNC_WAIT` (55 s) the call returns `200` with `{data, meta}`. Past that it returns `202`
   with an `execution_id` and a status URL; `Prefer: respond-async` forces that path immediately.
4. Repeat calls inside the API's `cache_ttl` return `meta.cached = true` — free, no replay.
5. Failures return `502` with an error summary; a screenshot and HTML dump are stored for the owner.

### J3 — Share and monetize

1. A Pro/Max owner sets the API's visibility to shared, picks a pricing mode (free / one-time /
   per-call / creator subscription), and creates an invite link.
2. The consumer opens `/invite/{token}`, signs in, and sees the price against their wallet balance.
3. Accepting debits their wallet (no bKash, no admin) and creates an access grant.
4. Per-call pricing debits at enqueue and **refunds automatically** if the replay fails — consumers
   pay only for successful calls.
5. Each sale splits into a platform cut and creator **earnings**. Earnings can be swept into
   spendable balance by anyone; only Max creators can **cash out** to bKash (manual admin payout).

### J4 — Pay (money in)

1. User opens Billing → **Add funds**, enters any amount ≥ ৳10.
2. The UI shows the platform's bKash number and asks them to "Send Money", then paste the TrxID.
3. An SMS forwarder on the admin phone POSTs every incoming bKash SMS to a webhook. The matcher
   pairs TrxID + amount **in either arrival order** and credits the wallet automatically.
4. If nothing matches (unparsed SMS, wrong amount), the transaction lands in the admin queue for
   manual verification. **Manual verification works with the webhook entirely absent** — the
   webhook is an accelerator, not a dependency.

---

## 6. Functional requirements

### 6.1 Accounts & access (`FR-A`)

| ID | Requirement |
|---|---|
| FR-A1 | Sign-in via Google OAuth **and** email/password; same-email accounts auto-link on Google sign-in. Registering an email that already exists is rejected. |
| FR-A2 | Every user claims an immutable, lowercase username (`^[a-z0-9_]{3,30}$`) before reaching any app page. |
| FR-A3 | Two roles only: `user` and `super_admin`. Super admins are seeded from an email allowlist and thereafter managed in the UI. |
| FR-A4 | Self-service profile: edit name/phone/password, view active sessions, "log out everywhere else", delete own account. Username and email are immutable for everyone, including super admins. |
| FR-A5 | Suspended users cannot sign in, have live sessions rejected, and cannot have their APIs or keys used. |
| FR-A6 | Every super-admin action writes an audit-log row (actor, action, target, before/after detail — never secrets). |

### 6.2 Recording (`FR-R`)

| ID | Requirement |
|---|---|
| FR-R1 | Starting a session opens a headful Chromium window driven by the worker; the web UI is a control panel, not an embedded browser. |
| FR-R2 | Recorded interactions — navigation, click, fill, key press — stream to the panel live over a WebSocket and can be individually undone. |
| FR-R3 | Each interaction stores **ranked selector candidates**, not a single selector, so small markup changes don't break replay. Icon-only and label-only controls resolve to stable selectors. |
| FR-R4 | Pick mode intercepts clicks to select elements without triggering the page, and reports how many similar elements exist (single vs list). |
| FR-R5 | An extraction wizard turns picks into a root + named fields with take/transform options; the compiler produces deterministic selectors, and an LLM handles semantic fields and self-heals when selectors miss. |
| FR-R6 | Any recorded input can be marked as a **parameter** with name, type, required flag, and example. |
| FR-R7 | Sessions time out (10 min idle / 30 min hard) and can be cancelled; a dead worker surfaces in the panel within ~20 s. |
| FR-R8 | Exactly **one** recording session runs at a time on the host. |

### 6.3 Authenticated targets (`FR-L`)

| ID | Requirement |
|---|---|
| FR-L1 | With "saved logins" enabled, the recorder uses an app-managed persistent Chromium profile; the user signs into target sites there once. |
| FR-L2 | At save time the context's `storage_state` is snapshotted and stored **Fernet-encrypted** on the workflow; replays inject it into a fresh headless context. |
| FR-L3 | The product never reads the user's installed Chrome profile. The auth model is **owner-auth**: replays act as the recording owner, not as the caller. Per-caller or SSO login-at-replay is out of scope. |

### 6.4 Publishing & execution (`FR-X`)

| ID | Requirement |
|---|---|
| FR-X1 | Publishing snapshots the workflow (steps, extraction, params) so later edits to the draft don't silently change a live API. |
| FR-X2 | `GET /v1/run/{slug}` authenticates by API key (hashed at rest), coerces and validates parameters (`422` on failure), checks grant and quota, and returns `{data, meta}`. |
| FR-X3 | Sync response under `SYNC_WAIT`, otherwise `202` + status URL; `GET /v1/executions/{id}` returns status and result. |
| FR-X4 | Replay tries selector candidates in order with bounded timeouts, substitutes parameters, and enforces a whole-run timeout (default 90 s). |
| FR-X5 | On failure, a screenshot and HTML dump are written to `data/failures/{execution_id}/` and linked from the execution row. |
| FR-X6 | Results are cached per API per parameter set for `cache_ttl`; cache hits are free and marked `meta.cached`. |
| FR-X7 | Every call writes an `ApiExecution` row — the audit trail and the basis for owner-facing stats. Retention keeps the last 200 per API. |
| FR-X8 | Replay concurrency is capped at 2, headless, `--disable-gpu`. No automatic retries in v1. |
| FR-X9 | Per-key rate limiting and permissive CORS on `/v1` (the internal `/api` app allows only the frontend origin, with credentials). |

### 6.5 API documentation (`FR-D`)

| ID | Requirement |
|---|---|
| FR-D1 | The OpenAPI 3.1 skeleton (paths, params, response schema, security) is built **deterministically** from stored workflow data and is guaranteed valid. |
| FR-D2 | A hosted LLM fills only human prose — summaries, descriptions, examples — merged into the validated skeleton. |
| FR-D3 | **Spec generation failure never blocks publishing.** On LLM error or invalid output: retry once, then fall back to template prose. With the LLM disabled entirely, publishing still yields a valid spec. |
| FR-D4 | `GET /v1/apis/{slug}/openapi.json` plus a rendered docs page with working "try it"; owners can regenerate on demand. |

### 6.6 Plans, wallet & metering (`FR-M`)

| ID | Requirement |
|---|---|
| FR-M1 | Three tiers — Free / Pro (৳100/mo) / Max (৳500/mo) — with **runtime-editable** price, daily creation limit, monthly call quota, platform cut %, cashout flag, and invitee cap. |
| FR-M2 | A prepaid BDT wallet is the single internal rail. Recharging is the **only** thing that touches bKash for money-in and the only purpose an admin verifies. |
| FR-M3 | Subscriptions and one-time API access are internal wallet debits — no payment intent, no admin. |
| FR-M4 | Per-call pricing debits atomically at enqueue (gate and debit are the same operation — no oversell) and refunds in full on `failed`/`timeout`. |
| FR-M5 | Each sale splits into platform cut and creator earnings such that the two legs always sum to the debited price (no fractional dust). |
| FR-M6 | Earnings live in a separate bucket; **sweep** moves earnings → spendable balance for anyone, **cashout** (Max only, manual admin payout) moves earnings → bKash. |
| FR-M7 | Daily creation quota is enforced on the **Asia/Dhaka** calendar day; monthly call quota on the Dhaka calendar month. Redis counts, Postgres is the fallback truth if Redis is flushed. |
| FR-M8 | Super admins bypass every quota, gate, and payment prompt — by an explicit `is_super` branch, never by faking a tier. |

### 6.7 Sharing (`FR-S`)

| ID | Requirement |
|---|---|
| FR-S1 | Sharing, invites, and pricing require tier ∈ {pro, max}. |
| FR-S2 | Access is granted per user via invite links; owners list and revoke grants and invites. |
| FR-S3 | Tier rules are evaluated **at call time**, never materialized: if an owner's Pro lapses, their shared APIs return `403` for non-owners on the next call and self-heal on renewal. |
| FR-S4 | Grantees get their own keys, their own docs access, and a "shared with me" list. |

### 6.8 Payments & admin (`FR-P`)

| ID | Requirement |
|---|---|
| FR-P1 | Recharge intents expire after 24 h; TrxIDs are normalized and globally unique (replaying someone else's TrxID is impossible). |
| FR-P2 | The SMS webhook is token-authenticated, stores every raw receipt, dedupes double-sends, and parses best-effort; unparsed receipts still appear in the admin feed. |
| FR-P3 | The matcher runs on both triggers (TrxID submitted, SMS arrived) and is **order-independent**, inside one locked transaction. Underpayment is flagged for a human rather than auto-verified. |
| FR-P4 | Admin console: transactions queue, raw SMS feed, cashout queue, users, APIs/workflows moderation, plan editor, platform stats, audit log. |

---

## 7. Non-functional requirements

| Area | Requirement |
|---|---|
| **Deployment** | Single Windows 11 machine. Postgres 16 + Redis 7 in Docker; FastAPI, the worker, and Vite run natively. Not designed for multi-machine deployment. |
| **Process isolation** | Playwright runs **only** in the worker process — never in FastAPI, never in Docker. FastAPI and the worker communicate **only** through Redis (Streams for jobs, pub/sub for live recording). The WebSocket endpoint is a dumb bridge. |
| **Concurrency budget** | 1 recording · 2 replays · 1 LLM job. Explicit and fixed, because one laptop hosts everything. |
| **Latency** | Live recorder events appear in the panel within ~1 s. Sync API calls return within 55 s or degrade to async. Whole replay budget 90 s. |
| **Data integrity** | SQLAlchemy 2.0 typed async models on asyncpg; enums stored as varchar; JSONB replaced never mutated; money `Numeric(10,2)` BDT; timestamps tz-aware UTC. Wallet debits use an atomic conditional UPDATE — never read-then-write. |
| **Security** | Secrets only via config/`.env`, never committed. API keys hashed at rest, plaintext shown once. Auth snapshots Fernet-encrypted. Passwords bcrypt, min 8 chars, never logged. Login rate-limited (10 failures / 15 min / email). Webhook token-authenticated. |
| **Availability** | A wedged browser must not take down the API. LLM downtime must not block publishing. Redis flush must not grant free quota. |
| **Accessibility & design** | Frontend follows the "Warm Editorial" system in [DESIGN.md](DESIGN.md); Tailwind v4, CSS-first. |
| **Testing** | Services tested against real Postgres/Redis; replay and extraction against a local fixture site (including a page whose primary selector is missing, to exercise candidate fallback). No external-network tests. LLM calls mocked except one opt-in integration test. |

---

## 8. Success metrics

| Metric | Target |
|---|---|
| Time from "New API" to first successful `curl` (new user, no docs) | < 10 min |
| Publish success rate (record → `ready` → published) | > 80 % of started recordings |
| Replay success rate on a stable target over 7 days | > 90 % |
| Spec validity (`openapi-spec-validator`) | 100 % of published APIs, LLM up or down |
| Recharges auto-matched without admin action | > 70 % |
| Wallet ledger integrity (sum of legs = debited price, no orphan debits) | 100 % — asserted in tests |
| Median sync call latency on a cache hit | < 200 ms |

---

## 9. Release status

| Stage | Scope | Status |
|---|---|---|
| Phases 0–2 | Scaffold, infra, Google auth, schema, plans, quotas | Shipped |
| Phases 3–4 | Recording pipeline, element picking, extraction, parameters, saved logins | Shipped |
| Phase 5 | Publish & execute — the product moment | Shipped |
| Phase 6 | Hybrid OpenAPI generation + docs page | Shipped |
| Phase 7 | bKash billing, SMS matcher, admin console | Shipped |
| Phase 8 | Invites, grants, call-time tier enforcement | Shipped |
| Phase 9 | Hardening: retention, GC, nicer errors, E2E smoke, README | Shipped |
| T1–T7 | Email/password auth, usernames, profiles, DB-backed plans, admin controls, moderation, owner stats | Shipped |
| W1–W6 | Wallet, ledger, wallet-routed subscriptions & access, per-call metering, revenue split, tier re-base, cashout | Shipped |
| Post-plan | LLM-first semantic extraction, compile-time selector compiler with self-heal, pick-driven extraction wizard, Gemini as the sole LLM provider | Shipped |

### Deferred / next

- Selector self-healing beyond the current LLM reheal pass; automatic re-record prompts on repeated
  failure.
- Known recorder gaps around root/DOM edge cases and multi-tab flows.
- Screencasting the recorded browser into the web page (removes the single-machine constraint on
  the recorder).
- Scheduled/cron execution of published APIs.
- Creator revenue reporting beyond per-API stats.

---

## 10. Key risks

| Risk | Impact | Mitigation |
|---|---|---|
| **Anti-bot defenses** block headless replay even though recording worked | API unusable for that site | Real Chrome channel while recording, `storage_state` auth, realistic UA/viewport; clear `502` with failure screenshot. CAPTCHA solving is a non-goal. |
| **Selector drift** as target sites change markup | Silent breakage | Ranked selector candidates + compiled selectors + LLM reheal; failure artifacts make re-recording obvious and cheap. |
| **Single-machine capacity** — replays are expensive and concurrency is 2 | Queue backup, slow calls | Per-call metering ties price to compute; monthly call quotas per tier; result caching; explicit concurrency caps. |
| **Manual bKash verification** doesn't scale and depends on one phone | Recharges stall | SMS auto-matcher handles the common path; manual admin verification always works standalone; intents expire in 24 h. |
| **LLM dependency** (hosted Gemini) on the authoring and docs path | Degraded authoring | Deterministic spec skeleton and template prose fallback; deterministic compiled selectors do the extraction work, LLM only assists. |
| **Owner-auth model** — replays use the owner's saved session | Owner's account is exercised by consumers' calls | Documented explicitly; encrypted at rest; owner controls sharing, pricing, and can deactivate an API instantly. |
| **Legal/ToS exposure** from scraping third-party sites | Course-project risk | Visible disclaimer at creation; responsibility rests with the user; admin can deactivate or delete any API. |
| **Recorder fragility** on iframes, popups, and infinite pagination | Partial support | Recorder warns on detection; documented as a known limitation rather than a silent failure. |

---

## 11. Glossary

| Term | Meaning |
|---|---|
| **Workflow** | The recorded artifact: ordered steps + extraction config + parameters. Draft until it has extraction, then `ready`. |
| **Custom API** | A published, immutable snapshot of a workflow, addressable by slug. |
| **Step DSL** | The stored representation of one recorded interaction (`goto`, `click`, `fill`, `press`, `extract`) with its ranked selector candidates. |
| **Pick** | A user click in pick mode that identifies an element to extract or a control to parameterize. |
| **Grant** | A non-revoked, non-expired right for a specific user to call a specific API. |
| **Storage state** | An encrypted snapshot of cookies/localStorage captured at save time and injected into replays. |
| **Effective tier** | The tier of the user's active, unexpired subscription, else `free` — computed per request, never materialized. |
| **Sweep** | Moving creator earnings into spendable wallet balance. |
| **Cashout** | Moving creator earnings out to bKash — Max tier only, manually approved. |

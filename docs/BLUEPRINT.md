# API Builder — Project Blueprint

A web app that turns manual browsing into reusable APIs: the user records a browser session
(navigate, search, click), marks the data they want extracted, and the system saves the workflow
as a parameterized automation that can be called as a JSON HTTP API — complete with an
auto-generated OpenAPI spec (via a hosted LLM), Google login, tiered subscriptions,
and manual bKash payment verification.

This document is the **design contract**. The build order lives in
[IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Read the relevant sections here before
implementing each phase.

---

## 1. System overview

### 1.1 Processes on the machine

Everything runs on one Windows 11 laptop. Infra is Dockerized; all Python
runs **natively on Windows** because the recorder must open a visible browser window on the
user's desktop (Playwright can't drive a headful window from inside Docker).

| Process            | Where          | Port | Role |
|--------------------|----------------|------|------|
| React (Vite dev)   | native         | 3000 | UI; proxies `/api/*` and `/api/ws/*` to FastAPI |
| FastAPI (uvicorn)  | native         | 8000 | Auth, CRUD, billing, public `/v1` API, WebSocket gateway. **Never touches Playwright.** |
| Worker (`python -m app.workers.main`) | native | —    | Owns all Playwright browsers + LLM jobs. Consumes Redis Streams. |
| PostgreSQL 16      | Docker         | 5432 | System of record |
| Redis 7            | Docker         | 6379 | Job queues (Streams), live-session pub/sub, sessions, quotas, cache |

### 1.2 Architecture diagram

```
┌──────────────┐  HTTP/WS (vite proxy /api → :8000)   ┌─────────────────────────┐
│ React :3000  │◄────────────────────────────────────►│ FastAPI :8000           │
│  - dashboard │                                      │  /api/*  (session auth) │
│  - recorder  │                                      │  /v1/*   (API-key auth) │
│    panel     │                                      │  /api/ws/recordings/{id}│
└──────────────┘                                      └───────┬─────────────────┘
                                                              │ Redis only — never Playwright
                     ┌────────────────────────────────────────┼──────────────┐
                     │                        Redis :6379     │              │
                     │  Streams: jobs:rec  jobs:exec  jobs:llm│              │
                     │  Pub/Sub: rec:evt:{sid} rec:cmd:{sid}  │  PostgreSQL  │
                     │           exec:done:{id}               │    :5432     │
                     │  KV: sessions, quotas, cache, results  │              │
                     └───────────────┬────────────────────────┴──────────────┘
                                     │ consume jobs / publish events
                     ┌───────────────▼────────────────────────────────┐
                     │ Worker process (asyncio, Proactor loop)        │
                     │  rec handler (max 1): headful Chromium,        │
                     │    injected recorder.js, element picker        │
                     │  exec handler (max 2): headless replay,        │
                     │    --disable-gpu, storage_state auth           │
                     │  llm handler (max 1): calls the hosted LLM     │
                     │  periodic: subscription expiry sweep           │
                     └────────────────────────────────────────────────┘
```

### 1.3 One critical UX fact

The recorded browser is a **real, headful Chromium window on the user's desktop**, launched by the
worker. It is *not* embedded/streamed inside the web page (CDP screencasting into the browser is a
v2+ feature and out of scope). The React "Recorder" page is a **control panel**: it shows the live
step list, toggles record/pick modes, previews extraction, and saves — while the user interacts
with the native browser window next to it. This works because everything runs on one machine.

---

## 2. Core design decisions (and why)

1. **Playwright lives only in the worker process.** Crash isolation (a wedged browser can't take
   down the API), memory isolation, and it sidesteps event-loop conflicts. FastAPI stays a thin
   async I/O layer; all cross-process communication goes through Redis, so nothing ever blocks the
   API event loop.
2. **Custom asyncio worker on Redis Streams — not Celery/RQ/ARQ.** RQ requires `fork` (no native
   Windows). Celery on Windows needs the `solo` pool hacks. ARQ registers Unix signal handlers.
   A ~70-line asyncio consumer with consumer groups + per-queue semaphores is dependency-free,
   Windows-clean, and handles the recorder's long-lived interactive "jobs" naturally. Skeleton in §5.4.
3. **Auth reuse via `storage_state` snapshots, not raiding the installed Chrome profile.**
   Chrome 127+ App-Bound Encryption makes copied cookies undecryptable, and Chrome 136+ blocks
   CDP automation of the default profile. So the "use my cookies/passwords" setting becomes:
   the recorder uses an **app-managed persistent profile** (user logs into sites there once;
   optionally `channel="chrome"` so they can sign into Chrome Sync for real password autofill),
   and at save time we snapshot `context.storage_state()` — encrypted with Fernet — onto the
   workflow. Replays inject that snapshot into a fresh headless context. Details in §4.9.
4. **Hybrid OpenAPI generation.** The spec skeleton (paths, params, response schema, security) is
   built **deterministically** from stored workflow data — guaranteed valid. The LLM only
   fills human prose (descriptions, summaries, examples) via a prompt-embedded-schema completion.
   This keeps the LLM task small and cheap, and the system still ships a
   valid spec if the LLM is down. Details in §6.
5. **Google OAuth redirect lands on the frontend origin but is served by FastAPI**, because Vite
   proxies `/api/*` → `:8000`. Your registered redirect URI
   `http://localhost:3000/api/auth/callback/google` therefore hits FastAPI directly, cookies are
   same-origin, and there is zero CORS pain. Details in §7.
6. **Tier rules are checked at request time**, not materialized. If a Pro subscription lapses, the
   owner's shared APIs simply stop resolving for non-owners on the next call — no cascade updates,
   self-healing on renewal.
7. **Concurrency budget is explicit** because one laptop hosts everything: 1 recording session,
   2 concurrent replays, 1 LLM job, headless browsers launched with `--disable-gpu` to keep
   replay off the GPU.

---

## 3. Database schema (SQLAlchemy 2.0, async)

Ten tables: `users`, `subscriptions`, `payment_transactions`, `bkash_sms_receipts`, `workflows`,
`custom_apis`, `api_keys`, `api_access_grants`, `api_invites`, `api_executions`.

Conventions:
- UUID PKs, timezone-aware UTC timestamps, money as `Numeric(10,2)` BDT.
- Enums stored as varchar (`native_enum=False`) — avoids `ALTER TYPE` migration pain.
- JSONB columns are **replaced, never mutated in place** (SQLAlchemy doesn't track in-place
  mutation without `MutableDict`).
- Split across `app/models/{base,user,billing,workflow,api,execution}.py`; shown consolidated here.

```python
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean, DateTime, Enum as SAEnum, ForeignKey, Index, Integer, LargeBinary,
    Numeric, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def enum_column(enum_cls: type[enum.Enum]) -> SAEnum:
    # Persist enum .value (lowercase strings) as varchar; keeps raw SQL and partial
    # indexes readable and avoids native PG enum migrations.
    return SAEnum(enum_cls, native_enum=False, length=32,
                  values_callable=lambda e: [m.value for m in e])


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


# ── Users ────────────────────────────────────────────────────────────────────

class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    google_sub: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # stable Google subject id
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    picture_url: Mapped[str | None] = mapped_column(Text)
    role: Mapped[UserRole] = mapped_column(enum_column(UserRole), default=UserRole.USER)
    # {"use_saved_logins": bool, "recorder_channel": "chromium"|"chrome"}
    settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))

    workflows: Mapped[list["Workflow"]] = relationship(back_populates="owner")
    apis: Mapped[list["CustomApi"]] = relationship(back_populates="owner")


# ── Subscriptions & payments ─────────────────────────────────────────────────

class PlanTier(str, enum.Enum):
    FREE = "free"
    PRO = "pro"
    MAX = "max"


class SubscriptionStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Subscription(Base, TimestampMixin):
    __tablename__ = "subscriptions"
    # Users with no ACTIVE row are FREE tier. Renewal of the same tier extends
    # expires_at; upgrades cancel the old row and insert a new one (no proration).
    __table_args__ = (
        Index("uq_one_active_sub_per_user", "user_id", unique=True,
              postgresql_where=text("status = 'active'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tier: Mapped[PlanTier] = mapped_column(enum_column(PlanTier))
    status: Mapped[SubscriptionStatus] = mapped_column(
        enum_column(SubscriptionStatus), default=SubscriptionStatus.ACTIVE)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_transactions.id", ondelete="SET NULL"))


class PaymentPurpose(str, enum.Enum):
    SUBSCRIPTION = "subscription"
    API_ACCESS = "api_access"


class PaymentStatus(str, enum.Enum):
    PENDING = "pending"        # intent created, waiting for user to send money + submit TrxID
    SUBMITTED = "submitted"    # TrxID submitted, not yet matched
    VERIFIED = "verified"
    REJECTED = "rejected"
    EXPIRED = "expired"        # intent older than 24h without verification


class VerificationMethod(str, enum.Enum):
    AUTO_SMS = "auto_sms"
    MANUAL_ADMIN = "manual_admin"


class PaymentTransaction(Base, TimestampMixin):
    __tablename__ = "payment_transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[PaymentPurpose] = mapped_column(enum_column(PaymentPurpose))
    plan_tier: Mapped[PlanTier | None] = mapped_column(enum_column(PlanTier))       # purpose=subscription
    api_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("custom_apis.id", ondelete="SET NULL"))                          # purpose=api_access
    amount_expected_bdt: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    amount_received_bdt: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    bkash_trx_id: Mapped[str | None] = mapped_column(String(40), unique=True, index=True)
    status: Mapped[PaymentStatus] = mapped_column(
        enum_column(PaymentStatus), default=PaymentStatus.PENDING, index=True)
    matched_sms_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("bkash_sms_receipts.id", ondelete="SET NULL"))
    verification_method: Mapped[VerificationMethod | None] = mapped_column(
        enum_column(VerificationMethod))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))                                # admin, for manual verify
    note: Mapped[str | None] = mapped_column(Text)


class BkashSmsReceipt(Base):
    __tablename__ = "bkash_sms_receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    raw_text: Mapped[str] = mapped_column(Text)
    sms_sender: Mapped[str | None] = mapped_column(String(40))       # e.g. "bKash"
    # SMS forwarders retry; hash of raw_text+minute bucket deduplicates deliveries.
    dedupe_hash: Mapped[str] = mapped_column(String(64), unique=True)
    parsed_trx_id: Mapped[str | None] = mapped_column(String(40), index=True)
    parsed_amount_bdt: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    parsed_sender_msisdn: Mapped[str | None] = mapped_column(String(20))
    matched_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_transactions.id", ondelete="SET NULL"), index=True)


# ── Workflows ────────────────────────────────────────────────────────────────

class WorkflowStatus(str, enum.Enum):
    RECORDING = "recording"   # live session in progress (row created when session starts)
    DRAFT = "draft"           # steps saved, extraction/params incomplete
    READY = "ready"           # publishable
    ARCHIVED = "archived"


class Workflow(Base, TimestampMixin):
    __tablename__ = "workflows"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    start_url: Mapped[str] = mapped_column(Text)
    status: Mapped[WorkflowStatus] = mapped_column(
        enum_column(WorkflowStatus), default=WorkflowStatus.RECORDING)
    steps: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))       # step DSL, §4.5
    parameters: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))  # §4.7
    extraction: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))  # §4.6
    output_schema: Mapped[dict | None] = mapped_column(JSONB)        # inferred JSON Schema of sample output
    sample_output: Mapped[dict | None] = mapped_column(JSONB)        # captured at save time
    browser_settings: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    # Fernet-encrypted context.storage_state() snapshot captured at save time; replays inject it.
    auth_state_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary)

    owner: Mapped["User"] = relationship(back_populates="workflows")


# ── Published APIs ───────────────────────────────────────────────────────────

class ApiVisibility(str, enum.Enum):
    PRIVATE = "private"   # owner only (Free tier ceiling)
    SHARED = "shared"     # invite/purchase grants allowed (Pro/Max)


class SpecStatus(str, enum.Enum):
    PENDING = "pending"
    GENERATING = "generating"
    READY = "ready"
    FAILED = "failed"


class CustomApi(Base, TimestampMixin):
    __tablename__ = "custom_apis"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    workflow_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)  # slugified name + 4-char suffix
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[ApiVisibility] = mapped_column(
        enum_column(ApiVisibility), default=ApiVisibility.PRIVATE)
    price_bdt: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # None/0 = free for grantees
    # Frozen copy of {steps, parameters, extraction, output_schema} at publish time,
    # so editing the workflow never silently changes a published API.
    workflow_snapshot: Mapped[dict] = mapped_column(JSONB)
    openapi_spec: Mapped[dict | None] = mapped_column(JSONB)
    spec_status: Mapped[SpecStatus] = mapped_column(enum_column(SpecStatus), default=SpecStatus.PENDING)
    cache_ttl_seconds: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    owner: Mapped["User"] = relationship(back_populates="apis")


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_keys"
    # Keys belong to a consumer (any user) and are global; what a key may call is
    # decided by api_access_grants + ownership at request time.

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(100), default="default")
    key_prefix: Mapped[str] = mapped_column(String(12), index=True)   # "ab_" + first 8 chars, for display/lookup
    key_hash: Mapped[str] = mapped_column(String(64))                 # sha256 of full key; plaintext shown once
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GrantSource(str, enum.Enum):
    INVITE = "invite"
    PURCHASE = "purchase"
    ADMIN = "admin"


class ApiAccessGrant(Base, TimestampMixin):
    __tablename__ = "api_access_grants"
    __table_args__ = (UniqueConstraint("api_id", "user_id", name="uq_grant_api_user"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("custom_apis.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    granted_via: Mapped[GrantSource] = mapped_column(enum_column(GrantSource))
    invite_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_invites.id", ondelete="SET NULL"))
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_transactions.id", ondelete="SET NULL"))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApiInvite(Base, TimestampMixin):
    __tablename__ = "api_invites"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("custom_apis.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)  # secrets.token_urlsafe(24)
    max_uses: Mapped[int | None] = mapped_column(Integer)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ── Execution log ────────────────────────────────────────────────────────────

class ExecutionStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"


class ApiExecution(Base):
    __tablename__ = "api_executions"
    __table_args__ = (Index("ix_exec_api_created", "api_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    api_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("custom_apis.id", ondelete="CASCADE"))
    caller_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"))
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("api_keys.id", ondelete="SET NULL"))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[ExecutionStatus] = mapped_column(
        enum_column(ExecutionStatus), default=ExecutionStatus.QUEUED)
    result: Mapped[dict | None] = mapped_column(JSONB)     # truncate >256KB, set result_truncated
    result_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    failure_artifact_path: Mapped[str | None] = mapped_column(Text)  # screenshot/html dump dir
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
```

Notes:
- **No `recording_sessions` table** — the `Workflow` row (status `recording`) plus transient Redis
  state is the session. Daily quota counts workflow rows created per day (Redis counter as fast path).
- Alembic: init with the async template (`alembic init -t async alembic`); engine URL
  `postgresql+asyncpg://...`. asyncpg (not psycopg) — it works on Windows' Proactor loop.

---

## 4. Recording pipeline

### 4.1 Session lifecycle

```
POST /api/recordings {start_url, name}
  → quota check (§8) → INSERT Workflow(status=recording) → XADD jobs:rec → 201 {workflow_id}
Frontend opens WS /api/ws/recordings/{workflow_id}
Worker picks up job → launches headful browser → publishes {"t":"status","state":"ready"}
  … user browses; steps stream to panel; user picks elements; marks params …
Panel sends {"t":"save", ...} → worker captures storage_state + sample extraction,
  writes steps/extraction/schema to the Workflow row (status=draft|ready), closes browser,
  publishes {"t":"saved"} → frontend navigates to workflow editor.
Guards: idle timeout 10 min, hard cap 30 min, heartbeat key rec:alive:{id} (EX 15, refreshed 5s).
  FastAPI's WS bridge watches the heartbeat; if it disappears → send {"t":"died"}, mark workflow draft.
```

### 4.2 Redis contract for live sessions

| Key/Channel | Type | Purpose |
|---|---|---|
| `jobs:rec` | Stream | Session-start jobs `{workflow_id, user_id}` |
| `rec:cmd:{wid}` | Pub/Sub | Frontend→worker commands (relayed by FastAPI from WS) |
| `rec:evt:{wid}` | Pub/Sub | Worker→frontend events (relayed by FastAPI to WS) |
| `rec:alive:{wid}` | String EX 15 | Worker heartbeat |

The FastAPI WS endpoint is a dumb bidirectional bridge: `SUBSCRIBE rec:evt:{wid}` → forward to
socket; socket message → `PUBLISH rec:cmd:{wid}`. It validates the session cookie and workflow
ownership on connect, then does pure Redis I/O — the API event loop never blocks.

**Commands (frontend → worker):**
`{"t":"set_mode","mode":"record"|"pick"|"idle"}` · `{"t":"undo_step","i":n}` ·
`{"t":"mark_param","step_i":n,"name":"query"}` · `{"t":"set_extraction","config":{...}}` ·
`{"t":"test_extraction"}` · `{"t":"bring_to_front"}` · `{"t":"save","name":"...","status":"ready"}` ·
`{"t":"cancel"}`

**Events (worker → frontend):**
`{"t":"status","state":"launching"|"ready"|"closed"|"died"}` · `{"t":"step_recorded","step":{...}}` ·
`{"t":"step_removed","i":n}` · `{"t":"pick_result","candidate":{"selectors":[...],"preview":"...","count":12}}` ·
`{"t":"extraction_result","sample":{...},"schema":{...}}` · `{"t":"error","message":"..."}` · `{"t":"saved"}`

### 4.3 How recording works inside the worker

- `launch_persistent_context(user_data_dir, headless=False, channel=..., args=...)` — profile dir
  per user under `data/profiles/{user_id}` when `use_saved_logins` is on, else a temp dir.
- `context.expose_binding("__abEmit", on_event)` — the injected script calls this to deliver
  events; it survives navigations.
- `context.add_init_script(path="app/recorder/injected.js")` — re-injected on every page/frame.
- `page.on("framenavigated")` for main-frame navigations → `goto`-equivalent steps (only when
  user-initiated address-bar/link navigation changes origin+path; SPA route changes are captured
  as part of click steps).
- `context.on("page")` — new tabs: follow the newest page for recording; note as a limitation
  (multi-tab workflows are v2).

`injected.js` responsibilities:
1. **Record mode:** listeners for `click`, `input` (debounced 400 ms into one `fill` per field),
   `keydown` (Enter/Tab), `change` (selects). Each event → selector candidates + value → `__abEmit`.
2. **Pick mode:** hover overlay (outline via absolutely-positioned div, not CSS class), click
   captures the element instead of acting on it (`preventDefault` + `stopPropagation` in capture
   phase), emits selector candidates + `innerText` preview + "similar element" count (see below).
3. **Selector candidates**, best-first: `[data-testid]` → `#id` (skip if it looks generated:
   digits/uuid-ish) → `[name]` → `role`+accessible-name → trimmed CSS path (max 4 levels, nth-of-type
   only where needed). Store the **top 3** on every step; replay tries them in order (§5.2).
4. **"Select similar"** for lists: strip trailing `:nth-of-type(n)` from the clicked element's path,
   count matches, propose the generalized selector + common ancestor as the list root.

Scroll events are **not recorded** — Playwright locators auto-scroll on replay. The panel offers an
explicit "scroll to load more ×N" button for infinite-scroll pages (records `{"type":"scroll_page","times":N}`).

### 4.4 Mode switching

Worker holds authoritative mode; on `set_mode` it runs `page.evaluate("window.__abSetMode('pick')")`
on the active page and the init script reads the current mode from a variable the worker refreshes
on each navigation (evaluate after `framenavigated`).

### 4.5 Step DSL (stored in `workflows.steps`)

```json
[
  {"i": 0, "type": "goto", "url": "https://www.rokomari.com"},
  {"i": 1, "type": "fill", "selectors": ["#js--search-input", "input[name=key]"],
   "value": {"param": "query"}},
  {"i": 2, "type": "press", "selectors": ["#js--search-input"], "key": "Enter"},
  {"i": 3, "type": "wait_for", "selectors": [".book-list-wrapper"], "state": "visible",
   "timeout_ms": 15000},
  {"i": 4, "type": "extract", "ref": "main"}
]
```

Step types v1: `goto`, `click`, `fill`, `press`, `select_option`, `wait_for`, `scroll_page`,
`extract`. `value` is either `{"literal": "..."}` or `{"param": "name"}`. The recorder
auto-inserts a `wait_for(domcontentloaded)` equivalent after navigation-causing steps; the panel's
save flow appends the `extract` step last.

### 4.6 Extraction config (stored in `workflows.extraction`)

```json
{
  "main": {
    "mode": "list",
    "root": ".book-list-wrapper .book-item",
    "fields": [
      {"name": "title",  "selector": ".book-title",  "take": "text"},
      {"name": "price",  "selector": ".book-price",  "take": "text", "transform": "number"},
      {"name": "url",    "selector": "a",            "take": "attr:href", "transform": "abs_url"},
      {"name": "cover",  "selector": "img",          "take": "attr:src",  "transform": "abs_url"}
    ]
  }
}
```

`mode`: `"single"` (fields are page-global selectors → one object) or `"list"` (fields relative to
each `root` match → array of objects). `take`: `text` | `html` | `attr:<name>`.
`transform`: `none` | `number` (strip non-numeric, parse) | `abs_url` | `trim`.

### 4.7 Parameters

When the user clicks "make this a parameter" on a recorded `fill` step, the panel sends
`mark_param`; the step's literal value becomes `{"param": name}` and an entry is added to
`workflows.parameters`:

```json
[{"name": "query", "type": "string", "required": true, "example": "physics",
  "description": null, "source_step": 1}]
```

`type` v1: `string` | `integer` | `number` | `boolean` (coerced from query strings at execute time,
422 on failure). The recorded literal becomes `example` and the default replay value for
"test extraction".

### 4.8 Output schema inference

On `test_extraction` and at save, the worker runs the extraction against the live page, returns the
sample, and infers a JSON Schema with `genson`, post-processed: arrays get `items` from merged
element schemas; all leaf types from transforms (`number` → `"type": "number"`). Stored in
`workflows.output_schema` — reused verbatim in the OpenAPI response schema (§6.3).

### 4.9 The "saved logins" setting (replaces raw Chrome-profile reuse)

`users.settings.use_saved_logins`:
- **off (default):** recorder uses a throwaway profile; replays get a fresh context. 
- **on:** recorder uses the app-managed persistent profile `data/profiles/{user_id}` (optionally
  `channel:"chrome"` from settings so the user can enable Chrome Sync → real password autofill,
  legitimately). At save time the worker calls `context.storage_state()`, encrypts it with Fernet
  (`AUTH_STATE_FERNET_KEY`), stores it on the workflow. **Replays never open the profile** — they
  inject the snapshot via `new_context(storage_state=...)`, so concurrent executions don't fight
  over a profile lock.

Why not "use my regular Chrome's cookies": Chrome 127+ App-Bound Encryption makes copied cookie DBs
undecryptable and Chrome 136+ refuses CDP automation of the default profile. Surface this in the
Settings UI as an "experimental — usually blocked by Chrome" disabled option with a tooltip, so the
requirement is visibly acknowledged. Add "Refresh logins" button on a workflow → opens a short
recorder session on the same profile just to re-capture `storage_state` when cookies expire.

---

## 5. Execution — serving the custom APIs

### 5.1 Public endpoint contract (`/v1`, API-key auth, permissive CORS)

Mounted as a sub-application (`app.mount("/v1", public_app)`) with its own CORS middleware
(`allow_origins=["*"]`, no credentials); the internal `/api` app allows only the frontend origin
with credentials.

```
GET /v1/run/{slug}?query=physics
  Headers: X-API-Key: ab_...
  200 {"data": [...], "meta": {"cached": false, "duration_ms": 8214, "execution_id": "..."}}
  202 {"execution_id": "...", "status_url": "/v1/executions/{id}"}     ← if > SYNC_WAIT (55s)
       or if header Prefer: respond-async
  401 bad key · 403 no grant / owner tier lapsed / api disabled · 404 slug
  422 param coercion failed · 429 rate limit · 502 replay failed (error summary in body)

GET /v1/executions/{id}          (same auth) → status + result when done
GET /v1/apis/{slug}/openapi.json (API key, or browser session with a grant)
```

Sync flow inside the endpoint (never blocks the loop — it awaits Redis):

```python
await pubsub.subscribe(f"exec:done:{exec_id}")        # subscribe BEFORE enqueue (no race)
await redis.xadd("jobs:exec", {"payload": payload})
try:
    await asyncio.wait_for(next_message(pubsub), timeout=settings.sync_wait_seconds)
except asyncio.TimeoutError:
    return JSONResponse({"execution_id": ...}, status_code=202)
result = json.loads(await redis.get(f"exec:result:{exec_id}"))   # worker SETs (EX 600) then PUBLISHes
```

### 5.2 Replay engine (`app/recorder/replay.py`)

- `chromium.launch(headless=True, args=["--disable-gpu"])`; context from decrypted `storage_state`
  when present; realistic UA/viewport; `default_timeout` 10 s.
- Interprets the step DSL from `custom_apis.workflow_snapshot` (published) or `workflows.steps`
  (test runs). For each step, try selector candidates in order (10 s / 5 s / 5 s budgets); locators
  auto-wait and auto-scroll. Param substitution from validated inputs.
- Whole-run timeout `EXEC_TIMEOUT_SECONDS` (default 90). On failure: screenshot + HTML dump to
  `data/failures/{execution_id}/`, path stored on the execution row, status `failed`.
- Extraction runs in one `page.evaluate` pass over the config → JSON, then transforms applied
  Python-side.
- Concurrency: exec semaphore = 2 (config). No retries in v1 (selector fallbacks already absorb
  most flake); the execution row is the audit trail.

### 5.3 Redis key map (complete)

| Key | Type/TTL | Purpose |
|---|---|---|
| `sess:{sid}` | Hash, EX 7 d refresh | Web session → user_id |
| `oauth:state:{state}` | EX 600 | OAuth CSRF state |
| `quota:create:{user_id}:{YYYYMMDD}` | INCR, EX 48 h | Daily creation attempts (date in Asia/Dhaka) |
| `rl:key:{api_key_id}:{unix_minute}` | INCR, EX 120 | Per-key rate limit (default 60/min) |
| `cache:exec:{api_id}:{sha256(sorted_params)}` | EX = api.cache_ttl | Result cache |
| `jobs:rec` / `jobs:exec` / `jobs:llm` | Streams | Work queues (consumer group `workers`) |
| `exec:result:{id}` | EX 600 | Serialized result handoff |
| `exec:done:{id}` | Pub/Sub | Completion signal |
| `rec:cmd/evt/alive:{wid}` | see §4.2 | Live recording |

### 5.4 Worker skeleton (`app/workers/main.py`)

```python
import asyncio, json, logging, sys
from redis.asyncio import Redis
from app.config import settings
from app.workers import handlers

log = logging.getLogger("worker")

QUEUES = {  # stream → (handler, max concurrent)
    "jobs:rec":  (handlers.record_session, settings.rec_max_concurrency),   # 1
    "jobs:exec": (handlers.execute_api,    settings.exec_max_concurrency),  # 2
    "jobs:llm":  (handlers.generate_spec,  1),
}

async def consume(redis: Redis, stream: str, handler, limit: int) -> None:
    group = "workers"
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except Exception:  # BUSYGROUP on restart
        pass
    sem = asyncio.Semaphore(limit)

    async def run(msg_id: str, fields: dict) -> None:
        try:
            await handler(json.loads(fields["payload"]))
        except Exception:
            log.exception("job failed stream=%s id=%s", stream, msg_id)
        finally:
            await redis.xack(stream, group, msg_id)
            sem.release()

    while True:
        await sem.acquire()                                   # backpressure before reading
        resp = await redis.xreadgroup(group, f"c-{stream}", {stream: ">"}, count=1, block=5000)
        if not resp:
            sem.release()
            continue
        _, messages = resp[0]
        msg_id, fields = messages[0]
        asyncio.create_task(run(msg_id, fields))

async def main() -> None:
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await asyncio.gather(
        *(consume(redis, s, h, n) for s, (h, n) in QUEUES.items()),
        handlers.periodic_sweep(),   # every 10 min: expire subscriptions, expire stale payment intents
    )

if __name__ == "__main__":
    if sys.platform == "win32":
        # Playwright spawns subprocesses; requires the (default) Proactor loop — guard against overrides.
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
```

---

## 6. LLM integration (hosted)

### 6.1 Provider & configuration

The LLM is reached over an **OpenAI-compatible HTTP endpoint** — no local model is run. Two
providers are supported via `LLM_PROVIDER`:

- **`gemini`** (default) — Google AI Studio's OpenAI-compat endpoint (`GEMINI_BASE_URL`,
  `GEMINI_API_KEY`, `GEMINI_MODEL`). Use a fast, non-thinking model (e.g.
  `gemini-flash-lite-latest`); reasoning models that emit `<thought>` blocks are slow and
  unreliable for structured output.
- **`craftx`** — a hosted OpenAI-compatible gateway (`CRAFTX_BASE_URL`, `CRAFTX_API_KEY`,
  `CRAFTX_MODEL`).

Operational rules:
- `jobs:llm` concurrency = 1 — LLM calls are serialized (one hosted quota, kept predictable).
- All headless replay browsers launch with `--disable-gpu` (§5.2).
- `LLM_ENABLED=false` must keep the whole product functional (fallback path, §6.4).

### 6.2 Client — OpenAI SDK pointed at the hosted endpoint

`app/llm/client.py` builds an `AsyncOpenAI` client for the configured provider and exposes
`complete_json(system, user, schema, max_tokens, images=None)`. Both providers are unreliable with
`response_format` json_schema, so the schema is **embedded in the prompt** and the response is
defensively parsed by `_extract_json` (strips `<think>/<thought>/<thinking>` reasoning blocks and
markdown code fences, then extracts the first balanced JSON object). `images` carries a base64
screenshot part for the multimodal selector compiler.

```python
# app/llm/client.py (shape)
client = _build_client()   # craftx | gemini (default), pointed at an OpenAI-compatible /v1

async def complete_json(system, user, schema, max_tokens=2000, images=None) -> dict:
    user += "\n\nRespond with ONLY a JSON object matching this schema ...\n" + json.dumps(schema)
    resp = await client.chat.completions.create(
        model=MODEL_NAME, messages=[...], temperature=0.2, max_tokens=max_tokens)
    return _extract_json(resp.choices[0].message.content)
```

Because a hosted gateway may still wrap or prefix the JSON, output is **not** structurally
guaranteed the way a local grammar-constrained server would be — `_extract_json` plus the spec
validator + retry (§6.3) are what keep malformed output from shipping.

### 6.3 Hybrid spec generation (`jobs:llm` handler)

1. **Deterministic skeleton** (`app/llm/spec_builder.py`, pure Python, always valid): OpenAPI
   **3.1.0** document with `GET /v1/run/{slug}`; query parameters from `parameters` (types,
   required, examples); `200` response schema = stored `output_schema` wrapped in
   `{data, meta}`; error responses 401/403/422/429/502; `securitySchemes: {ApiKeyAuth: {type:
   apiKey, in: header, name: X-API-Key}}`; `servers: [{url: http://localhost:8000}]`.
   OpenAPI 3.1 is deliberate — it embeds the inferred JSON Schema verbatim.
2. **LLM enrichment**: one `complete_json` call. The response schema is **built dynamically** so
   each parameter name is an explicit required property (small models do far better with
   enumerated keys than free-form maps):

   ```json
   {"type": "object", "additionalProperties": false,
    "required": ["api_description", "endpoint_summary", "param_query"],
    "properties": {
      "api_description":  {"type": "string", "maxLength": 400},
      "endpoint_summary": {"type": "string", "maxLength": 120},
      "param_query":      {"type": "string", "maxLength": 200},
      "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3}}}
   ```

   Prompt context: API name, target site domain, humanized step list ("opens rokomari.com, types
   {query} into the search box, presses Enter, extracts a list of books"), parameter names with
   example values, sample output truncated to ~1,500 chars. Instructions: ≤ 2 sentences per
   description, do not invent parameters or fields.
3. **Merge** enrichment into the skeleton (`info.description`, `summary`, per-param `description`, `tags`).
4. **Validate** with `openapi-spec-validator`. On failure: retry once with the validator error
   appended to the prompt.

### 6.4 Fallback & status

Any failure after the retry — the LLM gateway down, timeout, still-invalid merge — ships the
**skeleton with template prose** ("Runs the '<name>' workflow against <domain> and returns the
extracted data as JSON."), sets `spec_status=ready` with `x-llm-enriched: false` in the spec, and
logs. Spec generation must never block publishing. A "Regenerate docs" button re-enqueues the job.

---

## 7. Auth & sessions

Flow (authorization-code; the registered redirect URI works because Vite proxies `/api` → FastAPI):

1. `GET /api/auth/login` → 302 to Google (`client_id`, `redirect_uri=http://localhost:3000/api/auth/callback/google`,
   `scope=openid email profile`, random `state` stored in `oauth:state:{state}` EX 600).
2. Google redirects to `/api/auth/callback/google?code&state` — served by **FastAPI** through the
   proxy. Verify state; exchange code (httpx POST to `oauth2.googleapis.com/token`); verify the ID
   token with `google-auth` (`verify_oauth2_token`, checks `aud`/`iss`/expiry).
3. Upsert user by `google_sub`; auto-promote `role=admin` if email ∈ `ADMIN_EMAILS`.
4. Create session: `sid = secrets.token_urlsafe(32)`; `HSET sess:{sid}` (EX 7 d, refreshed on use);
   set cookie `ab_session` HttpOnly, SameSite=Lax, Path=/ (Secure off for localhost). 302 → `/dashboard`.
5. `GET /api/me` → profile + effective tier + today's quota usage. `POST /api/auth/logout` deletes the key.

Vite config (also gives the WS bridge same-origin cookies):

```ts
// vite.config.ts
export default defineConfig({
  plugins: [react(), tailwindcss()],           // @tailwindcss/vite — v4, CSS-first, no tailwind.config.js
  server: {
    port: 3000,                                 // must match the registered OAuth origin
    proxy: { "/api": { target: "http://localhost:8000", ws: true } },
  },
});
```

Never commit the client secret; `.env` is gitignored, `.env.example` carries placeholders.

---

## 8. Subscriptions, quotas & tier enforcement

| | Free | Pro (৳100/mo) | Max (৳500/mo) |
|---|---|---|---|
| API creation attempts/day | 5 | 50 | unlimited |
| Visibility ceiling | private | shared | shared |
| Invites + paid access | — | ✓ | ✓ |

- **Effective tier** = tier of the user's `active` subscription row with `expires_at > now()`, else
  `free`. Computed in one dependency (`deps.current_user_with_tier`), cached per-request.
- **Creation quota**: an *attempt* = starting a recording session. Dependency `require_creation_quota`
  INCRs `quota:create:{uid}:{YYYYMMDD}` (date computed in `Asia/Dhaka`); if the result exceeds the
  tier limit → DECR back and 429 with a friendly payload. Postgres (workflows created today) is the
  fallback truth if Redis was flushed.
- **Sharing gates**: setting `visibility=shared`, creating invites, and setting `price_bdt` require
  tier ∈ {pro, max}. At **call time**, `/v1/run/{slug}` re-checks the *owner's current* tier for
  non-owner callers (decision #6: lapsed Pro → shared APIs 403 for others, self-heal on renewal).
- **Renewal/upgrade**: verified payment for the same tier extends `expires_at` +30 d; for a
  different tier cancels the active row and inserts a new 30-day row. No proration (documented).
- Expiry sweep runs in the worker's periodic task (every 10 min).

---

## 9. Payments — manual bKash verification

### 9.1 Flow

```
User clicks "Upgrade to Pro"
→ POST /api/billing/intents {purpose, plan_tier|api_id} → PaymentTransaction(status=pending,
  amount_expected=plan price or api price) → UI shows: send ৳100 to BKASH_RECEIVE_MSISDN
  ("Send Money"), then paste your TrxID.  Intent expires after 24 h (sweep).
→ User submits TrxID: POST /api/billing/intents/{id}/submit-trx {trx_id}
  → normalize (uppercase/strip) → status=submitted → run matcher (§9.3)
Meanwhile, admin phone runs an SMS-forwarder app (e.g. "SMS Forwarder" / Macrodroid) that POSTs
every incoming bKash SMS to the webhook.
```

### 9.2 Webhook

```
POST /api/webhooks/bkash-sms
Headers: X-Webhook-Token: <SMS_WEBHOOK_TOKEN>       ← 401 otherwise
Body: {"from": "bKash", "text": "You have received Tk 100.00 from 01712345678. ... TrxID 9AB7CXXXX ...", "received_at": "..."}
```

Store the raw receipt always (dedupe on `sha256(text + minute-bucket)` — forwarders double-send);
parse best-effort:

```python
AMOUNT = re.compile(r"Tk\s*([\d,]+(?:\.\d{1,2})?)")
TRX    = re.compile(r"TrxID\s*:?\s*([A-Z0-9]{8,12})", re.I)
MSISDN = re.compile(r"from\s+(01\d{9})")
```

bKash wording varies by SMS type; unparsed receipts still appear in the admin feed. After insert,
run the matcher.

### 9.3 Matcher (runs on both trx submission and SMS arrival — order-independent)

Inside one DB transaction (`SELECT ... FOR UPDATE` on the payment row):
match `payment_transactions.status='submitted'` ↔ unmatched receipt on
`upper(trx_id) = upper(parsed_trx_id)` **and** `parsed_amount >= amount_expected`.
On match: payment → `verified` (method `auto_sms`, link `matched_sms_id`, store `amount_received`);
receipt → linked; then **apply effects**: activate/extend subscription (§8) or create the
`ApiAccessGrant` (purchase). If amount < expected: leave `submitted`, set `note` — admin decides.
The unique constraint on `bkash_trx_id` prevents replaying someone else's TrxID.

### 9.4 Admin (manual path — required feature)

`/admin` (role=admin): pending/submitted transactions table (approve → same "apply effects" path
with method `manual_admin`; reject with note), raw SMS feed with parse/match status, user list with
tier override. Manual verify works with the webhook entirely absent — the webhook is an
accelerator, not a dependency.

---

## 10. Sharing & monetization

- **Invite**: owner (Pro+) creates `ApiInvite` → link `/invite/{token}`. Visitor logs in →
  `POST /api/invites/{token}/accept`: if `price_bdt` unset/0 → grant immediately (`invite`); else →
  create an `api_access` payment intent for that API and route through §9; grant on verify (`purchase`).
- **Access management**: owner lists/revokes grants and invites on the API detail page.
- **Consumption**: grantee creates an `ApiKey` (self-service), calls `/v1/run/{slug}`, views docs at
  `/docs/{slug}` (React page embedding Scalar API Reference against `/v1/apis/{slug}/openapi.json`).
- Grant check at call time: owner always; else non-revoked, non-expired grant **and** owner tier
  still ∈ {pro, max} **and** `is_active`.
- Creator revenue is settled outside the system in v1 (admin receives all bKash payments); note in UI.

---

## 11. Internal route map (`/api`, session cookie)

```
auth:      GET  /auth/login · GET /auth/callback/google · POST /auth/logout
me:        GET  /me · PATCH /me/settings
recording: POST /recordings · WS /ws/recordings/{workflow_id}
workflows: GET/PATCH/DELETE /workflows[/{id}] · POST /workflows/{id}/publish → CustomApi
apis:      GET  /apis (owned + granted) · GET/PATCH /apis/{id} (visibility, price, cache_ttl,
           is_active) · POST /apis/{id}/regenerate-spec · GET /apis/{id}/executions
           POST /apis/{id}/invites · GET/DELETE /apis/{id}/grants[/{gid}] · POST /invites/{token}/accept
keys:      GET/POST/DELETE /keys[/{id}]
billing:   GET /billing/plans · POST /billing/intents · POST /billing/intents/{id}/submit-trx ·
           GET /billing/mine
admin:     GET /admin/transactions · POST /admin/transactions/{id}/verify|reject ·
           GET /admin/sms · GET /admin/users · PATCH /admin/users/{id}
webhooks:  POST /webhooks/bkash-sms (token header, no session)
```

Public surface is §5.1.

---

## 12. Frontend

Vite + React + TypeScript, Tailwind **v4** (`@import "tailwindcss";` in `index.css`, `@tailwindcss/vite`
plugin, **no** `tailwind.config.js`), TanStack Query for server state, `react-router-dom`.

Pages: `Landing`, `Dashboard` (APIs, quota meter, tier badge), `Recorder`, `WorkflowEditor`
(steps/params/extraction post-hoc editing), `ApiDetail` (spec status, keys hint, invites, grants,
pricing, executions log), `ApiDocs` (Scalar embed via CDN — skip heavy React wrappers), `Billing`,
`Settings` (saved-logins toggle + explanation), `InviteAccept`, `Admin{Transactions,Sms,Users}`.

Recorder page layout: left = live step list (icons per type, undo, "make parameter" on fill steps);
right = mode toggle (Record / Pick data), pick results & extraction builder (root + fields table),
"Test extraction" preview (JSON viewer), Save dialog. Banner: "Interact with the Chromium window
that just opened — this panel updates live." with a "Bring window to front" button.
`useRecorder(workflowId)` hook wraps the WS: auto-reconnect, message → reducer, typed send helpers.

---

## 13. Configuration (`.env.example`)

```env
# infra
DATABASE_URL=postgresql+asyncpg://apibuilder:apibuilder@localhost:5432/apibuilder
REDIS_URL=redis://localhost:6379/0
# auth
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=changeme
OAUTH_REDIRECT_URI=http://localhost:3000/api/auth/callback/google
FRONTEND_ORIGIN=http://localhost:3000
SESSION_SECRET=changeme-random-32-bytes
ADMIN_EMAILS=whaider2002@gmail.com
# security
AUTH_STATE_FERNET_KEY=changeme-generate-with-Fernet.generate_key()
# billing
BKASH_RECEIVE_MSISDN=01XXXXXXXXX
SMS_WEBHOOK_TOKEN=changeme-random
PLAN_PRICE_PRO_BDT=100
PLAN_PRICE_MAX_BDT=500
QUOTA_TZ=Asia/Dhaka
# llm
LLM_PROVIDER=gemini
GEMINI_API_KEY=your-aistudio-key
GEMINI_MODEL=gemini-flash-lite-latest
LLM_ENABLED=true
# worker
REC_MAX_CONCURRENCY=1
EXEC_MAX_CONCURRENCY=2
EXEC_TIMEOUT_SECONDS=90
SYNC_WAIT_SECONDS=55
PROFILES_DIR=./data/profiles
FAILURES_DIR=./data/failures
```

`docker-compose.yml`: `postgres:16` (user/pass/db `apibuilder`, volume, healthcheck
`pg_isready`) + `redis:7-alpine` (`--appendonly yes`). Nothing else.

Backend deps (`uv`, Python 3.12): fastapi, uvicorn[standard], sqlalchemy[asyncio], asyncpg,
alembic, pydantic-settings, redis, playwright, openai, httpx, google-auth, requests, cryptography,
openapi-spec-validator, genson. Dev: pytest, pytest-asyncio, ruff.

`requests` isn't used directly — `google-auth`'s ID-token verification (`google.auth.transport.requests`)
requires it as its default HTTP transport for fetching Google's signing certs.

---

## 14. Directory structure

```
API Builder V3/
├─ docker-compose.yml
├─ .env  .env.example  .gitignore          # ignore: .env, data/, node_modules, __pycache__
├─ CLAUDE.md
├─ docs/            BLUEPRINT.md  IMPLEMENTATION_PLAN.md
├─ scripts/         dev.ps1  e2e.ps1
├─ data/            profiles/  failures/    (gitignored)
├─ backend/
│  ├─ pyproject.toml  alembic.ini  alembic/versions/
│  ├─ app/
│  │  ├─ main.py            # app factory; mounts /api (session) and /v1 (public)
│  │  ├─ config.py db.py redis.py
│  │  ├─ models/            base.py user.py billing.py workflow.py api.py execution.py
│  │  ├─ schemas/           # pydantic v2 request/response models, mirrors routers
│  │  ├─ api/               auth.py me.py recordings.py ws.py workflows.py apis.py keys.py
│  │  │                     invites.py billing.py admin.py webhooks.py public.py
│  │  ├─ core/              security.py deps.py
│  │  ├─ services/          quota.py plans.py publish.py payments.py sms_matcher.py grants.py cache.py
│  │  ├─ recorder/          session.py injected.js selectors.py dsl.py replay.py
│  │  │                     extraction.py schema_infer.py profiles.py
│  │  ├─ llm/               client.py spec_builder.py enrich.py prompts.py
│  │  └─ workers/           main.py handlers.py periodic.py
│  └─ tests/
│     ├─ fixtures/site/     # static HTML pages served locally → deterministic replay tests
│     └─ test_{quota,sms_matcher,dsl_replay,spec_builder,selectors}.py
└─ frontend/
   ├─ package.json  vite.config.ts  index.html
   └─ src/
      ├─ main.tsx App.tsx routes.tsx index.css
      ├─ lib/       api.ts ws.ts types.ts
      ├─ hooks/     useSession.ts useRecorder.ts
      ├─ pages/     …(§12)
      └─ components/ recorder/ apis/ billing/ admin/ ui/
```

---

## 15. Known risks & explicit non-goals (v1)

- **Anti-bot defenses**: some sites (Cloudflare-protected, heavy fingerprinting) will block
  headless replays even when recording worked. Mitigations already in the design: real
  `channel="chrome"` recording, storage_state auth, realistic UA. Captcha solving is a non-goal;
  surface a clear 502 with the failure screenshot.
- **Selector drift**: sites change markup; the 3-candidate fallback absorbs small changes. LLM-based
  self-healing is a v2 idea. The failure artifacts make re-recording easy to justify.
- **ToS/legality**: scraping may violate target-site terms; this is a course project — add a visible
  disclaimer and per-API `robots`-style responsibility note on creation.
- **Multi-tab/popup flows, iframes-heavy sites, infinite pagination**: partial or no support; the
  recorder should warn when it detects them.
- **Single-machine scale**: the queue design scales to multiple workers later, but v1 assumes one
  laptop; don't add distributed complexity.
- **Payment edge cases**: users sending the wrong amount or reusing TrxIDs are handled (§9.3), but
  refunds/chargebacks are manual/out of scope.

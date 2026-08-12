# Autonomous API Authoring ("Agent Builder") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user describe an API in natural language and have an LLM agent drive a browser to produce a published, parameterized JSON API, with no manual recording.

**Architecture:** The agent is a *second producer* of the existing `Workflow` artifact, not a second system. It drives a live `RecordingSession`, so the already-injected recorder captures every step with ranked selector candidates — the agent never authors a selector. A workflow is promoted to `ready` only after it replays correctly against a parameter value the agent never drove with. Per-call replay stays fully deterministic; no LLM runs in the hot path.

**Tech Stack:** Python 3.12 · FastAPI · SQLAlchemy 2.0 async + asyncpg · Alembic · Playwright (worker-only) · Redis (Streams + pub/sub) · OpenAI-compatible LLM client (Gemini default) · React + Vite + Tailwind v4 · pytest / pytest-asyncio

**Spec:** [2026-08-13-autonomous-api-authoring-design.md](../specs/2026-08-13-autonomous-api-authoring-design.md)

## Global Constraints

- **Playwright runs ONLY in the worker process.** Never in FastAPI, never in Docker.
- **FastAPI ↔ worker communicate ONLY via Redis.** Streams for jobs, pub/sub for live events. The WS endpoint is a dumb bridge.
- **SQLAlchemy 2.0 typed style, async + asyncpg.** Enums via `enum_column(...)` storing `.value` (`native_enum=False`). JSONB columns are **replaced, never mutated in place**. Money is `Numeric(10,2)` BDT. Timestamps are UTC tz-aware.
- **Credential redaction is mandatory on every LLM path.** Drop any value whose selector matches `password|passwd|pwd|otp|pin|cvv|secret` (case-insensitive); cap every literal sent to the model at 120 chars. This applies to plan, distill, and repair.
- **Secrets only via `app/config.py` / `.env`.** Never commit `.env` or `data/`.
- **Wallet debits use an atomic conditional UPDATE**, never read-then-write. `wallet.debit` / `wallet.credit` do **not** commit — the caller owns the transaction boundary.
- **Super admins bypass every quota and payment gate** via an explicit `is_super` branch, never by faking a tier.
- **No external-network tests.** Everything runs against `tests/fixtures/site`, served by a static `SimpleHTTPRequestHandler` — so fixture behavior must be **client-side JS**, not server-side. The one exception is an opt-in integration test.
- **Tests run against real Postgres/Redis** (`apibuilder_test` DB, Redis db index 1) via the existing `conftest.py` fixtures.
- **LLM parsing is defensive.** Always route model output through `_extract_json`; never assume clean JSON.
- Lint and test clean before every commit: `cd backend; uv run ruff check app` and `uv run pytest`.

## Reference target

`waltonbd.com` — used only in the opt-in integration test (Task 19). Every other test uses the local fixture site.

## Phasing

| Phase | Tasks | Ships |
|---|---|---|
| 0 — Prerequisites | 1–2 | URL templating (also unblocks a known manual-recorder limitation) + tool-calling LLM primitive |
| 1 — Data & money | 3–5 | `AgentRun` model, plan settings, debit/refund service |
| 2 — Perception & actuation | 6–7 | `observe()` and ref-based tool dispatch |
| 3 — Pipeline | 8–13 | plan → drive → distill → extract → verify → repair |
| 4 — Wiring | 14–15 | worker job + FastAPI routes |
| 5 — Frontend | 16 | the new page |
| 6 — Hardening | 17–19 | concurrency, redaction, opt-in integration |

**Phase 0 is independently shippable** and valuable on its own.

---

# Phase 0 — Prerequisites

### Task 1: URL templating in replay

Today `_resolve_value` substitutes parameters into *step values* only. A `goto` URL is replayed literally, so a parameter baked into a URL is frozen.

`session.py`'s `NAV_AFTER_INTERACTION_WINDOW_S` already suppresses the side-effect `goto` that follows a click/press, which protects the *human* recording path. It does **not** protect the agent's `navigate(url)` tool, which produces a standalone `goto`. Without this task the agent can produce an API that ignores its own parameter.

**Files:**
- Modify: `backend/app/recorder/replay.py` (add `_resolve_url`, use it at the `goto` branch, ~line 423)
- Modify: `backend/tests/fixtures/site/index.html` (add a client-side search page)
- Create: `backend/tests/fixtures/site/search.html`
- Test: `backend/tests/test_replay_url_params.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `_resolve_url(url: str, params: dict) -> str` in `app.recorder.replay`. A `goto` step may carry `url_template: str` containing `{param_name}` placeholders; when present it takes precedence over `url`.

- [ ] **Step 1: Add the fixture search page**

Create `backend/tests/fixtures/site/search.html`. The test server is a static file server, so filtering must happen in the browser.

```html
<!doctype html>
<meta charset="utf-8">
<title>Fixture Store — Search</title>
<body>
  <form id="search-form" onsubmit="event.preventDefault(); go();">
    <input id="q" name="q" type="text" placeholder="Search products">
    <button type="submit">Search</button>
  </form>
  <ul id="results"></ul>
<script>
const CATALOG = [
  {name: "Blue Refrigerator", price: "45000", cat: "refrigerator"},
  {name: "Silver Refrigerator", price: "52000", cat: "refrigerator"},
  {name: "Smart Television", price: "38000", cat: "television"},
  {name: "Basic Television", price: "21000", cat: "television"},
  {name: "Desk Fan", price: "3200", cat: "fan"},
];
function go() {
  const q = document.getElementById("q").value;
  location.search = "?q=" + encodeURIComponent(q);
}
function render() {
  const q = (new URLSearchParams(location.search).get("q") || "").toLowerCase();
  document.getElementById("q").value = q;
  const hits = q ? CATALOG.filter(p => p.cat.includes(q) || p.name.toLowerCase().includes(q)) : [];
  document.getElementById("results").innerHTML = hits.map(p =>
    `<li class="product"><span class="title">${p.name}</span>` +
    `<span class="price">${p.price}</span></li>`).join("");
}
render();
</script>
</body>
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_replay_url_params.py`:

```python
import uuid

import pytest

from app.recorder.replay import _resolve_url, replay_workflow


def test_resolve_url_substitutes_named_param():
    assert _resolve_url("/search?q={query}", {"query": "television"}) == "/search?q=television"


def test_resolve_url_url_encodes_the_value():
    assert _resolve_url("/search?q={query}", {"query": "smart tv"}) == "/search?q=smart%20tv"


def test_resolve_url_leaves_unknown_placeholders_alone():
    assert _resolve_url("/search?q={missing}", {}) == "/search?q={missing}"


def test_resolve_url_without_placeholders_is_identity():
    assert _resolve_url("/search?q=fixed", {"query": "x"}) == "/search?q=fixed"


@pytest.mark.asyncio
async def test_replay_substitutes_param_into_goto_url(fixture_site_url):
    snapshot = {
        "steps": [
            {"type": "goto", "url": f"{fixture_site_url}/search.html?q=refrigerator",
             "url_template": f"{fixture_site_url}/search.html?q={{query}}"},
            {"type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "list",
                "root": "li.product",
                "fields": [
                    {"name": "title", "selectors": [".title"], "take": "text"},
                    {"name": "price", "selectors": [".price"], "take": "text"},
                ],
            }
        },
    }
    result = await replay_workflow(
        snapshot, {"query": "television"}, None, uuid.uuid4(), headless=True
    )
    titles = [row["title"] for row in result["data"]]
    assert titles == ["Smart Television", "Basic Television"]
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_replay_url_params.py -v`
Expected: FAIL — `ImportError: cannot import name '_resolve_url'`

- [ ] **Step 4: Implement `_resolve_url`**

In `backend/app/recorder/replay.py`, add below `_resolve_value` (after line 139):

```python
_URL_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _resolve_url(url: str, params: dict) -> str:
    """Substitutes {param} placeholders in a goto URL with URL-encoded values.

    Unlike _resolve_value (which owns a whole step value), a URL is a mix of
    literal text and placeholders, so this is a targeted regex substitution.
    Unknown placeholders are left verbatim rather than blanked: a URL with a
    hole in it fails loudly at navigation instead of silently fetching the
    wrong page.
    """
    def _sub(match: re.Match) -> str:
        name = match.group(1)
        if name not in params:
            return match.group(0)
        return quote(str(params[name]), safe="")

    return _URL_PLACEHOLDER_RE.sub(_sub, url)
```

Add the imports at the top of the file (`re` may already be imported — check before adding):

```python
import re
from urllib.parse import quote
```

- [ ] **Step 5: Use it at the `goto` branch**

In `replay_workflow`, replace the `goto` branch (currently line 423):

```python
                if stype == "goto":
                    target = step.get("url_template") or step["url"]
                    await page.goto(_resolve_url(target, params), wait_until="domcontentloaded")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_replay_url_params.py -v`
Expected: PASS (5 passed)

- [ ] **Step 7: Run the full suite for regressions**

Run: `cd backend; uv run pytest tests/test_replay.py tests/test_api_parameters.py -v`
Expected: PASS — existing `goto` steps have no `url_template` and no `{}` placeholders, so `_resolve_url` is identity for them.

- [ ] **Step 8: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/recorder/replay.py backend/tests/test_replay_url_params.py backend/tests/fixtures/site/search.html
git commit -m "feat(replay): substitute parameters into goto URLs

Adds _resolve_url so a goto step can carry a url_template with {param}
placeholders. Closes the gap noted in AI_AUTHORING_PLAN.md that kept URL
query params from becoming API parameters, and is a prerequisite for
autonomous authoring, where the agent reaches results by navigating
directly to a search URL."
```

---

### Task 2: Tool-calling LLM primitive

`complete_json` is single-shot: prompt in, one JSON object out. It already supports base64 image parts (proven by `tests/test_llm_multimodal.py`). An agent loop additionally needs **multi-turn conversation with tool calls**.

**Files:**
- Modify: `backend/app/llm/client.py`
- Test: `backend/tests/test_llm_tools.py`

**Interfaces:**
- Consumes: `_extract_json` (existing, `app.llm.client`).
- Produces, in `app.llm.client`:
  - `@dataclass ToolCall: id: str, name: str, arguments: dict`
  - `@dataclass TurnResult: tool_calls: list[ToolCall], text: str | None, usage_tokens: int`
  - `async def complete_tools(system: str, messages: list[dict], tools: list[dict], max_tokens: int = 4000) -> TurnResult`
  - `def user_message(text: str, screenshot_b64: str | None = None) -> dict`
  - `def tool_result_message(tool_call_id: str, content: str) -> dict`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_llm_tools.py`:

```python
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.llm.client import (
    ToolCall,
    complete_tools,
    tool_result_message,
    user_message,
)


def _fake_response(tool_calls=None, content=None, total_tokens=42):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(total_tokens=total_tokens),
        model_dump=lambda: {},
    )


def _fake_tool_call(call_id, name, arguments_json):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments_json),
    )


@pytest.mark.asyncio
async def test_complete_tools_parses_tool_calls():
    resp = _fake_response(tool_calls=[_fake_tool_call("c1", "click", '{"ref": "ref_3"}')])
    with patch("app.llm.client.client.chat.completions.create", AsyncMock(return_value=resp)):
        result = await complete_tools("sys", [user_message("go")], [{"type": "function"}])

    assert result.tool_calls == [ToolCall(id="c1", name="click", arguments={"ref": "ref_3"})]
    assert result.usage_tokens == 42


@pytest.mark.asyncio
async def test_complete_tools_parses_fenced_arguments():
    # Some providers wrap tool arguments in a markdown fence; _extract_json must save us.
    resp = _fake_response(tool_calls=[_fake_tool_call("c2", "fill", '```json\n{"ref": "ref_1"}\n```')])
    with patch("app.llm.client.client.chat.completions.create", AsyncMock(return_value=resp)):
        result = await complete_tools("sys", [user_message("go")], [])

    assert result.tool_calls[0].arguments == {"ref": "ref_1"}


@pytest.mark.asyncio
async def test_complete_tools_returns_text_when_no_tool_calls():
    resp = _fake_response(content="all done")
    with patch("app.llm.client.client.chat.completions.create", AsyncMock(return_value=resp)):
        result = await complete_tools("sys", [user_message("go")], [])

    assert result.tool_calls == []
    assert result.text == "all done"


@pytest.mark.asyncio
async def test_complete_tools_raises_on_gateway_error():
    resp = SimpleNamespace(choices=[], model_dump=lambda: {"message": "blocked"})
    with patch("app.llm.client.client.chat.completions.create", AsyncMock(return_value=resp)):
        with pytest.raises(RuntimeError, match="blocked"):
            await complete_tools("sys", [], [])


def test_user_message_without_screenshot_is_plain_text():
    assert user_message("hello") == {"role": "user", "content": "hello"}


def test_user_message_with_screenshot_uses_content_parts():
    msg = user_message("look", screenshot_b64="QUJD")
    assert msg["role"] == "user"
    assert msg["content"][0] == {"type": "text", "text": "look"}
    assert msg["content"][1]["image_url"]["url"] == "data:image/png;base64,QUJD"


def test_tool_result_message_shape():
    assert tool_result_message("c1", "ok") == {
        "role": "tool", "tool_call_id": "c1", "content": "ok",
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_llm_tools.py -v`
Expected: FAIL — `ImportError: cannot import name 'ToolCall'`

- [ ] **Step 3: Implement the primitive**

Append to `backend/app/llm/client.py`:

```python
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class TurnResult:
    tool_calls: list[ToolCall]
    text: str | None
    usage_tokens: int


def user_message(text: str, screenshot_b64: str | None = None) -> dict:
    """One user turn, optionally carrying a screenshot as an inline image part.
    Mirrors the image handling complete_json already uses."""
    if screenshot_b64 is None:
        return {"role": "user", "content": text}
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
        ],
    }


def tool_result_message(tool_call_id: str, content: str) -> dict:
    return {"role": "tool", "tool_call_id": tool_call_id, "content": content}


async def complete_tools(
    system: str,
    messages: list[dict],
    tools: list[dict],
    max_tokens: int = 4000,
) -> TurnResult:
    """One turn of a tool-calling conversation.

    The caller owns the message list and appends to it across turns. Tool call
    arguments arrive as a JSON *string* and, like every other structured output
    from these providers, can be fence- or think-wrapped — so they go through
    _extract_json rather than a bare json.loads.
    """
    kwargs: dict = {
        "model": MODEL_NAME,
        "messages": [{"role": "system", "content": system}, *messages],
        "temperature": 0.2,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    resp = await client.chat.completions.create(**kwargs)

    if not resp.choices:
        extra = resp.model_dump()
        detail = extra.get("message") or extra.get("error") or "gateway returned no choices"
        raise RuntimeError(f"LLM gateway error: {detail}")

    message = resp.choices[0].message
    calls: list[ToolCall] = []
    for raw in (getattr(message, "tool_calls", None) or []):
        try:
            arguments = _extract_json(raw.function.arguments)
        except ValueError:
            # A malformed argument payload is one bad turn, not a dead run —
            # surface it as an empty-argument call so the loop can tell the
            # model it failed rather than crashing the session.
            arguments = {}
        calls.append(ToolCall(id=raw.id, name=raw.function.name, arguments=arguments))

    usage = getattr(resp, "usage", None)
    return TurnResult(
        tool_calls=calls,
        text=message.content,
        usage_tokens=getattr(usage, "total_tokens", 0) or 0,
    )
```

Add to the imports at the top of the file:

```python
from dataclasses import dataclass
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_llm_tools.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Verify existing LLM tests still pass**

Run: `cd backend; uv run pytest tests/test_llm_client.py tests/test_llm_provider.py tests/test_llm_multimodal.py tests/test_llm_extract_json.py -v`
Expected: PASS — `complete_json` is untouched.

- [ ] **Step 6: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/llm/client.py backend/tests/test_llm_tools.py
git commit -m "feat(llm): add multi-turn tool-calling primitive

complete_tools sits alongside complete_json for agent loops that need
tool calls across turns. Tool arguments are parsed through _extract_json
because providers fence-wrap them the same way they wrap JSON responses."
```

---

# Phase 1 — Data & money

### Task 3: `AgentRun` model and migration

**Files:**
- Create: `backend/app/models/agent_run.py`
- Modify: `backend/app/models/__init__.py`
- Modify: `backend/app/models/workflow.py` (add `agent_run_id`)
- Create: `backend/alembic/versions/<hash>_add_agent_runs.py`
- Test: `backend/tests/test_agent_run_model.py`

**Interfaces:**
- Consumes: `Base`, `TimestampMixin`, `enum_column` from `app.models.base`.
- Produces: `AgentRun`, `AgentRunStatus` in `app.models.agent_run`, re-exported from `app.models`. `Workflow.agent_run_id: uuid.UUID | None`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_agent_run_model.py`:

```python
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun, AgentRunStatus


@pytest.mark.asyncio
async def test_agent_run_defaults(db: AsyncSession, make_user):
    user = await make_user()
    run = AgentRun(user_id=user.id, prompt="search walton for fridges")
    db.add(run)
    await db.commit()
    await db.refresh(run)

    assert run.status == AgentRunStatus.PLANNING
    assert run.attempt == 0
    assert run.plan == {}
    assert run.transcript == []
    assert run.workflow_id is None
    assert run.token_usage == 0


@pytest.mark.asyncio
async def test_agent_run_status_persists_as_value(db: AsyncSession, make_user):
    from sqlalchemy import text

    user = await make_user()
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.VERIFYING)
    db.add(run)
    await db.commit()

    raw = await db.execute(text("SELECT status FROM agent_runs WHERE id = :i"), {"i": run.id})
    assert raw.scalar_one() == "verifying"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_run_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.agent_run'`

- [ ] **Step 3: Create the model**

Create `backend/app/models/agent_run.py`:

```python
import enum
import uuid

from sqlalchemy import ForeignKey, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, enum_column


class AgentRunStatus(str, enum.Enum):
    PLANNING = "planning"
    AWAITING_CONFIRM = "awaiting_confirm"
    DRIVING = "driving"
    DISTILLING = "distilling"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


TERMINAL_STATUSES = {AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED}


class AgentRun(Base, TimestampMixin):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Nullable: a run that fails before distilling never produces a workflow.
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workflows.id", ondelete="SET NULL"), index=True)

    prompt: Mapped[str] = mapped_column(Text)
    resolved_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AgentRunStatus] = mapped_column(
        enum_column(AgentRunStatus), default=AgentRunStatus.PLANNING, index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    # Declared parameters (with drive/verify values) and output fields, set by
    # the plan phase before the browser opens. JSONB: replace, never mutate.
    plan: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))
    transcript: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))

    failure_reason: Mapped[str | None] = mapped_column(Text)
    token_usage: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
```

- [ ] **Step 4: Register the model and add the workflow back-reference**

In `backend/app/models/__init__.py`, add the import (keep alphabetical placement — before `app.models.api`):

```python
from app.models.agent_run import AgentRun, AgentRunStatus
```

and add to `__all__`:

```python
    "AgentRun",
    "AgentRunStatus",
```

In `backend/app/models/workflow.py`, add to the `Workflow` class after `auth_state_encrypted`:

```python
    # Set when this workflow was produced by an autonomous agent run rather
    # than a human recording. Purely so the UI can badge it; nothing in the
    # replay or publish path reads it.
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
```

> Note: deliberately **not** a ForeignKey — `agent_runs.workflow_id` already points the other way, and a mutual FK creates a cycle that `TRUNCATE ... CASCADE` in `conftest.py` handles but Alembic ordering does not.

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend; uv run pytest tests/test_agent_run_model.py -v`
Expected: PASS (2 passed) — `conftest.py`'s `create_all` builds the table for tests.

- [ ] **Step 6: Generate and inspect the migration**

```bash
cd backend && uv run alembic revision --autogenerate -m "add agent runs"
```

Open the generated file in `backend/alembic/versions/`. Confirm it creates `agent_runs` with a `sa.String` status column (not a native enum) and adds `workflows.agent_run_id`. Remove any unrelated autogenerated drift.

- [ ] **Step 7: Apply and verify the migration**

```bash
cd backend && uv run alembic upgrade head
```
Expected: no error. Then confirm round-tripping:
```bash
cd backend && uv run alembic downgrade -1 && uv run alembic upgrade head
```
Expected: no error.

- [ ] **Step 8: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/models/agent_run.py backend/app/models/__init__.py backend/app/models/workflow.py backend/alembic/versions/ backend/tests/test_agent_run_model.py
git commit -m "feat(agent): add AgentRun model

Tracks an autonomous authoring run independently of the workflow it
produces, since a failed run has no workflow and the run is the key for
the wallet debit and its refund."
```

---

### Task 4: Plan settings for agent runs

**Files:**
- Modify: `backend/app/models/plan_settings.py`
- Modify: `backend/app/services/plans.py`
- Create: `backend/alembic/versions/<hash>_add_agent_plan_settings.py`
- Test: `backend/tests/test_agent_plan_settings.py`

**Interfaces:**
- Consumes: existing `PlanSettings` model and `app.services.plans` accessors.
- Produces: `PlanSettings.agent_run_price_bdt: Decimal`, `PlanSettings.agent_runs_per_day: int`, and `plans.agent_run_price(tier)` / `plans.agent_runs_per_day(tier)`.

- [ ] **Step 1: Read the existing plan settings module**

Run: `cd backend; cat app/models/plan_settings.py app/services/plans.py`

Note the exact column style, the seeded defaults, and how existing accessors read a tier's row. **Follow that pattern exactly** in the steps below; the code shown here uses the column names and idioms visible in `plan_settings.py` and must be adapted if they differ.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_agent_plan_settings.py`:

```python
from decimal import Decimal

import pytest

from app.models.billing import PlanTier
from app.services import plans


@pytest.mark.asyncio
async def test_free_tier_gets_no_agent_runs(db):
    await plans.ensure_seeded(db)
    assert await plans.agent_runs_per_day(PlanTier.FREE, db) == 0


@pytest.mark.asyncio
async def test_pro_and_max_get_agent_runs(db):
    await plans.ensure_seeded(db)
    assert await plans.agent_runs_per_day(PlanTier.PRO, db) > 0
    assert await plans.agent_runs_per_day(PlanTier.MAX, db) > 0


@pytest.mark.asyncio
async def test_agent_run_price_is_positive_money(db):
    await plans.ensure_seeded(db)
    price = await plans.agent_run_price(PlanTier.PRO, db)
    assert isinstance(price, Decimal)
    assert price > 0
```

> If `plans` exposes seeding under a different name than `ensure_seeded`, use the real name found in Step 1.

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_plan_settings.py -v`
Expected: FAIL — `AttributeError: module 'app.services.plans' has no attribute 'agent_runs_per_day'`

- [ ] **Step 4: Add the columns**

In `backend/app/models/plan_settings.py`, add to the `PlanSettings` class, matching the surrounding column style:

```python
    # Autonomous authoring: 0 runs/day disables the feature for a tier, which
    # is how Free is gated. Price is charged per attempt and refunded when the
    # run ends failed.
    agent_runs_per_day: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"))
    agent_run_price_bdt: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0.00"), server_default=text("0.00"))
```

Add `Integer`, `Numeric`, `text` to the SQLAlchemy imports and `Decimal` from `decimal` if not already present.

- [ ] **Step 5: Seed the defaults**

In the seeding routine in `backend/app/services/plans.py`, set for each tier:

| Tier | `agent_runs_per_day` | `agent_run_price_bdt` |
|---|---|---|
| free | `0` | `0.00` |
| pro | `5` | `10.00` |
| max | `25` | `10.00` |

- [ ] **Step 6: Add the accessors**

Append to `backend/app/services/plans.py`, matching the signature style of the existing accessors:

```python
async def agent_runs_per_day(tier: PlanTier, db: AsyncSession) -> int:
    row = await _settings_for(tier, db)
    return row.agent_runs_per_day


async def agent_run_price(tier: PlanTier, db: AsyncSession) -> Decimal:
    row = await _settings_for(tier, db)
    return row.agent_run_price_bdt
```

> `_settings_for` is a placeholder for whatever the module's existing per-tier lookup helper is called (found in Step 1). Reuse it; do not add a second lookup path.

- [ ] **Step 7: Run the test to verify it passes**

Run: `cd backend; uv run pytest tests/test_agent_plan_settings.py -v`
Expected: PASS (3 passed)

- [ ] **Step 8: Migration, regression check, lint, commit**

```bash
cd backend && uv run alembic revision --autogenerate -m "add agent plan settings"
cd backend && uv run alembic upgrade head
cd backend && uv run pytest tests/test_plans.py tests/test_tier.py -v
cd backend && uv run ruff check app
```
Expected: migration applies, existing plan tests PASS.

```bash
git add backend/app/models/plan_settings.py backend/app/services/plans.py backend/alembic/versions/ backend/tests/test_agent_plan_settings.py
git commit -m "feat(plans): add runtime-editable agent run price and daily cap

Free gets 0 runs/day, which is how the Pro/Max gate is expressed —
no separate tier check to keep in sync."
```

---

### Task 5: Agent run service — create, charge, refund

**Files:**
- Create: `backend/app/services/agent_runs.py`
- Modify: `backend/app/models/wallet.py` (two new reason constants)
- Test: `backend/tests/test_agent_run_billing.py`

**Interfaces:**
- Consumes: `wallet.debit` / `wallet.credit` (`app.services.wallet`), `plans.agent_run_price` / `plans.agent_runs_per_day` (Task 4), `AgentRun` / `AgentRunStatus` (Task 3).
- Produces, in `app.services.agent_runs`:
  - `REASON_AGENT_DEBIT = "agent_debit"`, `REASON_AGENT_REFUND = "agent_refund"` (defined in `app.models.wallet`)
  - `async def create_run(user: User, prompt: str, db: AsyncSession) -> AgentRun` — charges, does **not** commit
  - `async def finish_run(run: AgentRun, *, succeeded: bool, reason: str | None, db: AsyncSession) -> None` — refunds on failure, does **not** commit
  - `class AgentRunNotAllowed(Exception)`

- [ ] **Step 1: Add the ledger reasons**

In `backend/app/models/wallet.py`, after `REASON_CALL_REFUND` (line 21):

```python
REASON_AGENT_DEBIT = "agent_debit"
REASON_AGENT_REFUND = "agent_refund"
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_agent_run_billing.py`:

```python
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.agent_run import AgentRunStatus
from app.models.billing import PlanTier
from app.models.wallet import REASON_AGENT_DEBIT, REASON_AGENT_REFUND, WalletLedger
from app.services import agent_runs, plans, wallet


@pytest.mark.asyncio
async def test_create_run_debits_the_wallet(db, make_user):
    await plans.ensure_seeded(db)
    user = await make_user()
    user.tier_override = PlanTier.PRO
    await wallet.credit(user.id, Decimal("100.00"), "recharge", db)
    await db.commit()

    run = await agent_runs.create_run(user, "search fridges", db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    price = await plans.agent_run_price(PlanTier.PRO, db)
    assert balance == Decimal("100.00") - price
    assert run.status == AgentRunStatus.PLANNING


@pytest.mark.asyncio
async def test_failed_run_is_refunded_in_full(db, make_user):
    await plans.ensure_seeded(db)
    user = await make_user()
    user.tier_override = PlanTier.PRO
    await wallet.credit(user.id, Decimal("100.00"), "recharge", db)
    await db.commit()

    run = await agent_runs.create_run(user, "p", db)
    await db.commit()
    await agent_runs.finish_run(run, succeeded=False, reason="gave up", db=db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    assert balance == Decimal("100.00")
    assert run.status == AgentRunStatus.FAILED
    assert run.failure_reason == "gave up"


@pytest.mark.asyncio
async def test_succeeded_run_keeps_the_charge(db, make_user):
    await plans.ensure_seeded(db)
    user = await make_user()
    user.tier_override = PlanTier.PRO
    await wallet.credit(user.id, Decimal("100.00"), "recharge", db)
    await db.commit()

    run = await agent_runs.create_run(user, "p", db)
    await db.commit()
    await agent_runs.finish_run(run, succeeded=True, reason=None, db=db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    price = await plans.agent_run_price(PlanTier.PRO, db)
    assert balance == Decimal("100.00") - price
    assert run.status == AgentRunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_refund_is_idempotent(db, make_user):
    await plans.ensure_seeded(db)
    user = await make_user()
    user.tier_override = PlanTier.PRO
    await wallet.credit(user.id, Decimal("100.00"), "recharge", db)
    await db.commit()

    run = await agent_runs.create_run(user, "p", db)
    await db.commit()
    await agent_runs.finish_run(run, succeeded=False, reason="x", db=db)
    await db.commit()
    await agent_runs.finish_run(run, succeeded=False, reason="x", db=db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    assert balance == Decimal("100.00")
    refunds = await db.execute(
        select(WalletLedger).where(
            WalletLedger.user_id == user.id, WalletLedger.reason == REASON_AGENT_REFUND
        )
    )
    assert len(refunds.scalars().all()) == 1


@pytest.mark.asyncio
async def test_free_tier_is_rejected(db, make_user):
    await plans.ensure_seeded(db)
    user = await make_user()
    await wallet.credit(user.id, Decimal("100.00"), "recharge", db)
    await db.commit()

    with pytest.raises(agent_runs.AgentRunNotAllowed):
        await agent_runs.create_run(user, "p", db)


@pytest.mark.asyncio
async def test_super_admin_is_not_charged(db, make_user):
    from app.models.user import UserRole

    await plans.ensure_seeded(db)
    user = await make_user()
    user.role = UserRole.SUPER_ADMIN
    await db.commit()

    run = await agent_runs.create_run(user, "p", db)
    await db.commit()

    debits = await db.execute(
        select(WalletLedger).where(
            WalletLedger.user_id == user.id, WalletLedger.reason == REASON_AGENT_DEBIT
        )
    )
    assert debits.scalars().all() == []
    assert run is not None
```

> `tier_override` / `role` are placeholders for however this codebase sets a user's effective tier and super-admin status. Replace with the real mechanism found by reading `app/services/plans.py` and `app/models/user.py` before writing the test.

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_run_billing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.agent_runs'`

- [ ] **Step 4: Implement the service**

Create `backend/app/services/agent_runs.py`:

```python
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.user import User
from app.models.wallet import REASON_AGENT_DEBIT, REASON_AGENT_REFUND, WalletLedger
from app.services import plans, wallet

log = logging.getLogger("agent")


class AgentRunNotAllowed(Exception):
    """The user's tier does not permit autonomous authoring."""


async def create_run(user: User, prompt: str, db: AsyncSession) -> AgentRun:
    """Creates an agent run and takes the wallet charge for it.

    Gate and debit are one operation, matching the per-call metering rule: a
    user who can't pay never gets a run row. Super admins bypass both by an
    explicit branch, never by faking a tier. Does NOT commit.
    """
    is_super = user.is_super_admin
    tier = await plans.effective_tier(user, db)

    if not is_super:
        if await plans.agent_runs_per_day(tier, db) <= 0:
            raise AgentRunNotAllowed(
                "Autonomous authoring requires a Pro or Max plan."
            )

    run = AgentRun(user_id=user.id, prompt=prompt, status=AgentRunStatus.PLANNING)
    db.add(run)
    await db.flush()  # assigns run.id for the ledger row

    if not is_super:
        price = await plans.agent_run_price(tier, db)
        if price > 0:
            # Raises InsufficientBalance, which the caller surfaces as 402.
            await wallet.debit(user.id, price, REASON_AGENT_DEBIT, db)

    return run


async def _already_refunded(run: AgentRun, db: AsyncSession) -> bool:
    result = await db.execute(
        select(WalletLedger).where(
            WalletLedger.user_id == run.user_id,
            WalletLedger.reason == REASON_AGENT_REFUND,
            WalletLedger.counterparty_user_id == run.id,
        )
    )
    return result.first() is not None


async def _debit_amount(run: AgentRun, db: AsyncSession):
    result = await db.execute(
        select(WalletLedger).where(
            WalletLedger.user_id == run.user_id,
            WalletLedger.reason == REASON_AGENT_DEBIT,
            WalletLedger.counterparty_user_id == run.id,
        )
    )
    row = result.scalar_one_or_none()
    return None if row is None else -row.amount_bdt


async def finish_run(
    run: AgentRun, *, succeeded: bool, reason: str | None, db: AsyncSession
) -> None:
    """Terminates a run. A failed run is refunded in full — the user received
    nothing, so charging for it would be charging for the system's inability to
    do the job. Idempotent: a second call never double-refunds. Does NOT commit.
    """
    if run.status in (AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED):
        return

    run.status = AgentRunStatus.SUCCEEDED if succeeded else AgentRunStatus.FAILED
    run.failure_reason = reason

    if succeeded:
        return

    if await _already_refunded(run, db):
        return
    price = await _debit_amount(run, db)
    if price is None or price <= 0:
        return
    await wallet.credit(run.user_id, price, REASON_AGENT_REFUND, db)
```

> `wallet.debit`/`credit` have no `agent_run_id` kwarg. This uses `counterparty_user_id` to carry the run id, which is a **type mismatch to resolve in Step 5** — do not ship it as written.

- [ ] **Step 5: Add a proper ledger link for agent runs**

The ledger has no column for an agent run. Add one rather than overloading `counterparty_user_id`.

In `backend/app/models/wallet.py`, add to `WalletLedger`:

```python
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
```

In `backend/app/services/wallet.py`, add `agent_run_id: uuid.UUID | None = None` to the keyword-only parameters of `_ledger_row`, `debit`, and `credit`, and pass it through to the `WalletLedger(...)` constructor — following exactly how `cashout_request_id` is threaded today.

Then in `agent_runs.py`, replace every `counterparty_user_id == run.id` filter with `WalletLedger.agent_run_id == run.id`, and pass `agent_run_id=run.id` to both `wallet.debit` and `wallet.credit`.

Generate the migration:
```bash
cd backend && uv run alembic revision --autogenerate -m "add agent run id to wallet ledger"
cd backend && uv run alembic upgrade head
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_agent_run_billing.py -v`
Expected: PASS (6 passed)

- [ ] **Step 7: Verify wallet regressions**

Run: `cd backend; uv run pytest tests/test_wallet.py tests/test_wallet_purchases.py tests/test_per_call_pricing.py tests/test_cashout.py -v`
Expected: PASS — `agent_run_id` defaults to `None` on every existing ledger path.

- [ ] **Step 8: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/services/agent_runs.py backend/app/models/wallet.py backend/app/services/wallet.py backend/alembic/versions/ backend/tests/test_agent_run_billing.py
git commit -m "feat(agent): charge and refund agent authoring runs

Gate and debit are one operation, mirroring per-call metering. A failed
run refunds in full and the refund is idempotent, so a retried terminal
transition can't pay the user twice."
```

---

# Phase 2 — Perception & actuation

### Task 6: Page observation

**Files:**
- Create: `backend/app/agent/__init__.py`
- Create: `backend/app/agent/observe.py`
- Create: `backend/app/agent/observe.js`
- Test: `backend/tests/test_agent_observe.py`

**Interfaces:**
- Consumes: a Playwright `Page`.
- Produces, in `app.agent.observe`:
  - `@dataclass Observation: tree: str, ref_count: int, screenshot_b64: str, url: str, title: str`
  - `async def observe(page: Page, with_screenshot: bool = True) -> Observation`
  - `async def resolve_ref(page: Page, ref: str) -> ElementHandle` — raises `RefNotFound`
  - `class RefNotFound(Exception)`

**Design note:** refs are stored in a JS array on `window.__abRefs`, **not** as DOM attributes. Tagging elements with `data-ab-*` would pollute the selectors the recorder generates for those very elements, which is the whole asset this feature is built on. The array is cleared by navigation, so `observe()` must be called after every navigation — which the agent loop does anyway.

- [ ] **Step 1: Write the observation script**

Create `backend/app/agent/observe.js`:

```javascript
// Builds a compact, interactive-first view of the page for an LLM agent and
// parks the matching element references on window.__abRefs so the driver can
// act on them WITHOUT tagging the DOM (attributes would pollute the recorder's
// generated selectors for these elements).
(() => {
  const INTERACTIVE = 'a,button,input,select,textarea,[role=button],[role=link],[role=tab],[onclick],[contenteditable=true]';
  const MAX_TEXT = 120;

  window.__abRefs = [];

  const visible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    const s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  };

  const label = (el) => {
    const parts = [
      el.getAttribute('aria-label'),
      el.getAttribute('placeholder'),
      el.getAttribute('name'),
      el.getAttribute('title'),
      el.value,
      el.innerText,
    ];
    for (const p of parts) {
      if (p && p.trim()) return p.trim().slice(0, MAX_TEXT).replace(/\s+/g, ' ');
    }
    return '';
  };

  const lines = [];
  for (const el of document.querySelectorAll(INTERACTIVE)) {
    if (!visible(el)) continue;
    const i = window.__abRefs.length;
    window.__abRefs.push(el);
    const tag = el.tagName.toLowerCase();
    const type = el.getAttribute('type');
    lines.push(`[ref_${i}] <${tag}${type ? ' type=' + type : ''}> ${label(el)}`);
  }

  // A sample of repeated content blocks, so the agent can see what data the
  // page holds and pick an extraction target, not just what it can click.
  const blocks = [];
  const counts = new Map();
  for (const el of document.querySelectorAll('li,article,tr,[class*=item],[class*=card],[class*=product]')) {
    if (!visible(el)) continue;
    const key = el.tagName.toLowerCase() + '.' + (el.className || '').toString().split(/\s+/)[0];
    counts.set(key, (counts.get(key) || 0) + 1);
    if (counts.get(key) <= 3) {
      const i = window.__abRefs.length;
      window.__abRefs.push(el);
      blocks.push(`[ref_${i}] (${key}) ${el.innerText.trim().slice(0, MAX_TEXT).replace(/\s+/g, ' ')}`);
    }
  }

  return {
    url: location.href,
    title: document.title,
    interactive: lines.join('\n'),
    blocks: blocks.join('\n'),
    refCount: window.__abRefs.length,
  };
})()
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_agent_observe.py`:

```python
import pytest

from app.agent.observe import RefNotFound, observe, resolve_ref


@pytest.mark.asyncio
async def test_observe_lists_interactive_elements(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html")
    obs = await observe(fixture_page, with_screenshot=False)

    assert "[ref_0]" in obs.tree
    assert "input" in obs.tree
    assert "Search products" in obs.tree
    assert obs.ref_count > 0
    assert obs.url.endswith("/search.html")


@pytest.mark.asyncio
async def test_observe_surfaces_repeated_content_blocks(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html?q=television")
    obs = await observe(fixture_page, with_screenshot=False)
    assert "Smart Television" in obs.tree


@pytest.mark.asyncio
async def test_observe_does_not_tag_the_dom(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html")
    await observe(fixture_page, with_screenshot=False)
    html = await fixture_page.content()
    assert "data-ab-" not in html


@pytest.mark.asyncio
async def test_resolve_ref_returns_the_element(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html")
    await observe(fixture_page, with_screenshot=False)
    handle = await resolve_ref(fixture_page, "ref_0")
    assert (await handle.evaluate("el => el.tagName")).lower() in {"input", "button", "a", "form"}


@pytest.mark.asyncio
async def test_resolve_ref_rejects_unknown_ref(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html")
    await observe(fixture_page, with_screenshot=False)
    with pytest.raises(RefNotFound):
        await resolve_ref(fixture_page, "ref_9999")


@pytest.mark.asyncio
async def test_observe_captures_a_screenshot(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html")
    obs = await observe(fixture_page, with_screenshot=True)
    assert len(obs.screenshot_b64) > 100
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_observe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent'`

- [ ] **Step 4: Implement the module**

Create `backend/app/agent/__init__.py` (empty file).

Create `backend/app/agent/observe.py`:

```python
import base64
import re
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import ElementHandle, Page

OBSERVE_JS_PATH = Path(__file__).resolve().parent / "observe.js"
_REF_RE = re.compile(r"^ref_(\d+)$")


class RefNotFound(Exception):
    """A ref the model named is not in the current observation."""


@dataclass(frozen=True)
class Observation:
    tree: str
    ref_count: int
    screenshot_b64: str
    url: str
    title: str


async def observe(page: Page, with_screenshot: bool = True) -> Observation:
    """Snapshots the page for the agent: an interactive-element listing, a
    sample of repeated content blocks, and (optionally) a screenshot.

    The listing is curated rather than a raw DOM dump — burying the real
    controls in inline SVG and framework hashes makes the model worse at
    finding them, not better.
    """
    raw = await page.evaluate(OBSERVE_JS_PATH.read_text(encoding="utf-8"))

    sections = [f"URL: {raw['url']}", f"TITLE: {raw['title']}"]
    if raw["interactive"]:
        sections.append("INTERACTIVE ELEMENTS:\n" + raw["interactive"])
    if raw["blocks"]:
        sections.append("CONTENT BLOCKS (sampled):\n" + raw["blocks"])

    screenshot_b64 = ""
    if with_screenshot:
        screenshot_b64 = base64.b64encode(await page.screenshot(type="png")).decode()

    return Observation(
        tree="\n\n".join(sections),
        ref_count=raw["refCount"],
        screenshot_b64=screenshot_b64,
        url=raw["url"],
        title=raw["title"],
    )


async def resolve_ref(page: Page, ref: str) -> ElementHandle:
    """Turns a ref_N handle from the last observation back into a live element.

    Refs live in a JS array, so they are invalidated by navigation — the caller
    must re-observe after any page load before acting again.
    """
    match = _REF_RE.match(ref or "")
    if match is None:
        raise RefNotFound(f"malformed ref {ref!r}")

    handle = await page.evaluate_handle(
        "i => (window.__abRefs || [])[i] || null", int(match.group(1))
    )
    element = handle.as_element()
    if element is None:
        raise RefNotFound(
            f"{ref} is not on the current page — re-observe before acting"
        )
    return element
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_agent_observe.py -v`
Expected: PASS (6 passed)

- [ ] **Step 6: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/agent/ backend/tests/test_agent_observe.py
git commit -m "feat(agent): page observation with non-invasive element refs

Refs live on window.__abRefs rather than DOM attributes: tagging elements
would pollute the selectors the recorder generates for them, which is the
asset this whole feature is built on."
```

---

### Task 7: Tool dispatch

**Files:**
- Create: `backend/app/agent/tools.py`
- Test: `backend/tests/test_agent_tools.py`

**Interfaces:**
- Consumes: `observe`, `resolve_ref`, `RefNotFound`, `Observation` (Task 6).
- Produces, in `app.agent.tools`:
  - `TOOL_SCHEMAS: list[dict]` — OpenAI function-tool definitions for `complete_tools`
  - `@dataclass ToolOutcome: text: str, observation: Observation | None, finished: bool, gave_up: bool`
  - `async def dispatch(page: Page, name: str, arguments: dict, marks: list[str]) -> ToolOutcome`

`marks` is the caller-owned list that `mark_target` appends to; `dispatch` never keeps state of its own.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_agent_tools.py`:

```python
import pytest

from app.agent.observe import observe
from app.agent.tools import TOOL_SCHEMAS, dispatch


def test_tool_schemas_cover_the_documented_surface():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert names == {
        "navigate", "click", "fill", "press", "scroll",
        "mark_target", "done", "give_up",
    }


def test_every_tool_schema_is_well_formed():
    for tool in TOOL_SCHEMAS:
        assert tool["type"] == "function"
        fn = tool["function"]
        assert fn["description"]
        assert fn["parameters"]["type"] == "object"


@pytest.mark.asyncio
async def test_navigate_loads_the_page(fixture_site_url, fixture_page):
    outcome = await dispatch(
        fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, []
    )
    assert not outcome.finished
    assert outcome.observation is not None
    assert outcome.observation.url.endswith("/search.html")


@pytest.mark.asyncio
async def test_fill_then_click_runs_the_search(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, [])
    obs = await observe(fixture_page, with_screenshot=False)

    input_ref = next(
        line.split("]")[0].strip("[") for line in obs.tree.splitlines()
        if "<input" in line
    )
    await dispatch(fixture_page, "fill", {"ref": input_ref, "value": "television"}, [])
    assert await fixture_page.input_value("#q") == "television"


@pytest.mark.asyncio
async def test_unknown_ref_is_reported_not_raised(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, [])
    outcome = await dispatch(fixture_page, "click", {"ref": "ref_9999"}, [])
    assert "re-observe" in outcome.text
    assert not outcome.finished


@pytest.mark.asyncio
async def test_mark_target_records_the_ref(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html?q=fan"}, [])
    await observe(fixture_page, with_screenshot=False)
    marks: list[str] = []
    await dispatch(fixture_page, "mark_target", {"ref": "ref_0"}, marks)
    assert marks == ["ref_0"]


@pytest.mark.asyncio
async def test_done_and_give_up_terminate(fixture_site_url, fixture_page):
    await dispatch(fixture_page, "navigate", {"url": f"{fixture_site_url}/search.html"}, [])
    assert (await dispatch(fixture_page, "done", {}, [])).finished
    gave = await dispatch(fixture_page, "give_up", {"reason": "login wall"}, [])
    assert gave.finished and gave.gave_up and "login wall" in gave.text


@pytest.mark.asyncio
async def test_unknown_tool_is_reported(fixture_site_url, fixture_page):
    outcome = await dispatch(fixture_page, "teleport", {}, [])
    assert "unknown tool" in outcome.text.lower()
    assert not outcome.finished
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_tools.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent.tools'`

- [ ] **Step 3: Implement the dispatcher**

Create `backend/app/agent/tools.py`:

```python
import logging
from dataclasses import dataclass

from playwright.async_api import Page

from app.agent.observe import Observation, RefNotFound, observe, resolve_ref

log = logging.getLogger("agent")

POST_ACTION_SETTLE_MS = 1200


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOL_SCHEMAS: list[dict] = [
    _fn("navigate", "Load a URL. Prefer interacting with the page over "
        "navigating directly to a result URL — a hardcoded result URL "
        "produces an API that ignores its own parameters.",
        {"url": {"type": "string"}}, ["url"]),
    _fn("click", "Click the element with the given ref from the latest observation.",
        {"ref": {"type": "string"}}, ["ref"]),
    _fn("fill", "Type a value into the input with the given ref.",
        {"ref": {"type": "string"}, "value": {"type": "string"}}, ["ref", "value"]),
    _fn("press", "Press a keyboard key (for example Enter) on the element with the given ref.",
        {"ref": {"type": "string"}, "key": {"type": "string"}}, ["ref", "key"]),
    _fn("scroll", "Scroll the page to load lazily-rendered content.",
        {"direction": {"type": "string", "enum": ["down", "up"]}}, ["direction"]),
    _fn("mark_target", "Mark the element with the given ref as holding data the "
        "API should extract. Mark one representative repeated item, not every item.",
        {"ref": {"type": "string"}}, ["ref"]),
    _fn("done", "The workflow is complete and the target data is on screen.", {}, []),
    _fn("give_up", "Stop: this task cannot be completed (for example a login wall).",
        {"reason": {"type": "string"}}, ["reason"]),
]


@dataclass
class ToolOutcome:
    text: str
    observation: Observation | None = None
    finished: bool = False
    gave_up: bool = False


async def dispatch(page: Page, name: str, arguments: dict, marks: list[str]) -> ToolOutcome:
    """Executes one agent tool call against the live page.

    Never raises: a bad ref, a missing element, or a navigation error is
    reported back to the model as text so it can correct itself. Killing the
    run on a recoverable mistake would waste the whole authoring attempt.
    """
    if name == "done":
        return ToolOutcome(text="done", finished=True)
    if name == "give_up":
        reason = arguments.get("reason") or "no reason given"
        return ToolOutcome(text=f"gave up: {reason}", finished=True, gave_up=True)

    try:
        if name == "navigate":
            await page.goto(arguments["url"], wait_until="domcontentloaded")
        elif name == "scroll":
            delta = 2000 if arguments.get("direction", "down") == "down" else -2000
            await page.mouse.wheel(0, delta)
        elif name in {"click", "fill", "press"}:
            element = await resolve_ref(page, arguments.get("ref", ""))
            if name == "click":
                await element.click()
            elif name == "fill":
                await element.fill(str(arguments.get("value", "")))
            else:
                await element.press(arguments.get("key", "Enter"))
        elif name == "mark_target":
            ref = arguments.get("ref", "")
            await resolve_ref(page, ref)  # validates it exists
            marks.append(ref)
            return ToolOutcome(text=f"marked {ref} as an extraction target")
        else:
            return ToolOutcome(text=f"unknown tool {name!r}")
    except RefNotFound as exc:
        return ToolOutcome(text=str(exc))
    except Exception as exc:
        log.info("agent tool %s failed: %s", name, exc)
        return ToolOutcome(text=f"{name} failed: {exc}")

    await page.wait_for_timeout(POST_ACTION_SETTLE_MS)
    observation = await observe(page)
    return ToolOutcome(text=f"{name} ok", observation=observation)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_agent_tools.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/agent/tools.py backend/tests/test_agent_tools.py
git commit -m "feat(agent): tool schemas and dispatch

Tool failures are reported back to the model as text rather than raised:
a bad ref is a recoverable mistake, and killing the session for it would
throw away the whole authoring attempt."
```

---

# Phase 3 — The pipeline

### Task 8: Plan phase

**Files:**
- Create: `backend/app/agent/planner.py`
- Modify: `backend/app/llm/prompts.py`
- Test: `backend/tests/test_agent_planner.py`

**Interfaces:**
- Consumes: `complete_json` (`app.llm.client`).
- Produces, in `app.agent.planner`:
  - `PLAN_SCHEMA: dict`
  - `async def build_plan(prompt: str) -> dict` returning
    `{"url": str, "summary": str, "parameters": [{"name","type","required","drive_value","verify_value","description"}], "fields": [{"name","type"}]}`
  - `class PlanError(Exception)`

Declaring `drive_value` and `verify_value` up front is what makes parameter binding a string match in Task 10 and makes the differential check possible in Task 12.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_agent_planner.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.planner import PlanError, build_plan


def _plan(**overrides):
    plan = {
        "url": "https://waltonbd.com",
        "summary": "Search Walton products",
        "parameters": [{
            "name": "query", "type": "string", "required": True,
            "drive_value": "refrigerator", "verify_value": "television",
            "description": "Search term",
        }],
        "fields": [{"name": "title", "type": "string"},
                   {"name": "price", "type": "string"}],
    }
    plan.update(overrides)
    return plan


@pytest.mark.asyncio
async def test_build_plan_returns_a_validated_plan():
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=_plan())):
        plan = await build_plan("make me an API to search walton")

    assert plan["url"] == "https://waltonbd.com"
    assert plan["parameters"][0]["drive_value"] == "refrigerator"
    assert plan["parameters"][0]["verify_value"] == "television"


@pytest.mark.asyncio
async def test_build_plan_rejects_identical_drive_and_verify_values():
    bad = _plan(parameters=[{
        "name": "query", "type": "string", "required": True,
        "drive_value": "same", "verify_value": "same", "description": "",
    }])
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=bad)):
        with pytest.raises(PlanError, match="distinct"):
            await build_plan("p")


@pytest.mark.asyncio
async def test_build_plan_rejects_a_non_http_url():
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=_plan(url="ftp://x"))):
        with pytest.raises(PlanError, match="http"):
            await build_plan("p")


@pytest.mark.asyncio
async def test_build_plan_rejects_unsafe_parameter_names():
    bad = _plan(parameters=[{
        "name": "drop table", "type": "string", "required": True,
        "drive_value": "a", "verify_value": "b", "description": "",
    }])
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=bad)):
        with pytest.raises(PlanError, match="name"):
            await build_plan("p")


@pytest.mark.asyncio
async def test_build_plan_rejects_an_empty_field_list():
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=_plan(fields=[]))):
        with pytest.raises(PlanError, match="field"):
            await build_plan("p")


@pytest.mark.asyncio
async def test_build_plan_coerces_an_unknown_type_to_string():
    odd = _plan(parameters=[{
        "name": "query", "type": "wat", "required": True,
        "drive_value": "a", "verify_value": "b", "description": "",
    }])
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=odd)):
        plan = await build_plan("p")
    assert plan["parameters"][0]["type"] == "string"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_planner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent.planner'`

- [ ] **Step 3: Implement the planner**

Create `backend/app/agent/planner.py`:

```python
import re

from app.llm.client import complete_json
from app.recorder.session import VALID_PARAM_TYPES

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,29}$")

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "summary": {"type": "string"},
        "parameters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "type": {"type": "string", "enum": sorted(VALID_PARAM_TYPES)},
                    "required": {"type": "boolean"},
                    "drive_value": {"type": "string"},
                    "verify_value": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["name", "type", "drive_value", "verify_value"],
            },
        },
        "fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "type": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    "required": ["url", "parameters", "fields"],
}

PLAN_SYSTEM = (
    "You plan web-scraping workflows. Given a user's request in plain English, "
    "you decide which site to start on, which inputs become API parameters, and "
    "which data fields the API returns. You do not browse — you only plan.\n\n"
    "Rules:\n"
    "- Pick the most likely official site for the described target.\n"
    "- For every parameter, give TWO different realistic example values: "
    "drive_value (used while building) and verify_value (used to prove the "
    "parameter actually works). They MUST be different and MUST produce "
    "different results.\n"
    "- Parameter names are lowercase snake_case.\n"
    "- Field names describe the data, not the markup: title, price, url."
)


class PlanError(Exception):
    """The model's plan was unusable."""


async def build_plan(prompt: str) -> dict:
    raw = await complete_json(
        PLAN_SYSTEM,
        f"User request: {prompt}",
        PLAN_SCHEMA,
        max_tokens=1500,
    )

    url = str(raw.get("url") or "")
    if not url.startswith(("http://", "https://")):
        raise PlanError(f"plan did not produce an http(s) start URL: {url!r}")

    fields = [f for f in (raw.get("fields") or []) if f.get("name")]
    if not fields:
        raise PlanError("plan declared no output fields")

    parameters = []
    for item in raw.get("parameters") or []:
        name = str(item.get("name") or "")
        if not _NAME_RE.match(name):
            raise PlanError(f"unsafe parameter name {name!r}")
        drive = str(item.get("drive_value") or "")
        verify = str(item.get("verify_value") or "")
        if not drive or not verify:
            raise PlanError(f"parameter {name!r} is missing an example value")
        if drive == verify:
            # Without two distinct values the differential verify check cannot
            # tell a working parameter from a hardcoded one.
            raise PlanError(f"parameter {name!r} needs distinct drive and verify values")
        ptype = item.get("type")
        parameters.append({
            "name": name,
            "type": ptype if ptype in VALID_PARAM_TYPES else "string",
            "required": bool(item.get("required", True)),
            "drive_value": drive,
            "verify_value": verify,
            "description": item.get("description") or None,
        })

    return {
        "url": url,
        "summary": raw.get("summary") or "",
        "parameters": parameters,
        "fields": fields,
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_agent_planner.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/agent/planner.py backend/tests/test_agent_planner.py
git commit -m "feat(agent): plan phase declares parameters before browsing

Two distinct example values per parameter are mandatory: drive_value
makes binding a string match, and verify_value makes the differential
check possible. A plan without both is rejected."
```

---

### Task 9: Drive phase

**Files:**
- Create: `backend/app/agent/driver.py`
- Test: `backend/tests/test_agent_driver.py`

**Interfaces:**
- Consumes: `complete_tools`, `user_message`, `tool_result_message`, `TurnResult` (Task 2); `TOOL_SCHEMAS`, `dispatch`, `ToolOutcome` (Task 7); `observe` (Task 6).
- Produces, in `app.agent.driver`:
  - `@dataclass DriveResult: marks: list[str], gave_up: bool, give_up_reason: str | None, turns: int, tokens: int`
  - `async def drive(page: Page, plan: dict, max_turns: int = 25, on_progress=None) -> DriveResult`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_agent_driver.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.driver import drive
from app.llm.client import ToolCall, TurnResult


def _turn(*calls, text=None, tokens=10):
    return TurnResult(tool_calls=list(calls), text=text, usage_tokens=tokens)


PLAN = {
    "url": "http://placeholder/search.html",
    "parameters": [{"name": "query", "type": "string", "required": True,
                    "drive_value": "television", "verify_value": "fan",
                    "description": None}],
    "fields": [{"name": "title", "type": "string"}],
}


@pytest.mark.asyncio
async def test_drive_runs_tool_calls_until_done(fixture_site_url, fixture_page):
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [
        _turn(ToolCall("1", "navigate", {"url": f"{fixture_site_url}/search.html?q=television"})),
        _turn(ToolCall("2", "mark_target", {"ref": "ref_0"})),
        _turn(ToolCall("3", "done", {})),
    ]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan)

    assert result.marks == ["ref_0"]
    assert not result.gave_up
    assert result.turns == 3
    assert result.tokens == 30


@pytest.mark.asyncio
async def test_drive_records_give_up(fixture_site_url, fixture_page):
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [_turn(ToolCall("1", "give_up", {"reason": "login wall"}))]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan)

    assert result.gave_up
    assert "login wall" in result.give_up_reason


@pytest.mark.asyncio
async def test_drive_stops_at_max_turns(fixture_site_url, fixture_page):
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    endless = _turn(ToolCall("x", "scroll", {"direction": "down"}))
    with patch("app.agent.driver.complete_tools", AsyncMock(return_value=endless)):
        result = await drive(fixture_page, plan, max_turns=3)

    assert result.turns == 3
    assert not result.gave_up


@pytest.mark.asyncio
async def test_drive_treats_a_textonly_turn_as_a_nudge(fixture_site_url, fixture_page):
    plan = {**PLAN, "url": f"{fixture_site_url}/search.html"}
    turns = [_turn(text="I am thinking"), _turn(ToolCall("1", "done", {}))]
    with patch("app.agent.driver.complete_tools", AsyncMock(side_effect=turns)):
        result = await drive(fixture_page, plan, max_turns=5)

    assert result.turns == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_driver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent.driver'`

- [ ] **Step 3: Implement the driver**

Create `backend/app/agent/driver.py`:

```python
import json
import logging
from dataclasses import dataclass, field

from playwright.async_api import Page

from app.agent.observe import observe
from app.agent.tools import TOOL_SCHEMAS, dispatch
from app.llm.client import complete_tools, tool_result_message, user_message

log = logging.getLogger("agent")

DRIVE_SYSTEM = (
    "You are building a reusable web API by driving a real browser.\n\n"
    "Your job: perform the described task once, using the example values given, "
    "then mark the data the API should return and call done.\n\n"
    "Critical rules:\n"
    "- Reach results by INTERACTING with the page (type into the search box, "
    "click the button). Do NOT navigate straight to a result URL you guessed — "
    "that produces an API that returns the same data for every input, which "
    "fails verification.\n"
    "- Use the exact example value given for each parameter, so the value can "
    "be recognised and turned into a parameter afterwards.\n"
    "- Call mark_target on ONE representative repeated item, not on every item.\n"
    "- Refs come from the most recent observation only. After any navigation, "
    "the previous refs are void.\n"
    "- If the task needs a login, call give_up — you must never enter credentials."
)


@dataclass
class DriveResult:
    marks: list[str] = field(default_factory=list)
    gave_up: bool = False
    give_up_reason: str | None = None
    turns: int = 0
    tokens: int = 0


def _task_brief(plan: dict) -> str:
    params = "\n".join(
        f"- {p['name']} ({p['type']}): use the value {p['drive_value']!r}"
        for p in plan["parameters"]
    )
    fields = ", ".join(f["name"] for f in plan["fields"])
    return (
        f"Task: {plan.get('summary') or 'build the described API'}\n"
        f"Start URL: {plan['url']}\n"
        f"Parameters to exercise:\n{params or '- (none)'}\n"
        f"Data fields the API must return: {fields}\n\n"
        "Begin by navigating to the start URL."
    )


async def drive(page: Page, plan: dict, max_turns: int = 25, on_progress=None) -> DriveResult:
    """Runs the agent's tool-calling loop against a live page.

    The page belongs to a live RecordingSession, so every action taken here is
    captured as a workflow step with ranked selectors by the injected recorder —
    this function never builds a selector itself.
    """
    result = DriveResult()
    marks: list[str] = result.marks

    observation = await observe(page)
    messages: list[dict] = [
        user_message(f"{_task_brief(plan)}\n\n{observation.tree}", observation.screenshot_b64)
    ]

    for _ in range(max_turns):
        turn = await complete_tools(DRIVE_SYSTEM, messages, TOOL_SCHEMAS)
        result.turns += 1
        result.tokens += turn.usage_tokens

        if not turn.tool_calls:
            # A text-only turn is the model thinking out loud; nudge it back to
            # acting rather than ending the run.
            messages.append({"role": "assistant", "content": turn.text or ""})
            messages.append(user_message("Call a tool to continue, or call give_up."))
            continue

        messages.append({
            "role": "assistant",
            "content": turn.text,
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                for c in turn.tool_calls
            ],
        })

        finished = False
        for call in turn.tool_calls:
            outcome = await dispatch(page, call.name, call.arguments, marks)
            if on_progress is not None:
                await on_progress(call.name, call.arguments, outcome.text)

            messages.append(tool_result_message(call.id, outcome.text))

            if outcome.gave_up:
                result.gave_up = True
                result.give_up_reason = outcome.text
            if outcome.finished:
                finished = True
                break
            if outcome.observation is not None:
                messages.append(
                    user_message(outcome.observation.tree, outcome.observation.screenshot_b64)
                )

        if finished:
            break

    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_agent_driver.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/agent/driver.py backend/tests/test_agent_driver.py
git commit -m "feat(agent): tool-calling drive loop

The system prompt explicitly forbids navigating straight to a guessed
result URL — that is the single most common way autonomous authoring
produces an API that ignores its own parameters."
```

---

### Task 10: Distill

**Files:**
- Create: `backend/app/agent/distill.py`
- Test: `backend/tests/test_agent_distill.py`

**Interfaces:**
- Consumes: `AgentRun` plan shape (Task 8); recorded steps in the session's step DSL.
- Produces, in `app.agent.distill`:
  - `def bind_parameters(steps: list[dict], plan: dict) -> list[dict]` — returns new steps; never mutates input
  - `def redact_steps(steps: list[dict]) -> list[dict]`
  - `class DistillError(Exception)`

`bind_parameters` covers both `fill` step values **and** `goto` URLs, emitting `url_template` for the latter (which Task 1 taught replay to honor).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_agent_distill.py`:

```python
import pytest

from app.agent.distill import DistillError, bind_parameters, redact_steps

PLAN = {
    "parameters": [{
        "name": "query", "type": "string", "required": True,
        "drive_value": "television", "verify_value": "fan", "description": None,
    }],
    "fields": [{"name": "title", "type": "string"}],
}


def test_binds_a_fill_step_value():
    steps = [
        {"type": "goto", "url": "https://x/"},
        {"type": "fill", "selectors": ["#q"], "value": {"literal": "television"}},
        {"type": "click", "selectors": ["button"]},
    ]
    bound = bind_parameters(steps, PLAN)
    assert bound[1]["value"] == {"param": "query"}


def test_binds_a_goto_url_as_a_template():
    steps = [{"type": "goto", "url": "https://x/search?q=television&page=1"}]
    bound = bind_parameters(steps, PLAN)
    assert bound[0]["url_template"] == "https://x/search?q={query}&page=1"
    assert bound[0]["url"] == "https://x/search?q=television&page=1"


def test_binds_a_url_encoded_value_in_a_goto():
    plan = {"parameters": [{**PLAN["parameters"][0], "drive_value": "smart tv"}],
            "fields": PLAN["fields"]}
    steps = [{"type": "goto", "url": "https://x/search?q=smart%20tv"}]
    bound = bind_parameters(steps, plan)
    assert bound[0]["url_template"] == "https://x/search?q={query}"


def test_does_not_mutate_the_input_steps():
    steps = [{"type": "fill", "selectors": ["#q"], "value": {"literal": "television"}}]
    bind_parameters(steps, PLAN)
    assert steps[0]["value"] == {"literal": "television"}


def test_raises_when_a_parameter_was_never_used():
    steps = [{"type": "goto", "url": "https://x/"}]
    with pytest.raises(DistillError, match="query"):
        bind_parameters(steps, PLAN)


def test_binds_every_occurrence_of_the_value():
    steps = [
        {"type": "fill", "selectors": ["#q"], "value": {"literal": "television"}},
        {"type": "fill", "selectors": ["#q2"], "value": {"literal": "television"}},
    ]
    bound = bind_parameters(steps, PLAN)
    assert bound[0]["value"] == {"param": "query"}
    assert bound[1]["value"] == {"param": "query"}


def test_redacts_password_like_steps():
    steps = [
        {"type": "fill", "selectors": ["#password"], "value": {"literal": "hunter2"}},
        {"type": "fill", "selectors": ["input[name=otp]"], "value": {"literal": "123456"}},
        {"type": "fill", "selectors": ["#q"], "value": {"literal": "television"}},
    ]
    safe = redact_steps(steps)
    assert safe[0]["value"]["literal"] == "[REDACTED]"
    assert safe[1]["value"]["literal"] == "[REDACTED]"
    assert safe[2]["value"]["literal"] == "television"


def test_redaction_caps_long_literals():
    steps = [{"type": "fill", "selectors": ["#q"], "value": {"literal": "x" * 500}}]
    assert len(redact_steps(steps)[0]["value"]["literal"]) == 120


def test_redaction_does_not_mutate_the_input():
    steps = [{"type": "fill", "selectors": ["#password"], "value": {"literal": "hunter2"}}]
    redact_steps(steps)
    assert steps[0]["value"]["literal"] == "hunter2"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_distill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent.distill'`

- [ ] **Step 3: Implement distill**

Create `backend/app/agent/distill.py`:

```python
import copy
import re
from urllib.parse import quote

SECRET_SELECTOR_RE = re.compile(r"password|passwd|pwd|otp|pin|cvv|secret", re.IGNORECASE)
MAX_LITERAL_CHARS = 120
REDACTED = "[REDACTED]"

VALUE_STEP_TYPES = {"fill", "select_option"}


class DistillError(Exception):
    """The transcript could not be turned into a usable workflow."""


def redact_steps(steps: list[dict]) -> list[dict]:
    """Strips credentials before any step list is shown to the model.

    injected.js does no input-type filtering, so a typed password lands in the
    steps as a plain literal. Returns a copy; never mutates the caller's list.
    """
    safe = copy.deepcopy(steps)
    for step in safe:
        value = step.get("value")
        if not isinstance(value, dict) or "literal" not in value:
            continue
        selectors = " ".join(step.get("selectors") or [])
        if SECRET_SELECTOR_RE.search(selectors):
            value["literal"] = REDACTED
        else:
            value["literal"] = str(value["literal"])[:MAX_LITERAL_CHARS]
    return safe


def _template_url(url: str, name: str, drive_value: str) -> str | None:
    """Replaces a drive value inside a URL with a {param} placeholder, trying
    the encoded form as well since the browser will have encoded it."""
    for candidate in (drive_value, quote(drive_value, safe=""), quote_plus_safe(drive_value)):
        if candidate and candidate in url:
            return url.replace(candidate, "{" + name + "}")
    return None


def quote_plus_safe(value: str) -> str:
    return quote(value, safe="").replace("%20", "+")


def bind_parameters(steps: list[dict], plan: dict) -> list[dict]:
    """Turns the agent's literal drive values into parameter references.

    This is a string match, not an inference: the plan chose drive_value before
    the browser opened, so the step that consumed it is identifiable exactly.
    Covers both step values and goto URLs — the agent can reach results either
    by typing or by navigating, and a URL-bound parameter left literal would
    silently produce an API that ignores its own input.

    Returns a new step list; never mutates the caller's.
    """
    bound = copy.deepcopy(steps)

    for parameter in plan.get("parameters") or []:
        name = parameter["name"]
        drive_value = parameter["drive_value"]
        used = False

        for step in bound:
            stype = step.get("type")

            if stype in VALUE_STEP_TYPES:
                value = step.get("value")
                if isinstance(value, dict) and value.get("literal") == drive_value:
                    step["value"] = {"param": name}
                    used = True

            elif stype == "goto":
                template = _template_url(step.get("url", ""), name, drive_value)
                if template is not None:
                    # Keep the literal url for display; replay prefers the template.
                    step["url_template"] = template
                    used = True

        if not used:
            raise DistillError(
                f"parameter {name!r} was never used during the run — "
                f"the value {drive_value!r} appears in no step"
            )

    return bound
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_agent_distill.py -v`
Expected: PASS (9 passed)

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/agent/distill.py backend/tests/test_agent_distill.py
git commit -m "feat(agent): bind drive values to parameters, redact secrets

Binding is a string match because the plan chose the value before the
browser opened. Covers goto URLs as well as step values, so a URL-driven
search cannot silently freeze its own parameter."
```

---

### Task 11: Build the extraction config from marks

`mark_target(ref)` records *which* elements hold the data. Nothing yet turns those refs into an `extraction` config or an `extract` step — without this task the distilled workflow returns no data at all.

The existing compiler entry points are `compile_root_from_pick(page, pick_ctx)` and `compile_from_pick(page, pick_ctx, *, mode, root, field)`. Both expect a **`pick_ctx`** — the payload `injected.js` produces when a human clicks in pick mode (it carries `pick_id`, heuristic `selectors`, `rect`, `outline`, `generalized`, and relies on the element being stamped with `data-ab-pick`). The agent's job here is to manufacture that same payload from a ref, then reuse the compiler untouched.

**Files:**
- Create: `backend/app/agent/extract.py`
- Create: `backend/app/agent/pick_ctx.js`
- Test: `backend/tests/test_agent_extract.py`

**Interfaces:**
- Consumes: `resolve_ref` (Task 6); `compile_from_pick`, `compile_root_from_pick` (`app.recorder.selector_compiler`); the plan's `fields` (Task 8).
- Produces, in `app.agent.extract`:
  - `async def pick_context_for_ref(page: Page, ref: str) -> dict` — a `pick_ctx` byte-compatible with pick mode's
  - `async def build_extraction(page: Page, marks: list[str], plan: dict) -> dict` — returns `{"main": {...}}`
  - `class ExtractionError(Exception)`

- [ ] **Step 1: Read the pick payload `injected.js` produces**

Run: `cd backend; grep -n "pick_id\|generalized\|outline\|data-ab-pick" app/recorder/injected.js`

Write down the **exact** key names and value shapes of the object pick mode posts through `__abEmit`. `pick_ctx.js` must reproduce that object exactly — the compiler validates against it, and a near-miss will fail in ways that look like selector bugs.

- [ ] **Step 2: Write `pick_ctx.js`**

Create `backend/app/agent/pick_ctx.js`. It takes a ref index, resolves it from `window.__abRefs`, stamps `data-ab-pick` exactly as pick mode does, and returns the same payload shape recorded in Step 1:

```javascript
// Manufactures the pick-mode payload for an element the agent marked, so the
// existing selector compiler can be reused verbatim. Key names MUST match
// injected.js's pick payload — see Step 1.
(i) => {
  const el = (window.__abRefs || [])[i];
  if (!el) return null;
  const pickId = 'agent-' + i + '-' + (window.__abPickSeq = (window.__abPickSeq || 0) + 1);
  el.setAttribute('data-ab-pick', pickId);
  // Fill in the remaining keys to match injected.js exactly.
  return { pick_id: pickId, /* selectors, rect, outline, generalized */ };
}
```

> The commented keys are **not** a placeholder to leave in place — Step 1 tells you their exact shapes. Complete them before running the tests.

- [ ] **Step 3: Write the failing test**

Create `backend/tests/test_agent_extract.py`:

```python
import pytest

from app.agent.extract import ExtractionError, build_extraction, pick_context_for_ref
from app.agent.observe import observe

PLAN = {
    "parameters": [],
    "fields": [{"name": "title", "type": "string"}, {"name": "price", "type": "string"}],
}


async def _mark_a_product(page, fixture_site_url) -> str:
    await page.goto(f"{fixture_site_url}/search.html?q=television")
    obs = await observe(page, with_screenshot=False)
    for line in obs.tree.splitlines():
        if "li.product" in line or "Smart Television" in line:
            return line.split("]")[0].strip("[")
    raise AssertionError(f"no product block in observation:\n{obs.tree}")


@pytest.mark.asyncio
async def test_pick_context_has_the_compiler_required_keys(fixture_site_url, fixture_page):
    ref = await _mark_a_product(fixture_page, fixture_site_url)
    ctx = await pick_context_for_ref(fixture_page, ref)
    assert ctx["pick_id"]
    assert ctx["selectors"]


@pytest.mark.asyncio
async def test_pick_context_stamps_the_element(fixture_site_url, fixture_page):
    ref = await _mark_a_product(fixture_page, fixture_site_url)
    ctx = await pick_context_for_ref(fixture_page, ref)
    stamped = await fixture_page.query_selector(f'[data-ab-pick="{ctx["pick_id"]}"]')
    assert stamped is not None


@pytest.mark.asyncio
async def test_build_extraction_produces_a_list_config(fixture_site_url, fixture_page):
    ref = await _mark_a_product(fixture_page, fixture_site_url)
    config = await build_extraction(fixture_page, [ref], PLAN)

    main = config["main"]
    assert main["mode"] == "list"
    assert main["root"]
    assert {f["name"] for f in main["fields"]} == {"title", "price"}
    for field in main["fields"]:
        assert field["selectors"], f"{field['name']} compiled to no selectors"


@pytest.mark.asyncio
async def test_built_config_actually_extracts(fixture_site_url, fixture_page):
    from app.recorder.extraction import run_extraction

    ref = await _mark_a_product(fixture_page, fixture_site_url)
    config = await build_extraction(fixture_page, [ref], PLAN)
    rows = await run_extraction(fixture_page, config["main"])

    assert len(rows) >= 2
    assert {r["title"] for r in rows} == {"Smart Television", "Basic Television"}


@pytest.mark.asyncio
async def test_no_marks_raises(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html?q=fan")
    with pytest.raises(ExtractionError, match="no extraction target"):
        await build_extraction(fixture_page, [], PLAN)


@pytest.mark.asyncio
async def test_stale_ref_raises(fixture_site_url, fixture_page):
    await fixture_page.goto(f"{fixture_site_url}/search.html?q=fan")
    with pytest.raises(ExtractionError):
        await build_extraction(fixture_page, ["ref_9999"], PLAN)
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent.extract'`

- [ ] **Step 5: Implement the module**

Create `backend/app/agent/extract.py`:

```python
import logging
from pathlib import Path

from playwright.async_api import Page

from app.agent.observe import RefNotFound, resolve_ref
from app.recorder.selector_compiler import compile_from_pick, compile_root_from_pick

log = logging.getLogger("agent")

PICK_CTX_JS_PATH = Path(__file__).resolve().parent / "pick_ctx.js"


class ExtractionError(Exception):
    """The marked elements could not be turned into an extraction config."""


async def pick_context_for_ref(page: Page, ref: str) -> dict:
    """Manufactures pick mode's payload for a ref the agent marked.

    The selector compiler is driven entirely by this payload, so producing it
    faithfully is what lets the agent reuse the human pick path unchanged
    instead of authoring selectors itself.
    """
    try:
        await resolve_ref(page, ref)
    except RefNotFound as exc:
        raise ExtractionError(str(exc)) from exc

    index = int(ref.removeprefix("ref_"))
    ctx = await page.evaluate(PICK_CTX_JS_PATH.read_text(encoding="utf-8"), index)
    if not ctx:
        raise ExtractionError(f"{ref} could not be described for the compiler")
    return ctx


async def build_extraction(page: Page, marks: list[str], plan: dict) -> dict:
    """Turns the agent's marked elements into an extraction config.

    The first mark is treated as the repeating container; the plan's declared
    field names are the compilation targets, so the model maps onto a declared
    schema rather than inventing names.
    """
    if not marks:
        raise ExtractionError("agent finished with no extraction target marked")

    root_ctx = await pick_context_for_ref(page, marks[0])
    roots = await compile_root_from_pick(page, root_ctx)
    mode = "list" if roots else "single"
    root = roots[0] if roots else None

    fields = []
    for declared in plan.get("fields") or []:
        field = {"name": declared["name"], "take": "text"}
        selectors = await compile_from_pick(
            page, root_ctx, mode=mode, root=root, field=field
        )
        if not selectors:
            log.info("no selectors compiled for field %s", declared["name"])
        fields.append({**field, "selectors": selectors})

    if not any(f["selectors"] for f in fields):
        raise ExtractionError("no declared field could be located on the page")

    config: dict = {"mode": mode, "fields": fields, "engine": "compiled"}
    if root:
        config["root"] = root
    return {"main": config}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_agent_extract.py -v`
Expected: PASS (6 passed)

> `compile_from_pick` calls the LLM when configured. If these tests are slow or flaky against the live provider, set `LLM_ENABLED=false` for this file — the compiler's heuristic path must produce working selectors on the fixture site on its own, and proving that is worth the test.

- [ ] **Step 7: Wire the extract step into distill**

The distilled workflow needs a terminal `extract` step, mirroring how `session.py` appends one at save time (`{"type": "extract", "ref": "main"}` when `extraction["main"]` exists and no extract step is present). Add to `backend/app/agent/distill.py`:

```python
def append_extract_step(steps: list[dict], extraction: dict) -> list[dict]:
    """Appends the terminal extract step, matching what RecordingSession does
    at save time. Returns a new list."""
    result = copy.deepcopy(steps)
    if extraction.get("main") and not any(s.get("type") == "extract" for s in result):
        result.append({"type": "extract", "ref": "main"})
    return result
```

Add a test to `tests/test_agent_distill.py`:

```python
def test_append_extract_step_adds_one_when_missing():
    from app.agent.distill import append_extract_step

    steps = append_extract_step([{"type": "goto", "url": "https://x/"}], {"main": {"mode": "list"}})
    assert steps[-1] == {"type": "extract", "ref": "main"}


def test_append_extract_step_is_idempotent():
    from app.agent.distill import append_extract_step

    existing = [{"type": "extract", "ref": "main"}]
    assert append_extract_step(existing, {"main": {}}) == existing
```

Run: `cd backend; uv run pytest tests/test_agent_distill.py -v`
Expected: PASS (11 passed)

- [ ] **Step 8: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/agent/extract.py backend/app/agent/pick_ctx.js backend/app/agent/distill.py backend/tests/test_agent_extract.py backend/tests/test_agent_distill.py
git commit -m "feat(agent): compile marked elements into an extraction config

Manufactures pick mode's payload from an agent ref so the existing
selector compiler is reused verbatim — the agent marks what it wants and
never writes a selector."
```

---

### Task 12: Verify

**Files:**
- Create: `backend/app/agent/verify.py`
- Test: `backend/tests/test_agent_verify.py`

**Interfaces:**
- Consumes: `replay_workflow`, `ReplayError` (`app.recorder.replay`).
- Produces, in `app.agent.verify`:
  - `@dataclass CheckResult: name: str, passed: bool, detail: str`
  - `@dataclass VerifyResult: checks: list[CheckResult], passed: bool, data: object`
  - `async def verify_workflow(snapshot: dict, plan: dict, drive_data: object, *, workflow_id=None) -> VerifyResult`

Check 4 — the differential check — is the reason this task exists. It is the only check that catches a workflow which ignores its parameter.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_agent_verify.py`:

```python
import pytest

from app.agent.verify import verify_workflow

PLAN = {
    "parameters": [{
        "name": "query", "type": "string", "required": True,
        "drive_value": "refrigerator", "verify_value": "television",
        "description": None,
    }],
    "fields": [{"name": "title", "type": "string"}, {"name": "price", "type": "string"}],
}

EXTRACTION = {
    "main": {
        "mode": "list",
        "root": "li.product",
        "fields": [
            {"name": "title", "selectors": [".title"], "take": "text"},
            {"name": "price", "selectors": [".price"], "take": "text"},
        ],
    }
}


def _templated_snapshot(base: str) -> dict:
    return {
        "steps": [
            {"type": "goto",
             "url": f"{base}/search.html?q=refrigerator",
             "url_template": f"{base}/search.html?q={{query}}"},
            {"type": "extract", "ref": "main"},
        ],
        "extraction": EXTRACTION,
    }


def _hardcoded_snapshot(base: str) -> dict:
    return {
        "steps": [
            {"type": "goto", "url": f"{base}/search.html?q=refrigerator"},
            {"type": "extract", "ref": "main"},
        ],
        "extraction": EXTRACTION,
    }


DRIVE_DATA = [
    {"title": "Blue Refrigerator", "price": "45000"},
    {"title": "Silver Refrigerator", "price": "52000"},
]


@pytest.mark.asyncio
async def test_a_correct_workflow_passes_every_check(fixture_site_url):
    result = await verify_workflow(
        _templated_snapshot(fixture_site_url), PLAN, DRIVE_DATA
    )
    assert result.passed, [c.detail for c in result.checks if not c.passed]
    assert {row["title"] for row in result.data} == {"Smart Television", "Basic Television"}


@pytest.mark.asyncio
async def test_a_hardcoded_url_fails_the_differential_check(fixture_site_url):
    """The critical test: schema and row-count checks all pass, and the workflow
    is still broken because it ignores its parameter."""
    result = await verify_workflow(
        _hardcoded_snapshot(fixture_site_url), PLAN, DRIVE_DATA
    )
    assert not result.passed
    failed = [c.name for c in result.checks if not c.passed]
    assert failed == ["differs_from_drive"]


@pytest.mark.asyncio
async def test_missing_declared_field_fails(fixture_site_url):
    snapshot = _templated_snapshot(fixture_site_url)
    snapshot["extraction"]["main"]["fields"] = [
        {"name": "title", "selectors": [".title"], "take": "text"}
    ]
    result = await verify_workflow(snapshot, PLAN, DRIVE_DATA)
    assert not result.passed
    assert "fields_present" in [c.name for c in result.checks if not c.passed]


@pytest.mark.asyncio
async def test_zero_rows_fails(fixture_site_url):
    snapshot = _templated_snapshot(fixture_site_url)
    snapshot["steps"][0]["url_template"] = f"{fixture_site_url}/search.html?q=nothingmatches"
    result = await verify_workflow(snapshot, PLAN, DRIVE_DATA)
    assert not result.passed
    assert "has_rows" in [c.name for c in result.checks if not c.passed]


@pytest.mark.asyncio
async def test_replay_error_fails_the_first_check(fixture_site_url):
    snapshot = _templated_snapshot(fixture_site_url)
    snapshot["steps"].insert(1, {"type": "click", "selectors": ["#does-not-exist"]})
    result = await verify_workflow(snapshot, PLAN, DRIVE_DATA)
    assert not result.passed
    assert result.checks[0].name == "replays"
    assert not result.checks[0].passed
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_verify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent.verify'`

- [ ] **Step 3: Implement verify**

Create `backend/app/agent/verify.py`:

```python
import json
import logging
import uuid
from dataclasses import dataclass, field

from app.recorder.replay import ReplayError, replay_workflow

log = logging.getLogger("agent")


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class VerifyResult:
    checks: list[CheckResult] = field(default_factory=list)
    data: object = None

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.passed for c in self.checks)

    def failure_summary(self) -> str:
        return "; ".join(c.detail for c in self.checks if not c.passed)


def _rows(data: object) -> list[dict]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _canonical(data: object) -> str:
    return json.dumps(data, sort_keys=True, default=str)


async def verify_workflow(
    snapshot: dict,
    plan: dict,
    drive_data: object,
    *,
    workflow_id: uuid.UUID | None = None,
) -> VerifyResult:
    """Replays the distilled workflow with values the agent never drove with.

    The differential check is the one that matters: an agent that navigated to
    a literal result URL produces a workflow that passes every structural check
    and still returns identical data for every input. Nothing else catches it.
    """
    params = {p["name"]: p["verify_value"] for p in plan.get("parameters") or []}
    result = VerifyResult()

    try:
        replay = await replay_workflow(
            snapshot, params, None, uuid.uuid4(), headless=True, workflow_id=workflow_id
        )
    except ReplayError as exc:
        result.checks.append(CheckResult("replays", False, f"replay failed: {exc}"))
        return result
    except Exception as exc:
        result.checks.append(CheckResult("replays", False, f"replay errored: {exc}"))
        return result

    data = replay.get("data")
    result.data = data
    result.checks.append(CheckResult("replays", True, "replay completed"))

    rows = _rows(data)
    has_rows = len(rows) >= 1
    result.checks.append(CheckResult(
        "has_rows", has_rows,
        "extraction returned no rows" if not has_rows else f"{len(rows)} row(s)",
    ))

    declared = [f["name"] for f in plan.get("fields") or []]
    missing = [n for n in declared if not any(n in row for row in rows)] if rows else declared
    result.checks.append(CheckResult(
        "fields_present", not missing,
        f"declared fields missing from output: {', '.join(missing)}" if missing
        else "all declared fields present",
    ))

    differs = _canonical(data) != _canonical(drive_data)
    result.checks.append(CheckResult(
        "differs_from_drive", differs,
        "output is identical for a different parameter value — the workflow "
        "ignores its own parameter (likely a hardcoded URL)" if not differs
        else "output changed with the parameter",
    ))

    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_agent_verify.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/agent/verify.py backend/tests/test_agent_verify.py
git commit -m "feat(agent): verify a distilled workflow against an unseen value

The differential check catches the dominant autonomous-authoring failure:
a workflow that passes every structural check and still returns the same
data regardless of input."
```

---

### Task 13: Orchestration and repair

**Files:**
- Create: `backend/app/agent/runner.py`
- Test: `backend/tests/test_agent_runner.py`

**Interfaces:**
- Consumes: `build_plan` (8), `drive` (9), `bind_parameters` / `redact_steps` (10), `build_extraction` (11), `verify_workflow` (12), `agent_runs.finish_run` (5), `observe` (6).
- Produces, in `app.agent.runner`:
  - `MAX_ATTEMPTS = 3`, `WALL_CLOCK_SECONDS = 600`
  - `async def run_agent(agent_run_id: uuid.UUID) -> None`
  - `async def attempt_once(page, plan, run, attempt: int) -> VerifyResult | None`

This task owns the retry policy and the publish handoff. Because it coordinates a live browser it is tested with the browser pieces stubbed; the browser-integrated path is covered by Task 19.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_agent_runner.py`:

```python
from unittest.mock import AsyncMock, patch

import pytest

from app.agent.runner import MAX_ATTEMPTS, should_retry
from app.agent.verify import CheckResult, VerifyResult


def _verify(passed: bool) -> VerifyResult:
    return VerifyResult(checks=[CheckResult("replays", passed, "d")])


def test_should_retry_while_attempts_remain():
    assert should_retry(_verify(False), attempt=1) is True
    assert should_retry(_verify(False), attempt=MAX_ATTEMPTS - 1) is True


def test_should_not_retry_after_the_last_attempt():
    assert should_retry(_verify(False), attempt=MAX_ATTEMPTS) is False


def test_should_not_retry_a_passing_verify():
    assert should_retry(_verify(True), attempt=1) is False


def test_repair_hint_names_the_failed_checks():
    from app.agent.runner import repair_hint

    result = VerifyResult(checks=[
        CheckResult("replays", True, "ok"),
        CheckResult("differs_from_drive", False, "output is identical"),
    ])
    hint = repair_hint(result)
    assert "differs_from_drive" in hint
    assert "identical" in hint


def test_repair_hint_suggests_interaction_for_the_differential_failure():
    from app.agent.runner import repair_hint

    result = VerifyResult(checks=[CheckResult("differs_from_drive", False, "identical")])
    assert "interact" in repair_hint(result).lower()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent.runner'`

- [ ] **Step 3: Implement the retry policy helpers**

Create `backend/app/agent/runner.py` with the pure helpers first:

```python
import logging

from app.agent.verify import VerifyResult

log = logging.getLogger("agent")

MAX_ATTEMPTS = 3
WALL_CLOCK_SECONDS = 600

_STRATEGY_HINTS = {
    "differs_from_drive": (
        "The workflow returned identical data for a different parameter value. "
        "You almost certainly navigated straight to a result URL. This time, "
        "interact with the page: type into the search input and submit it."
    ),
    "has_rows": (
        "The extraction returned no rows. Try a different extraction target — "
        "mark the repeated result container, not a wrapper or a single field."
    ),
    "fields_present": (
        "Some declared fields were missing. Mark an element that actually "
        "contains all the requested data."
    ),
    "replays": (
        "The workflow failed to replay. Prefer stable, visible controls and "
        "avoid steps that depend on transient page state."
    ),
}


def should_retry(result: VerifyResult, attempt: int) -> bool:
    return not result.passed and attempt < MAX_ATTEMPTS


def repair_hint(result: VerifyResult) -> str:
    """Turns failed checks into a concrete change of strategy for the next
    attempt. Repeating the same route would just reproduce the same failure."""
    failed = [c for c in result.checks if not c.passed]
    if not failed:
        return ""
    parts = []
    for check in failed:
        parts.append(f"[{check.name}] {check.detail}")
        hint = _STRATEGY_HINTS.get(check.name)
        if hint:
            parts.append(hint)
    return "\n".join(parts)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_agent_runner.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit the helpers**

```bash
cd backend && uv run ruff check app
git add backend/app/agent/runner.py backend/tests/test_agent_runner.py
git commit -m "feat(agent): retry policy and per-check repair hints"
```

- [ ] **Step 6: Add the orchestrator**

Append to `backend/app/agent/runner.py`:

```python
import json
import time
import uuid

from app.agent.distill import DistillError, bind_parameters
from app.agent.driver import drive
from app.agent.planner import PlanError, build_plan
from app.agent.verify import verify_workflow
from app.db import async_session
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.workflow import Workflow, WorkflowStatus
from app.redis import redis_client
from app.services import agent_runs


def evt_channel(run_id: uuid.UUID) -> str:
    return f"agent:evt:{run_id}"


def cmd_channel(run_id: uuid.UUID) -> str:
    return f"agent:cmd:{run_id}"


async def publish(run_id: uuid.UUID, event: dict) -> None:
    await redis_client.publish(evt_channel(run_id), json.dumps(event))


async def _set_status(run_id: uuid.UUID, status: AgentRunStatus, **extra) -> None:
    async with async_session() as db:
        run = await db.get(AgentRun, run_id)
        if run is None:
            return
        run.status = status
        for key, value in extra.items():
            setattr(run, key, value)
        await db.commit()
    await publish(run_id, {"t": "status", "state": status.value, **extra})


async def await_url_confirmation(run_id: uuid.UUID, timeout_s: float = 300.0) -> bool:
    """Blocks until the user confirms or rejects the resolved URL.

    Uses the same command-channel pattern the recorder already uses for
    pick-mode and undo — no new transport.
    """
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(cmd_channel(run_id))
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if message is None or message["type"] != "message":
                continue
            command = json.loads(message["data"])
            if command.get("t") == "confirm_url":
                return bool(command.get("ok"))
        return False
    finally:
        await pubsub.unsubscribe(cmd_channel(run_id))
        await pubsub.aclose()
```

> **Step 6 note for the implementer:** the remaining orchestration — opening the headless `RecordingSession`, looping attempts, writing the `Workflow` row, and calling `agent_runs.finish_run` — depends on a refactor of `RecordingSession` to accept an agent driver. That refactor is Task 14; complete it before finishing `run_agent`.

- [ ] **Step 7: Commit**

```bash
cd backend && uv run ruff check app
git add backend/app/agent/runner.py
git commit -m "feat(agent): run channels, status transitions, URL confirmation gate"
```

---

# Phase 4 — Wiring

### Task 14: Headless agent recording session

**Files:**
- Modify: `backend/app/recorder/session.py`
- Modify: `backend/app/agent/runner.py` (finish `run_agent`)
- Modify: `backend/app/workers/handlers.py`
- Modify: `backend/app/workers/main.py`
- Test: `backend/tests/test_agent_session.py`

**Interfaces:**
- Consumes: everything from Phases 0–3.
- Produces: `RecordingSession(..., agent_prompt: str | None = None, headless: bool = False)`; `handlers.agent_run(payload: dict)`; queue `jobs:agent` with concurrency 1.

- [ ] **Step 1: Read the session lifecycle end to end**

Run: `cd backend; sed -n '92,300p' app/recorder/session.py`

Identify: where `launch_kwargs["headless"]` is set (line ~119), where the page becomes available (`self.page`), where `_record_step` appends, and where `_run_in_context`'s `finally` cancels tasks. The agent hook must sit **after** the page and injected script are ready and **before** the idle-timeout watchdog can fire.

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_agent_session.py`:

```python
import pytest

from app.recorder.session import RecordingSession


def test_session_defaults_to_headful():
    session = RecordingSession("00000000-0000-0000-0000-000000000001",
                               "00000000-0000-0000-0000-000000000002")
    assert session.headless is False
    assert session.agent_prompt is None


def test_agent_session_is_headless():
    session = RecordingSession("00000000-0000-0000-0000-000000000001",
                               "00000000-0000-0000-0000-000000000002",
                               agent_prompt="search for fridges", headless=True)
    assert session.headless is True
    assert session.agent_prompt == "search for fridges"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_session.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'agent_prompt'`

- [ ] **Step 4: Add the constructor parameters**

In `backend/app/recorder/session.py`, extend `__init__` (line 46):

```python
    def __init__(
        self,
        workflow_id: str,
        user_id: str,
        rerecord: bool = False,
        agent_prompt: str | None = None,
        headless: bool = False,
    ):
```

and add to the body, next to the other flags:

```python
        # Autonomous authoring drives this same session headlessly. Headless
        # matters for correctness, not convenience: replay is headless, so a
        # workflow authored headful would be verified in a different browser
        # than the one that built it.
        self.agent_prompt = agent_prompt
        self.headless = headless
```

Then change the launch to use it (line ~119):

```python
                launch_kwargs: dict = {
                    "headless": self.headless,
                    "args": [
                        *([] if self.headless else ["--start-maximized"]),
                        *stealth.launch_args(headless=self.headless),
                    ],
                }
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend; uv run pytest tests/test_agent_session.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Verify recorder regressions**

Run: `cd backend; uv run pytest tests/test_recorder_mark_param.py tests/test_recorder_undo.py tests/test_recorder_rerecord.py tests/test_recorder_compile.py -v`
Expected: PASS — the defaults reproduce today's headful behavior exactly.

- [ ] **Step 7: Register the worker queue**

In `backend/app/workers/handlers.py`, add:

```python
async def agent_run(payload: dict) -> None:
    """Autonomous authoring run. Consumes the recording slot because it IS a
    recording session — the browser budget gains no new dimension."""
    from app.agent.runner import run_agent

    await run_agent(uuid.UUID(payload["agent_run_id"]))
```

In `backend/app/workers/main.py`, add to `QUEUES`:

```python
    # Shares the recorder's one-at-a-time budget: an agent run IS a recording
    # session, and only one browser may own the recorder profile at a time.
    "jobs:agent": (handlers.agent_run, settings.rec_max_concurrency),
```

- [ ] **Step 8: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/recorder/session.py backend/app/workers/handlers.py backend/app/workers/main.py backend/tests/test_agent_session.py
git commit -m "feat(agent): headless agent recording session and worker queue

Headless is a correctness choice: replay is headless, so authoring
headful would verify a workflow in a different browser than built it."
```

---

### Task 15: FastAPI routes

**Files:**
- Create: `backend/app/api/agent.py`
- Create: `backend/app/schemas/agent.py`
- Modify: `backend/app/main.py` (register the router)
- Modify: `backend/app/api/ws.py` (bridge `agent:evt:*` / `agent:cmd:*`)
- Test: `backend/tests/test_agent_api.py`

**Interfaces:**
- Consumes: `agent_runs.create_run` / `AgentRunNotAllowed` (Task 5); `runner.cmd_channel` / `evt_channel` (Task 13).
- Produces: `POST /api/agent/runs`, `GET /api/agent/runs/{id}`, `POST /api/agent/runs/{id}/confirm`, `GET /api/agent/runs`, and a WS bridge at the existing WS path for `agent:{run_id}`.

- [ ] **Step 1: Read the existing router and WS bridge**

Run: `cd backend; cat app/api/recordings.py app/api/ws.py`

Follow the exact auth dependency, error-shape, and Redis-enqueue idioms found there. The steps below use those idioms and must be adapted to the real names.

- [ ] **Step 2: Write the failing test**

This codebase tests API routes by **calling the route coroutine directly** with a `User` and an `AsyncSession` — there is no HTTP `client` fixture. Errors are asserted with `pytest.raises(HTTPException)` on `exc.value.status_code`. Follow that shape exactly.

Create `backend/tests/test_agent_api.py`:

```python
import asyncio
import json
import uuid
from decimal import Decimal

import pytest
from fastapi import HTTPException

from app.agent.runner import cmd_channel
from app.api import agent as agent_api
from app.models.agent_run import AgentRun, AgentRunStatus
from app.schemas.agent import AgentRunCreate, ConfirmUrlIn
from app.services import plans, wallet


async def _funded_pro(db, make_user, amount="100.00"):
    """A user on a tier that permits agent runs, with money in the wallet."""
    user = await make_user()
    await plans.set_tier(user, plans.PlanTier.PRO, db)  # use this repo's real helper
    await wallet.credit(user.id, Decimal(amount), "recharge", db)
    await db.commit()
    return user


@pytest.mark.asyncio
async def test_create_run_enqueues_a_job(db, make_user, redis):
    await plans.ensure_seeded(db)
    user = await _funded_pro(db, make_user)

    run = await agent_api.create_run(
        AgentRunCreate(prompt="search walton for fridges"), user=user, db=db
    )

    assert run.status == AgentRunStatus.PLANNING.value
    entries = await redis.xrange("jobs:agent")
    assert len(entries) == 1
    assert json.loads(entries[0][1]["payload"])["agent_run_id"] == str(run.id)


@pytest.mark.asyncio
async def test_create_run_debits_the_wallet(db, make_user, redis):
    await plans.ensure_seeded(db)
    user = await _funded_pro(db, make_user)

    await agent_api.create_run(AgentRunCreate(prompt="p"), user=user, db=db)

    balance, _ = await wallet.balances(user.id, db)
    assert balance < Decimal("100.00")


@pytest.mark.asyncio
async def test_free_tier_is_rejected_with_403(db, make_user):
    await plans.ensure_seeded(db)
    user = await make_user()  # free tier
    await wallet.credit(user.id, Decimal("100.00"), "recharge", db)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await agent_api.create_run(AgentRunCreate(prompt="p"), user=user, db=db)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_empty_wallet_is_rejected_with_402(db, make_user):
    await plans.ensure_seeded(db)
    user = await make_user()
    await plans.set_tier(user, plans.PlanTier.PRO, db)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await agent_api.create_run(AgentRunCreate(prompt="p"), user=user, db=db)
    assert exc.value.status_code == 402


@pytest.mark.asyncio
async def test_a_rejected_create_leaves_no_orphan_run(db, make_user):
    """Gate and debit are one operation, so a 402 must not leave a run row."""
    from sqlalchemy import select

    await plans.ensure_seeded(db)
    user = await make_user()
    await plans.set_tier(user, plans.PlanTier.PRO, db)
    await db.commit()

    with pytest.raises(HTTPException):
        await agent_api.create_run(AgentRunCreate(prompt="p"), user=user, db=db)

    rows = await db.execute(select(AgentRun).where(AgentRun.user_id == user.id))
    assert rows.scalars().all() == []


@pytest.mark.asyncio
async def test_confirm_publishes_to_the_command_channel(db, make_user, redis):
    await plans.ensure_seeded(db)
    user = await _funded_pro(db, make_user)
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.AWAITING_CONFIRM)
    db.add(run)
    await db.commit()

    pubsub = redis.pubsub()
    await pubsub.subscribe(cmd_channel(run.id))
    await asyncio.sleep(0.1)  # let the subscription register

    await agent_api.confirm_url(run.id, ConfirmUrlIn(ok=True), user=user, db=db)

    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=3.0)
    assert json.loads(message["data"]) == {"t": "confirm_url", "ok": True}
    await pubsub.aclose()


@pytest.mark.asyncio
async def test_a_user_cannot_read_another_users_run(db, make_user):
    await plans.ensure_seeded(db)
    owner = await make_user()
    stranger = await make_user()
    run = AgentRun(user_id=owner.id, prompt="p")
    db.add(run)
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await agent_api.get_run(run.id, user=stranger, db=db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_run_returns_the_owners_run(db, make_user):
    await plans.ensure_seeded(db)
    owner = await make_user()
    run = AgentRun(user_id=owner.id, prompt="p")
    db.add(run)
    await db.commit()

    out = await agent_api.get_run(run.id, user=owner, db=db)
    assert out.id == run.id


@pytest.mark.asyncio
async def test_unknown_run_is_404(db, make_user):
    user = await make_user()
    with pytest.raises(HTTPException) as exc:
        await agent_api.get_run(uuid.uuid4(), user=user, db=db)
    assert exc.value.status_code == 404
```

> `plans.set_tier` and `plans.PlanTier` are placeholders for whatever this repo uses to give a user an effective tier (see the note in Task 5). Read `app/services/plans.py` and substitute the real mechanism before running.
>
> A stranger reading someone else's run gets **404, not 403** — matching how the rest of this codebase hides the existence of other users' resources. Confirm against `app/api/apis.py` and follow whatever it does.

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_agent_api.py -v`
Expected: FAIL — `ImportError: cannot import name 'agent' from 'app.api'`

- [ ] **Step 4: Implement schemas, router, and WS bridge**

Create `backend/app/schemas/agent.py` with `AgentRunCreate` (`prompt: str`, 1–500 chars), `AgentRunOut` (id, status, prompt, resolved_url, plan, attempt, failure_reason, workflow_id, created_at), and `ConfirmUrlIn` (`ok: bool`).

Create `backend/app/api/agent.py` with the four routes. `POST /api/agent/runs` must:
1. call `agent_runs.create_run` (which gates and debits),
2. translate `AgentRunNotAllowed` → `403` and `InsufficientBalance` → `402`,
3. `commit`,
4. `XADD` `{"agent_run_id": str(run.id)}` to `jobs:agent`,
5. return `202` with the run.

`POST /api/agent/runs/{id}/confirm` publishes `{"t": "confirm_url", "ok": bool}` to `runner.cmd_channel(run_id)` after checking ownership.

In `backend/app/api/ws.py`, extend the existing bridge so a `kind=agent` connection subscribes to `agent:evt:{id}` and forwards client frames to `agent:cmd:{id}`, reusing the recorder bridge's ownership check verbatim.

Register the router in `backend/app/main.py` alongside the others.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_agent_api.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/api/agent.py backend/app/schemas/agent.py backend/app/main.py backend/app/api/ws.py backend/tests/test_agent_api.py
git commit -m "feat(agent): REST routes and WS bridge for agent runs

Gate and debit happen in one service call, so a 402 or 403 never leaves
an orphan run row behind."
```

---

# Phase 5 — Frontend

### Task 16: Agent builder page

**Files:**
- Create: `frontend/src/pages/AgentBuilder.tsx`
- Create: `frontend/src/hooks/useAgentRun.ts`
- Modify: `frontend/src/App.tsx` (route `/build`)
- Modify: the primary navigation component (add "Build with AI")

**Interfaces:**
- Consumes: `POST /api/agent/runs`, `GET /api/agent/runs/{id}`, `POST /api/agent/runs/{id}/confirm`, the WS bridge (Task 15).
- Produces: `useAgentRun(runId)` returning `{status, plan, steps, checks, failureReason, workflowId, confirmUrl}`.

- [ ] **Step 1: Read the existing recorder page and hook**

Run: `cd frontend; cat src/hooks/useRecorder.ts src/components/RecorderStepList.tsx`

Reuse `RecorderStepList` verbatim for the live step list; do not fork it. Follow [DESIGN.md](../../DESIGN.md) (Warm Editorial) for all styling.

- [ ] **Step 2: Build `useAgentRun`**

Mirror `useRecorder`'s WebSocket lifecycle: connect on mount, parse `{t: "status" | "step" | "plan" | "verify" | "error"}` events into state, reconnect on drop, close on unmount.

- [ ] **Step 3: Build the page**

Five states, in order:
1. **Prompt** — textarea, submit, and a visible note that autonomous authoring costs one agent-run charge and works on public sites only.
2. **Awaiting confirmation** — the resolved URL with Confirm / Cancel. This is the one interruption in the flow.
3. **Running** — phase indicator, the declared plan (parameters and fields) rendered as soon as it arrives, and `<RecorderStepList>` for live steps.
4. **Verifying** — the four checks with pass/fail and detail per check.
5. **Terminal** — on success, a link into the existing publish flow; on failure, the reason, the artifact link, and a **"Record it manually instead"** button routing to the existing recorder with `start_url` prefilled.

- [ ] **Step 4: Verify in the browser**

Start the dev server and walk the flow against the fixture site. Confirm: the step list populates live, the confirmation gate blocks until clicked, a failed run shows its reason, and the manual-recording button carries the URL across.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/AgentBuilder.tsx frontend/src/hooks/useAgentRun.ts frontend/src/App.tsx
git commit -m "feat(frontend): agent builder page

Separate route from the recorder, which stays untouched and becomes the
fallback a failed run routes into."
```

---

# Phase 6 — Hardening

### Task 17: Concurrency and refund reconciliation

**Files:**
- Modify: `backend/app/workers/periodic.py`
- Test: `backend/tests/test_agent_reconcile.py`

- [ ] **Step 1: Write the failing test**

Assert that a run left non-terminal past `WALL_CLOCK_SECONDS` (simulating a worker crash) is swept to `failed` and refunded exactly once, and that a run inside its window is left alone.

- [ ] **Step 2: Run it, implement the sweep in `periodic_sweep`, run it again**

The sweep reuses `agent_runs.finish_run`, whose idempotence guarantees a crashed-then-swept run cannot double-refund.

- [ ] **Step 3: Verify the deadlock hazard**

Write a test that holds the single `jobs:agent` slot and asserts a `jobs:exec` replay still runs — verify must never queue behind the recorder.

Run: `cd backend; uv run pytest tests/test_agent_reconcile.py -v`

- [ ] **Step 4: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/workers/periodic.py backend/tests/test_agent_reconcile.py
git commit -m "fix(agent): reconcile abandoned runs and refund them once"
```

---

### Task 18: Redaction on every LLM path

**Files:**
- Modify: `backend/app/agent/driver.py`, `backend/app/agent/runner.py`
- Test: `backend/tests/test_agent_redaction.py`

- [ ] **Step 1: Write the failing test**

Assert that when the transcript contains a step whose selector matches `#password`, the literal never appears in any message sent to the model — on the repair path as well as distill.

Create `backend/tests/test_agent_redaction.py`:

```python
import json

import pytest

from app.agent.distill import redact_steps

SECRET_STEPS = [
    {"type": "goto", "url": "https://x/login"},
    {"type": "fill", "selectors": ["#password"], "value": {"literal": "hunter2"}},
    {"type": "fill", "selectors": ["input[name=otp]"], "value": {"literal": "654321"}},
    {"type": "fill", "selectors": ["#q"], "value": {"literal": "television"}},
]


def test_serialized_redacted_steps_contain_no_secrets():
    payload = json.dumps(redact_steps(SECRET_STEPS))
    assert "hunter2" not in payload
    assert "654321" not in payload
    assert "television" in payload  # the real search term survives


def test_repair_context_is_built_from_redacted_steps():
    """The repair path feeds the transcript back to the model. If it reads the
    raw steps instead of the redacted copy, a typed password goes to the API."""
    from app.agent.runner import build_repair_context

    context = build_repair_context(SECRET_STEPS, failure="differs_from_drive")
    assert "hunter2" not in context
    assert "654321" not in context
```

Then add to `backend/app/agent/runner.py`:

```python
def build_repair_context(steps: list[dict], failure: str) -> str:
    """Renders the previous attempt for the repair prompt. Always goes through
    redact_steps: this is the one path where recorded credentials would
    otherwise reach the model, since drive-time values come from the plan
    rather than from the page."""
    from app.agent.distill import redact_steps

    safe = redact_steps(steps)
    lines = [f"Previous attempt failed: {failure}", "Steps taken:"]
    for i, step in enumerate(safe):
        value = (step.get("value") or {}).get("literal", "")
        lines.append(f"  {i}. {step.get('type')} {step.get('url', '')} {value}".rstrip())
    return "\n".join(lines)
```

- [ ] **Step 2: Run it, route every transcript through `redact_steps`, run it again**

- [ ] **Step 3: Lint and commit**

```bash
cd backend && uv run ruff check app
git add backend/app/agent/ backend/tests/test_agent_redaction.py
git commit -m "fix(agent): redact credentials on the repair and distill paths"
```

---

### Task 19: Opt-in integration test against Walton

**Files:**
- Create: `backend/tests/test_agent_integration.py`

- [ ] **Step 1: Write the opt-in test**

Follow the existing opt-in LLM integration test pattern:

```python
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AGENT_INTEGRATION") != "1",
    reason="hits the live network and a real LLM; set RUN_AGENT_INTEGRATION=1 to run",
)


@pytest.mark.asyncio
async def test_authors_a_walton_product_search_api(db, make_user):
    """End-to-end against waltonbd.com: plan, drive, distill, extract, verify.

    Asserts the run reaches a passing verify — including the differential
    check, which is what proves the parameter is real rather than baked in.
    """
    from decimal import Decimal

    from app.agent.runner import run_agent
    from app.models.agent_run import AgentRun, AgentRunStatus
    from app.models.workflow import Workflow
    from app.services import plans, wallet

    await plans.ensure_seeded(db)
    user = await make_user()
    await plans.set_tier(user, plans.PlanTier.MAX, db)
    await wallet.credit(user.id, Decimal("500.00"), "recharge", db)

    run = AgentRun(
        user_id=user.id,
        prompt="make me an API to search for products on the Walton website",
        resolved_url="https://waltonbd.com",
        status=AgentRunStatus.DRIVING,  # skip the interactive confirmation gate
    )
    db.add(run)
    await db.commit()

    await run_agent(run.id)

    await db.refresh(run)
    assert run.status == AgentRunStatus.SUCCEEDED, run.failure_reason
    assert run.workflow_id is not None

    workflow = await db.get(Workflow, run.workflow_id)
    assert workflow.parameters, "no parameter was bound"
    assert workflow.extraction.get("main"), "no extraction config was built"

    print(f"\nattempts={run.attempt} tokens={run.token_usage}")
```

- [ ] **Step 2: Run it manually once and record the result**

```bash
cd backend && RUN_AGENT_INTEGRATION=1 uv run pytest tests/test_agent_integration.py -v -s
```

Record in the PR description: wall-clock time, attempts used, and total `token_usage`. Those numbers are what `agent_run_price_bdt` should be calibrated against.

- [ ] **Step 3: Confirm it is skipped by default**

Run: `cd backend; uv run pytest tests/test_agent_integration.py -v`
Expected: SKIPPED (1 skipped)

- [ ] **Step 4: Full suite, lint, commit**

```bash
cd backend && uv run pytest && uv run ruff check app
git add backend/tests/test_agent_integration.py
git commit -m "test(agent): opt-in end-to-end run against waltonbd.com"
```

---

## Open items for the implementer

These are known-unresolved and must be settled during implementation, not silently assumed:

1. **Does `fill` get captured by `injected.js`?** The entire "agent drives the recorder" premise assumes it does. **Verify this before Task 14** with a throwaway script that drives a Playwright `fill()` against a live `RecordingSession` and inspects `session.steps`. If it does not fire, the agent must emit `fill` steps directly and pass them through `selector_compiler.compile_from_pick` — a contained change to Task 9, but one that must be discovered early rather than late.
2. **Gemini tool-calling support.** Task 2's tests mock the provider. Before Task 9, make one real `complete_tools` call against the configured provider and confirm `tool_calls` come back populated.
3. **`plans` module accessor names.** Tasks 4 and 5 use `ensure_seeded`, `effective_tier`, and `_settings_for` as placeholders. Read `app/services/plans.py` first and use the real names.
4. **`user.is_super_admin`.** Task 5 assumes this property exists. Confirm against `app/models/user.py`; the codebase may express it as a role comparison.

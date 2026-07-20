# Compile-Time Selector Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-call LLM value-extraction with an LLM that compiles durable, ranked, validated CSS selectors at pick time, so the published API runs deterministic extraction and the LLM only re-engages (self-heal) when a selector has broken.

**Architecture:** At pick time the worker sends the picked element's DOM outline + a screenshot crop to a multimodal LLM, which returns ranked selectors that are validated against the live DOM and stored per field. Replay runs those selectors deterministically; when all miss, a self-heal step re-derives a selector via the LLM and persists it to a keyed cache table. A pick-driven wizard (single-record vs. list-of-records) replaces the Single/List toggle.

**Tech Stack:** FastAPI + async worker (Playwright), `openai` AsyncOpenAI (OpenAI-compat, Gemma multimodal), Postgres 16 + asyncpg + SQLAlchemy 2.0 + Alembic, Redis (recorder cmd/evt pub/sub), React + Vite + TypeScript + Tailwind v4, pytest (`asyncio_mode=auto`).

## Global Constraints

- Playwright runs ONLY in the worker process — never in FastAPI, never in Docker.
- FastAPI ↔ worker talk ONLY via Redis (Streams for jobs, pub/sub for recording); the WS endpoint is a dumb bridge.
- Extraction failure must NEVER break a replay — any error degrades to selectors, then to nulls; never raises.
- LLM job concurrency is 1 — serialize LLM calls with the existing `_LLM_LOCK` semaphore in `app/recorder/llm_extract.py`.
- The app must work with `LLM_ENABLED=false`; headless replay browsers launch with `--disable-gpu`.
- SQLAlchemy 2.0 typed style, async + asyncpg; enums `native_enum=False` storing `.value`; JSONB replaced never mutated in place; timestamps UTC tz-aware.
- Extraction config is JSONB, validated loosely (`extraction: dict`) — new keys (`engine`, `selectors`, `roots`, `description`, `example`) need no schema/migration change.
- Tailwind is v4 (CSS-first, no `tailwind.config.js`).
- Backend tests: `cd backend; uv run pytest`. Lint: `cd backend; uv run ruff check app`. Frontend: `cd frontend; npm run build` (tsc + vite) and `npm run lint` (oxlint). There is NO frontend unit-test runner — frontend tasks verify via typecheck, lint, and the browser preview.
- Alembic current head is `a2861ad6bed7`.

---

## File Structure

- `backend/app/llm/client.py` — add optional `images` param to `complete_json` for multimodal calls (modify).
- `backend/app/models/extraction_cache.py` — `ExtractionSelectorCache` model (create).
- `backend/app/models/__init__.py` — register the new model (modify).
- `backend/alembic/versions/f1a2b3c4d5e6_add_extraction_selector_cache.py` — migration (create).
- `backend/app/recorder/selector_cache.py` — `read_cache` / `upsert_cache` repo helpers (create).
- `backend/app/recorder/extraction.py` — extraction JS tries `selectors[]` per field and `roots[]` for list mode (modify).
- `backend/app/recorder/injected.js` — on pick, stamp the element and emit `pick_id` + `outline` + `rect` (modify).
- `backend/app/recorder/selector_compiler.py` — `compile_from_pick` + `reheal` + prompt/validation helpers (create).
- `backend/app/recorder/session.py` — store last pick context; `compile_root` / `compile_field` commands (modify).
- `backend/app/recorder/replay.py` — `_extract_compiled` self-heal orchestration; route `engine=="compiled"`; add `workflow_id` param (modify).
- `backend/app/workers/handlers.py` — pass `workflow_id` to `replay_workflow` (modify).
- `frontend/src/lib/types.ts` — extend `ExtractionField`/`ExtractionConfig`/`PickCandidate`; add wizard types (modify).
- `frontend/src/hooks/useRecorder.ts` — wizard state machine + `compile_root`/`compile_field`/`add_field` handling + undo-pick (modify).
- `frontend/src/components/RecorderPipCard.tsx` — wizard UI in the pop-out (modify).
- `frontend/src/pages/RecorderSession.tsx` — wizard UI on the main tab; drive the state machine (modify).
- `frontend/src/components/ExtractionEditor.tsx` — remove Single/List + engine toggles; show compiled selectors read-only (modify).
- `frontend/src/pages/WorkflowEditor.tsx` — stop engine coercion; drop the toggle assumptions (modify).

Tests: `backend/tests/test_llm_multimodal.py` (create), `backend/tests/test_selector_cache.py` (create), `backend/tests/test_extraction.py` (modify), `backend/tests/test_selector_compiler.py` (create), `backend/tests/test_recorder_compile.py` (create), `backend/tests/test_replay.py` (modify).

---

## Phase A — Backend foundations

### Task 1: Multimodal `complete_json`

**Files:**
- Modify: `backend/app/llm/client.py:106-135`
- Test: `backend/tests/test_llm_multimodal.py`

**Interfaces:**
- Consumes: existing `client`, `MODEL_NAME`, `_uses_prompt_schema`, `_extract_json`.
- Produces: `complete_json(system, user, schema, max_tokens=2000, images: list[str] | None = None) -> dict`. `images` are base64-encoded PNG strings (no data-URI prefix); when present the user message becomes OpenAI content parts (one text part + one `image_url` part per image). When `images` is None the call is byte-for-byte identical to today.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_llm_multimodal.py`:

```python
import base64

from app.llm import client


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


async def test_images_become_content_parts(monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeResponse('{"selectors": ["a"]}')

    monkeypatch.setattr(client.client.chat.completions, "create", fake_create)
    img = base64.b64encode(b"pngbytes").decode()
    out = await client.complete_json("sys", "find it", {"type": "object"}, images=[img])

    assert out == {"selectors": ["a"]}
    content = captured["messages"][1]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"] == f"data:image/png;base64,{img}"


async def test_no_images_keeps_string_content(monkeypatch):
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return FakeResponse('{"ok": true}')

    monkeypatch.setattr(client.client.chat.completions, "create", fake_create)
    out = await client.complete_json("sys", "hello", {"type": "object"})

    assert out == {"ok": True}
    assert isinstance(captured["messages"][1]["content"], str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_llm_multimodal.py -v`
Expected: FAIL — `TypeError: complete_json() got an unexpected keyword argument 'images'`.

- [ ] **Step 3: Add the `images` param**

In `backend/app/llm/client.py`, replace `complete_json` (lines 106-135) with:

```python
async def complete_json(
    system: str,
    user: str,
    schema: dict,
    max_tokens: int = 2000,
    images: list[str] | None = None,
) -> dict:
    kwargs: dict = {}
    if _uses_prompt_schema():
        user = (
            f"{user}\n\nRespond with ONLY a JSON object matching this schema "
            f"(no prose, no markdown fence):\n{json.dumps(schema)}"
        )
    else:
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "out", "schema": schema, "strict": True},
        }

    if images:
        user_content: object = [
            {"type": "text", "text": user},
            *(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}
                for img in images
            ),
        ]
    else:
        user_content = user

    resp = await client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        temperature=0.2,
        max_tokens=max_tokens,
        **kwargs,
    )
    if not resp.choices:
        extra = resp.model_dump()
        detail = extra.get("message") or extra.get("error") or "gateway returned no choices"
        raise RuntimeError(f"LLM gateway error: {detail}")
    return _extract_json(resp.choices[0].message.content)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_llm_multimodal.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the existing provider tests for no regression**

Run: `cd backend; uv run pytest tests/test_llm_provider.py -v`
Expected: PASS (unchanged).

- [ ] **Step 6: Lint and commit**

```bash
cd backend; uv run ruff check app
git add backend/app/llm/client.py backend/tests/test_llm_multimodal.py
git commit -m "feat(llm): add optional images arg to complete_json for multimodal calls"
```

---

### Task 2: Selector cache model, migration, and repo helpers

**Files:**
- Create: `backend/app/models/extraction_cache.py`
- Modify: `backend/app/models/__init__.py:27` (add import)
- Create: `backend/alembic/versions/f1a2b3c4d5e6_add_extraction_selector_cache.py`
- Create: `backend/app/recorder/selector_cache.py`
- Test: `backend/tests/test_selector_cache.py`

**Interfaces:**
- Produces:
  - `ExtractionSelectorCache` ORM model, table `extraction_selector_cache`, PK `(workflow_id, ref, field_name)`, columns `selectors JSONB`, `healed_at DateTime(tz)`.
  - `async def read_cache(db, workflow_id: uuid.UUID) -> dict[tuple[str, str], list[str]]` keyed by `(ref, field_name)`.
  - `async def upsert_cache(db, workflow_id: uuid.UUID, ref: str, field_name: str, selectors: list[str]) -> None` (commits).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_selector_cache.py`. This uses the project's DB test fixture. Check `backend/tests/conftest.py` for the async-session fixture name; this plan assumes it is `db_session` (an `AsyncSession`). If the fixture has a different name, use that name instead — do not change the assertions.

```python
import uuid

from app.recorder.selector_cache import read_cache, upsert_cache


async def test_upsert_then_read_roundtrips(db_session):
    wf = uuid.uuid4()
    await upsert_cache(db_session, wf, "main", "price", [".p1", ".p2"])
    out = await read_cache(db_session, wf)
    assert out[("main", "price")] == [".p1", ".p2"]


async def test_upsert_overwrites_existing(db_session):
    wf = uuid.uuid4()
    await upsert_cache(db_session, wf, "main", "price", [".old"])
    await upsert_cache(db_session, wf, "main", "price", [".new"])
    out = await read_cache(db_session, wf)
    assert out[("main", "price")] == [".new"]


async def test_read_empty_for_unknown_workflow(db_session):
    out = await read_cache(db_session, uuid.uuid4())
    assert out == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_selector_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.recorder.selector_cache'`.

- [ ] **Step 3: Create the model**

Create `backend/app/models/extraction_cache.py`:

```python
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ExtractionSelectorCache(Base):
    """Self-healed selectors, keyed per (workflow, extract ref, field). Written by
    replay when all stored selectors miss and the LLM re-derives a working one.
    Kept out of the workflow snapshot so the authored config stays immutable and
    concurrent replays never race on one JSONB blob."""

    __tablename__ = "extraction_selector_cache"

    workflow_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflows.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ref: Mapped[str] = mapped_column(String(64), primary_key=True)
    field_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    selectors: Mapped[list] = mapped_column(JSONB)
    healed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
```

- [ ] **Step 4: Register the model**

In `backend/app/models/__init__.py`, add after line 23 (`from app.models.execution import ...`):

```python
from app.models.extraction_cache import ExtractionSelectorCache
```

If the module defines `__all__`, add `"ExtractionSelectorCache"` to it.

- [ ] **Step 5: Write the migration**

Create `backend/alembic/versions/f1a2b3c4d5e6_add_extraction_selector_cache.py`:

```python
"""add extraction selector cache

Revision ID: f1a2b3c4d5e6
Revises: a2861ad6bed7
Create Date: 2026-07-21 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'a2861ad6bed7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'extraction_selector_cache',
        sa.Column('workflow_id', sa.UUID(), nullable=False),
        sa.Column('ref', sa.String(length=64), nullable=False),
        sa.Column('field_name', sa.String(length=200), nullable=False),
        sa.Column('selectors', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('healed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['workflow_id'], ['workflows.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('workflow_id', 'ref', 'field_name'),
    )


def downgrade() -> None:
    op.drop_table('extraction_selector_cache')
```

- [ ] **Step 6: Create the repo helpers**

Create `backend/app/recorder/selector_cache.py`:

```python
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extraction_cache import ExtractionSelectorCache


async def read_cache(db: AsyncSession, workflow_id: uuid.UUID) -> dict[tuple[str, str], list[str]]:
    rows = (
        await db.execute(
            select(ExtractionSelectorCache).where(
                ExtractionSelectorCache.workflow_id == workflow_id
            )
        )
    ).scalars()
    return {(r.ref, r.field_name): r.selectors for r in rows}


async def upsert_cache(
    db: AsyncSession, workflow_id: uuid.UUID, ref: str, field_name: str, selectors: list[str]
) -> None:
    stmt = insert(ExtractionSelectorCache).values(
        workflow_id=workflow_id, ref=ref, field_name=field_name, selectors=selectors
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["workflow_id", "ref", "field_name"],
        set_={"selectors": stmt.excluded.selectors, "healed_at": __import__("sqlalchemy").func.now()},
    )
    await db.execute(stmt)
    await db.commit()
```

- [ ] **Step 7: Apply the migration and run tests**

Run: `cd backend; uv run alembic upgrade head`
Expected: applies `f1a2b3c4d5e6` with no error.

Run: `cd backend; uv run pytest tests/test_selector_cache.py -v`
Expected: PASS (3 passed). If the DB fixture creates schema from metadata rather than migrations, the model registration in Step 4 covers it; if it runs migrations, Step 7's upgrade covers it.

- [ ] **Step 8: Lint and commit**

```bash
cd backend; uv run ruff check app
git add backend/app/models/extraction_cache.py backend/app/models/__init__.py backend/alembic/versions/f1a2b3c4d5e6_add_extraction_selector_cache.py backend/app/recorder/selector_cache.py backend/tests/test_selector_cache.py
git commit -m "feat(extract): add extraction_selector_cache table + read/upsert helpers"
```

---

### Task 3: Extraction JS tries ranked `selectors[]` and `roots[]`

**Files:**
- Modify: `backend/app/recorder/extraction.py:7-49`
- Test: `backend/tests/test_extraction.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `run_extraction(page, config)` now honors, per field, a `selectors` array (tried in order, first match wins) in addition to legacy single `selector`; and for `mode == "list"`, a `roots` array (first non-empty match wins) in addition to legacy `root`. Empty/missing selectors yield `null` (no `querySelector('')` crash — guard already present).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_extraction.py`. Reuse the existing `fixture_page` fixture that the other tests in this file use.

```python
async def test_field_selectors_list_tries_in_order(fixture_page):
    # First selector misses, second hits — the ranked list must fall through.
    config = {
        "mode": "single",
        "fields": [
            {"name": "title", "selectors": [".does-not-exist", "h1"], "take": "text"},
        ],
    }
    result = await run_extraction(fixture_page, config)
    assert result["title"] is not None


async def test_roots_list_tries_in_order(fixture_page):
    # First root matches nothing, second matches the real rows.
    config = {
        "mode": "list",
        "roots": [".no-such-root", ".book-item"],
        "fields": [{"name": "title", "selectors": [".book-title"], "take": "text"}],
    }
    result = await run_extraction(fixture_page, config)
    assert len(result) == 3
    assert result[0]["title"] == "Physics 101"
```

Note: the `h1`, `.book-item`, `.book-title`, and "Physics 101" references assume the existing extraction fixture site. If your `fixture_page` serves different markup, adjust the selectors/expected text to that fixture — the behavior under test is "list tried in order," not the specific content.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_extraction.py::test_field_selectors_list_tries_in_order tests/test_extraction.py::test_roots_list_tries_in_order -v`
Expected: FAIL — the JS ignores `selectors`/`roots`, so `title` is null (no `selector` key) and the list root matches nothing.

- [ ] **Step 3: Extend the extraction JS**

In `backend/app/recorder/extraction.py`, replace `EXTRACTION_JS` (lines 7-49) with:

```python
EXTRACTION_JS = """
(config) => {
  function takeValue(el, take) {
    if (take === 'text') return el.textContent == null ? null : el.textContent.trim();
    if (take === 'html') return el.innerHTML;
    if (take && take.startsWith('attr:')) return el.getAttribute(take.slice(5));
    return null;
  }

  function applyTransform(value, transform) {
    if (value == null) return value;
    if (!transform || transform === 'none') return value;
    if (transform === 'trim') return String(value).trim();
    if (transform === 'number') {
      const cleaned = String(value).replace(/[^0-9.-]/g, '');
      const n = parseFloat(cleaned);
      return Number.isNaN(n) ? null : n;
    }
    if (transform === 'abs_url') {
      try { return new URL(value, window.location.href).href; } catch (e) { return value; }
    }
    return value;
  }

  // Ranked selectors: try each until one resolves. Legacy single `selector`
  // is treated as a one-element list. Empty/absent list -> no element.
  function fieldSelectors(f) {
    if (Array.isArray(f.selectors) && f.selectors.length) return f.selectors;
    if (f.selector) return [f.selector];
    return [];
  }

  function firstMatch(scope, selectors) {
    for (const sel of selectors) {
      if (!sel) continue;
      let el = null;
      try { el = scope.querySelector(sel); } catch (e) { el = null; }
      if (el) return el;
    }
    return null;
  }

  function extractFields(scope, fields) {
    const obj = {};
    for (const f of fields) {
      const el = firstMatch(scope, fieldSelectors(f));
      let value = el ? takeValue(el, f.take) : null;
      value = applyTransform(value, f.transform);
      obj[f.name] = value;
    }
    return obj;
  }

  function rootSelectors(config) {
    if (Array.isArray(config.roots) && config.roots.length) return config.roots;
    if (config.root) return [config.root];
    return [];
  }

  if (config.mode === 'single') {
    return extractFields(document, config.fields);
  }
  let roots = [];
  for (const sel of rootSelectors(config)) {
    if (!sel) continue;
    let found = [];
    try { found = Array.from(document.querySelectorAll(sel)); } catch (e) { found = []; }
    if (found.length) { roots = found; break; }
  }
  return roots.map((root) => extractFields(root, config.fields));
}
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_extraction.py -v`
Expected: PASS — the two new tests plus all existing extraction tests (legacy single `selector`/`root` still work, empty selector still yields null).

- [ ] **Step 5: Lint and commit**

```bash
cd backend; uv run ruff check app
git add backend/app/recorder/extraction.py backend/tests/test_extraction.py
git commit -m "feat(extract): extraction JS tries ranked per-field selectors and roots"
```

---

## Phase B — Pick-time compiler & authoring

### Task 4: Injected pick emits stamp + DOM outline + rect

**Files:**
- Modify: `backend/app/recorder/injected.js:146-163` (pick click handler) and add helpers
- Modify: `backend/app/recorder/session.py:253-266` (`_handle_page_event` pick branch)

**Interfaces:**
- Produces: on a pick click, `injected.js` stamps the element `data-ab-pick="<pickId>"`, and emits `pick_result` with new keys `pickId: string`, `outline: Array<{tag, id, classes, data, role, aria, text}>` (element first, then up to 4 ancestors), and `rect: {x, y, width, height}` — alongside existing `selectors`, `preview`, `count`, `generalized`. The session forwards `pick_id`, `outline`, `rect` to the client in the `pick_result` event AND stores the full context on `self._last_pick` for the compiler.

- [ ] **Step 1: Add outline/stamp helpers and extend the pick emit**

In `backend/app/recorder/injected.js`, add these helpers just above the pick-mode click handler (before line 146, after `stripLastNthOfType`):

```javascript
  let __abPickCounter = 0;

  // Compact, LLM-friendly description of an element and its ancestors. Drops
  // generated-looking ids/classes so the model anchors on stable attributes.
  function describeNode(el) {
    const classes = Array.from(el.classList || [])
      .filter((c) => !GENERATED_ID_RE.test(c))
      .slice(0, 6);
    const data = {};
    for (const attr of Array.from(el.attributes || [])) {
      if (attr.name.startsWith('data-') && attr.name !== 'data-ab-pick') {
        data[attr.name] = attr.value.slice(0, 40);
      }
    }
    const id = el.id && !GENERATED_ID_RE.test(el.id) ? el.id : '';
    return {
      tag: el.tagName ? el.tagName.toLowerCase() : '',
      id,
      classes,
      data,
      role: el.getAttribute ? (el.getAttribute('role') || '') : '',
      aria: el.getAttribute ? (el.getAttribute('aria-label') || '') : '',
      text: (el.textContent || '').trim().slice(0, 80),
    };
  }

  function buildOutline(el, maxLevels = 5) {
    const outline = [];
    let node = el;
    for (let i = 0; i < maxLevels && node && node.nodeType === 1 && node !== document.body; i++) {
      outline.push(describeNode(node));
      node = node.parentElement;
    }
    return outline;
  }
```

Then replace the pick-mode click handler (lines 146-163) with:

```javascript
  document.addEventListener('click', (e) => {
    if (window.__abMode !== 'pick') return;
    const el = e.target;
    if (!(el instanceof Element)) return;
    e.preventDefault();
    e.stopPropagation();

    const pickId = `p${++__abPickCounter}`;
    el.setAttribute('data-ab-pick', pickId);

    const selectors = rankSelectors(el);
    const generalized = stripLastNthOfType(selectors[selectors.length - 1]);
    let count = 1;
    try {
      count = document.querySelectorAll(generalized).length;
    } catch {
      count = 1;
    }
    const rect = el.getBoundingClientRect();
    const preview = (el.textContent || '').trim().slice(0, 200);
    emit({
      type: 'pick_result',
      pickId,
      selectors,
      preview,
      count,
      generalized,
      outline: buildOutline(el),
      rect: { x: rect.left, y: rect.top, width: rect.width, height: rect.height },
    });
  }, true);
```

- [ ] **Step 2: Store the pick context in the session and forward the new keys**

In `backend/app/recorder/session.py`, in `__init__` (after line 58, near `self._authoring_task`), add:

```python
        self._last_pick: dict | None = None
```

Then in `_handle_page_event`, replace the `pick_result` branch (lines 256-266) with:

```python
        if etype == "pick_result":
            self._last_pick = {
                "pick_id": event.get("pickId"),
                "selectors": event.get("selectors", []),
                "preview": event.get("preview"),
                "generalized": event.get("generalized"),
                "outline": event.get("outline", []),
                "rect": event.get("rect"),
            }
            await self._publish({
                "t": "pick_result",
                "candidate": {
                    "selectors": event.get("selectors", []),
                    "preview": event.get("preview"),
                    "count": event.get("count"),
                    "generalized": event.get("generalized"),
                    "pick_id": event.get("pickId"),
                },
            })
            return
```

- [ ] **Step 3: Verify the recorder still imports and the JS is syntactically valid**

Run: `cd backend; uv run python -c "import app.recorder.session; from pathlib import Path; import re; js=Path('app/recorder/injected.js').read_text(); assert js.count('(') and 'buildOutline' in js; print('ok')"`
Expected: prints `ok` (import succeeds; the file loads). Full behavior is covered by Task 6's integration test.

- [ ] **Step 4: Lint and commit**

```bash
cd backend; uv run ruff check app
git add backend/app/recorder/injected.js backend/app/recorder/session.py
git commit -m "feat(recorder): pick emits stamp id, DOM outline, and bounding rect"
```

---

### Task 5: Selector compiler (`compile_from_pick` + `reheal`)

**Files:**
- Create: `backend/app/recorder/selector_compiler.py`
- Test: `backend/tests/test_selector_compiler.py`

**Interfaces:**
- Consumes: `complete_json` (Task 1, with `images`), `_LLM_LOCK` and `_llm_configured` from `app/recorder/llm_extract.py`, a Playwright `Page`.
- Produces:
  - `async def compile_from_pick(page, pick_ctx: dict, *, mode: str, root: str | None, field: dict) -> list[str]` — authoring path. Uses the stamped element (`pick_ctx["pick_id"]`) for strong validation. Returns a ranked, validated selector list (LLM candidates first, then any validated heuristic candidates). Falls back to validated heuristic selectors when the LLM is unavailable/fails. Never raises.
  - `async def compile_root_from_pick(page, pick_ctx: dict) -> list[str]` — returns ranked, validated list-root selectors (must match ≥2 elements including the picked one). Falls back to `[pick_ctx["generalized"]]`.
  - `async def reheal(page, *, mode: str, root: str | None, field: dict) -> list[str] | None` — replay path, no human/stamp. Asks the LLM for a selector using `field["description"]`/`field["example"]`, validates it resolves to non-empty text (single) or non-empty within the first root row (list). Returns `None` when not configured, on error, or when nothing validates. Never raises.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_selector_compiler.py`:

```python
from app.recorder import selector_compiler as sc


class FakePage:
    """Stand-in Page: records evaluate() calls and returns queued results."""

    def __init__(self, eval_results, screenshot=b"png"):
        self._eval_results = list(eval_results)
        self._screenshot = screenshot
        self.evals = []

    async def evaluate(self, js, arg=None):
        self.evals.append((js, arg))
        return self._eval_results.pop(0)

    async def screenshot(self, clip=None):
        return self._screenshot


PICK = {
    "pick_id": "p1",
    "selectors": ["h3", "div > h3"],
    "preview": "Aparthotel Stare Miasto",
    "generalized": ".card",
    "outline": [{"tag": "h3", "id": "", "classes": ["title"], "data": {}, "role": "", "aria": "", "text": "Aparthotel"}],
    "rect": {"x": 0, "y": 0, "width": 100, "height": 20},
}
FIELD = {"name": "hotel_name", "description": "featured hotel name", "example": "Aparthotel", "take": "text"}


async def test_compile_from_pick_returns_validated_llm_selectors(monkeypatch):
    monkeypatch.setattr(sc, "_llm_configured", lambda: True)

    async def fake_complete_json(system, user, schema, max_tokens=2000, images=None):
        # The outline and example reached the prompt; a screenshot was attached.
        assert "featured hotel name" in user
        assert images and len(images) == 1
        return {"selectors": [".card .title", ".title"]}

    monkeypatch.setattr(sc, "complete_json", fake_complete_json)
    # Validation: both candidates resolve to the stamped element -> both True.
    page = FakePage(eval_results=[True, True])
    out = await sc.compile_from_pick(page, PICK, mode="single", root=None, field=FIELD)
    assert out[0] == ".card .title"
    assert ".title" in out


async def test_compile_from_pick_drops_invalid_candidates(monkeypatch):
    monkeypatch.setattr(sc, "_llm_configured", lambda: True)

    async def fake_complete_json(system, user, schema, max_tokens=2000, images=None):
        return {"selectors": [".wrong", ".card .title"]}

    monkeypatch.setattr(sc, "complete_json", fake_complete_json)
    # .wrong -> False (dropped), .card .title -> True (kept)
    page = FakePage(eval_results=[False, True])
    out = await sc.compile_from_pick(page, PICK, mode="single", root=None, field=FIELD)
    assert out == [".card .title"]


async def test_compile_from_pick_falls_back_to_heuristics_when_llm_down(monkeypatch):
    monkeypatch.setattr(sc, "_llm_configured", lambda: False)
    # No LLM: validate the heuristic candidates ["h3", "div > h3"]; first valid.
    page = FakePage(eval_results=[True, True])
    out = await sc.compile_from_pick(page, PICK, mode="single", root=None, field=FIELD)
    assert out and out[0] == "h3"


async def test_reheal_returns_none_when_not_configured(monkeypatch):
    monkeypatch.setattr(sc, "_llm_configured", lambda: False)
    page = FakePage(eval_results=[])
    out = await sc.reheal(page, mode="single", root=None, field=FIELD)
    assert out is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_selector_compiler.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.recorder.selector_compiler'`.

- [ ] **Step 3: Implement the compiler**

Create `backend/app/recorder/selector_compiler.py`:

```python
"""Compile-time selector generation.

At pick time the worker has the exact element the user marked (stamped with
data-ab-pick). This module asks a multimodal LLM to produce ROBUST selectors for
that element from its DOM outline + a screenshot crop, validates every candidate
against the live DOM, and returns a ranked, validated list. At replay time,
`reheal` re-derives a selector from the field description/example when all stored
selectors have broken. Nothing here ever raises — callers get selectors or a
fallback/None."""

import base64
import json
import logging
from typing import Any

from playwright.async_api import Page

from app.llm.client import complete_json
from app.recorder.llm_extract import _LLM_LOCK, _llm_configured

log = logging.getLogger("recorder")

MAX_SELECTORS = 3

_SYSTEM = (
    "You write robust CSS selectors for a specific web element. Prefer stable "
    "anchors (data-testid, semantic ids, meaningful class names, ARIA) over "
    "positional nth-of-type paths. Return only valid JSON."
)

_SELECTOR_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["selectors"],
    "properties": {"selectors": {"type": "array", "items": {"type": "string"}}},
}


def _outline_text(outline: list[dict]) -> str:
    lines = []
    for i, n in enumerate(outline):
        label = "element" if i == 0 else f"ancestor[{i}]"
        bits = [f"<{n.get('tag', '')}>"]
        if n.get("id"):
            bits.append(f"id={n['id']}")
        if n.get("classes"):
            bits.append("class=" + ".".join(n["classes"]))
        for k, v in (n.get("data") or {}).items():
            bits.append(f"{k}={v}")
        if n.get("role"):
            bits.append(f"role={n['role']}")
        if n.get("aria"):
            bits.append(f"aria-label={n['aria']}")
        if n.get("text"):
            bits.append(f'text="{n["text"]}"')
        lines.append(f"{label}: " + " ".join(bits))
    return "\n".join(lines)


async def _screenshot_b64(page: Page, rect: dict | None) -> list[str]:
    if not rect or rect.get("width", 0) <= 0 or rect.get("height", 0) <= 0:
        return []
    try:
        clip = {
            "x": max(0, float(rect["x"])),
            "y": max(0, float(rect["y"])),
            "width": float(rect["width"]),
            "height": float(rect["height"]),
        }
        png = await page.screenshot(clip=clip)
        return [base64.b64encode(png).decode()]
    except Exception as exc:
        log.warning("selector-compiler screenshot failed: %s", exc)
        return []


async def _validate_single(page: Page, pick_id: str, selector: str) -> bool:
    js = """
    (cfg) => {
      let el = null;
      try { el = document.querySelector(cfg.sel); } catch (e) { return false; }
      return !!el && el.getAttribute('data-ab-pick') === cfg.pid;
    }
    """
    try:
        return bool(await page.evaluate(js, {"sel": selector, "pid": pick_id}))
    except Exception:
        return False


async def _validate_relative(page: Page, pick_id: str, root: str | None, selector: str) -> bool:
    js = """
    (cfg) => {
      const stamped = document.querySelector('[data-ab-pick="' + cfg.pid + '"]');
      if (!stamped) return false;
      const row = cfg.root ? stamped.closest(cfg.root) : stamped.parentElement;
      if (!row) return false;
      let el = null;
      try { el = row.querySelector(cfg.sel); } catch (e) { return false; }
      return el === stamped;
    }
    """
    try:
        return bool(await page.evaluate(js, {"sel": selector, "pid": pick_id, "root": root or ""}))
    except Exception:
        return False


async def _validate(page: Page, pick_id: str, mode: str, root: str | None, selector: str) -> bool:
    if not selector:
        return False
    if mode == "list":
        return await _validate_relative(page, pick_id, root, selector)
    return await _validate_single(page, pick_id, selector)


def _field_prompt(field: dict, outline: list[dict], mode: str, root: str | None) -> str:
    scope = (
        f"The element lives inside a list row matched by `{root}`; produce a "
        "selector RELATIVE to that row (it will be run as row.querySelector)."
        if mode == "list"
        else "Produce a selector run as document.querySelector on the whole page."
    )
    return (
        f"Field: {field.get('name', '')}\n"
        f"Meaning: {field.get('description') or '(no description)'}\n"
        f"Example value: {field.get('example') or '(none)'}\n\n"
        f"{scope}\n\n"
        f"DOM outline (element first, then ancestors):\n{_outline_text(outline)}\n\n"
        f"Return up to {MAX_SELECTORS} candidate selectors, best (most robust) first."
    )


async def compile_from_pick(
    page: Page, pick_ctx: dict, *, mode: str, root: str | None, field: dict
) -> list[str]:
    pick_id = pick_ctx.get("pick_id") or ""
    heuristics = list(pick_ctx.get("selectors") or [])
    ranked: list[str] = []

    if _llm_configured():
        try:
            images = await _screenshot_b64(page, pick_ctx.get("rect"))
            user = _field_prompt(field, pick_ctx.get("outline") or [], mode, root)
            async with _LLM_LOCK:
                out = await complete_json(_SYSTEM, user, _SELECTOR_SCHEMA, max_tokens=400, images=images)
            for sel in out.get("selectors") or []:
                if isinstance(sel, str) and await _validate(page, pick_id, mode, root, sel):
                    ranked.append(sel)
        except Exception as exc:
            log.warning("compile_from_pick LLM step failed: %s", exc)

    # Append validated heuristic candidates as lower-ranked fallbacks.
    for sel in heuristics:
        if sel not in ranked and await _validate(page, pick_id, mode, root, sel):
            ranked.append(sel)

    # Last resort: the raw heuristic list (unvalidated) so authoring never yields
    # an empty selector set — the field still works on the recorded page.
    if not ranked:
        ranked = heuristics

    return ranked[:MAX_SELECTORS]


async def compile_root_from_pick(page: Page, pick_ctx: dict) -> list[str]:
    generalized = pick_ctx.get("generalized") or ""
    pick_id = pick_ctx.get("pick_id") or ""
    ranked: list[str] = []

    async def _valid_root(sel: str) -> bool:
        js = """
        (cfg) => {
          let nodes = [];
          try { nodes = Array.from(document.querySelectorAll(cfg.sel)); } catch (e) { return false; }
          if (nodes.length < 2) return false;
          const stamped = document.querySelector('[data-ab-pick="' + cfg.pid + '"]');
          return !!stamped && nodes.some((n) => n === stamped || n.contains(stamped));
        }
        """
        try:
            return bool(await page.evaluate(js, {"sel": sel, "pid": pick_id}))
        except Exception:
            return False

    if _llm_configured():
        try:
            user = (
                "Produce robust CSS selectors that match EVERY repeated row of this "
                "list (2+ elements). Prefer a stable class over positional paths.\n\n"
                f"A representative row is described by:\n{_outline_text(pick_ctx.get('outline') or [])}\n\n"
                f"A heuristic guess is `{generalized}`. Return up to {MAX_SELECTORS} candidates, best first."
            )
            images = await _screenshot_b64(page, pick_ctx.get("rect"))
            async with _LLM_LOCK:
                out = await complete_json(_SYSTEM, user, _SELECTOR_SCHEMA, max_tokens=400, images=images)
            for sel in out.get("selectors") or []:
                if isinstance(sel, str) and await _valid_root(sel):
                    ranked.append(sel)
        except Exception as exc:
            log.warning("compile_root_from_pick LLM step failed: %s", exc)

    if generalized and generalized not in ranked and await _valid_root(generalized):
        ranked.append(generalized)
    if not ranked and generalized:
        ranked = [generalized]
    return ranked[:MAX_SELECTORS]


async def _page_outline_text(page: Page) -> str:
    # A coarse page description for reheal (no picked element). Reuses the visible
    # text of likely-relevant landmarks; kept small for token budget.
    js = "() => (document.body ? document.body.innerText : '').trim().slice(0, 4000)"
    try:
        return await page.evaluate(js)
    except Exception:
        return ""


async def _reheal_valid(page: Page, mode: str, root: str | None, selector: str) -> bool:
    js = """
    (cfg) => {
      function nonEmpty(el) { return !!el && (el.textContent || '').trim().length > 0; }
      if (cfg.mode === 'list') {
        let row = null;
        try { row = cfg.root ? document.querySelector(cfg.root) : document.body; } catch (e) { return false; }
        if (!row) return false;
        let el = null;
        try { el = row.querySelector(cfg.sel); } catch (e) { return false; }
        return nonEmpty(el);
      }
      let el = null;
      try { el = document.querySelector(cfg.sel); } catch (e) { return false; }
      return nonEmpty(el);
    }
    """
    try:
        return bool(await page.evaluate(js, {"sel": selector, "mode": mode, "root": root or ""}))
    except Exception:
        return False


async def reheal(page: Page, *, mode: str, root: str | None, field: dict) -> list[str] | None:
    if not _llm_configured():
        return None
    try:
        scope = (
            f"The value lives inside a list row matched by `{root}`; return a selector "
            "relative to that row."
            if mode == "list"
            else "Return a selector run as document.querySelector on the whole page."
        )
        user = (
            f"Field: {field.get('name', '')}\n"
            f"Meaning: {field.get('description') or '(no description)'}\n"
            f"Example value seen before: {field.get('example') or '(none)'}\n\n"
            f"{scope}\n\n"
            f"Page text (truncated):\n{await _page_outline_text(page)}\n\n"
            f"Return up to {MAX_SELECTORS} candidate CSS selectors, best first."
        )
        async with _LLM_LOCK:
            out = await complete_json(_SYSTEM, user, _SELECTOR_SCHEMA, max_tokens=400)
        validated = [
            sel
            for sel in (out.get("selectors") or [])
            if isinstance(sel, str) and await _reheal_valid(page, mode, root, sel)
        ]
        return validated[:MAX_SELECTORS] or None
    except Exception as exc:
        log.warning("reheal failed: %s", exc)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_selector_compiler.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Lint and commit**

```bash
cd backend; uv run ruff check app
git add backend/app/recorder/selector_compiler.py backend/tests/test_selector_compiler.py
git commit -m "feat(extract): add selector compiler (compile_from_pick, compile_root, reheal)"
```

---

### Task 6: Recorder `compile_root` / `compile_field` commands

**Files:**
- Modify: `backend/app/recorder/session.py:312-353` (`_handle_command`) and add handlers
- Test: `backend/tests/test_recorder_compile.py`

**Interfaces:**
- Consumes: `self._last_pick` (Task 4), `compile_from_pick` / `compile_root_from_pick` (Task 5), `self.page`.
- Produces two new commands on the recorder command channel:
  - `{"t": "compile_root"}` → publishes `{"t": "root_compiled", "roots": [...]}` (empty list if no pick).
  - `{"t": "compile_field", "mode", "root", "name", "description", "take"}` → publishes `{"t": "field_compiled", "field": {"name", "description", "take", "example", "selectors": [...]}}`.
  - The published `field.example` is `self._last_pick["preview"]`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_recorder_compile.py`:

```python
import uuid

from app.recorder import session as session_mod
from app.recorder.session import RecordingSession


def make_session():
    s = RecordingSession(str(uuid.uuid4()), str(uuid.uuid4()))
    s.published = []

    async def fake_publish(evt):
        s.published.append(evt)

    s._publish = fake_publish  # type: ignore[method-assign]
    s.page = object()  # only passed through to the (patched) compiler
    return s


async def test_compile_field_publishes_selectors(monkeypatch):
    s = make_session()
    s._last_pick = {
        "pick_id": "p1", "selectors": ["h3"], "preview": "Aparthotel",
        "generalized": ".card", "outline": [], "rect": None,
    }

    async def fake_compile(page, pick_ctx, *, mode, root, field):
        return [".card .title", ".title"]

    monkeypatch.setattr(session_mod, "compile_from_pick", fake_compile)
    await s._handle_command({
        "t": "compile_field", "mode": "single", "root": None,
        "name": "hotel_name", "description": "the hotel", "take": "text",
    })

    evt = s.published[-1]
    assert evt["t"] == "field_compiled"
    assert evt["field"]["name"] == "hotel_name"
    assert evt["field"]["selectors"] == [".card .title", ".title"]
    assert evt["field"]["example"] == "Aparthotel"


async def test_compile_root_publishes_roots(monkeypatch):
    s = make_session()
    s._last_pick = {"pick_id": "p1", "generalized": ".card", "outline": [], "rect": None, "selectors": []}

    async def fake_compile_root(page, pick_ctx):
        return [".card", ".list .card"]

    monkeypatch.setattr(session_mod, "compile_root_from_pick", fake_compile_root)
    await s._handle_command({"t": "compile_root"})

    evt = s.published[-1]
    assert evt["t"] == "root_compiled"
    assert evt["roots"] == [".card", ".list .card"]


async def test_compile_field_without_pick_is_noop(monkeypatch):
    s = make_session()
    s._last_pick = None
    await s._handle_command({"t": "compile_field", "mode": "single", "name": "x", "take": "text"})
    assert all(e["t"] != "field_compiled" for e in s.published)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_recorder_compile.py -v`
Expected: FAIL — `compile_field`/`compile_root` fall into the `else` branch (logged as unsupported); no `field_compiled`/`root_compiled` published.

- [ ] **Step 3: Import the compiler and add command branches**

In `backend/app/recorder/session.py`, add the import after line 18 (`from app.recorder.extraction import run_extraction`):

```python
from app.recorder.selector_compiler import compile_from_pick, compile_root_from_pick
```

Then in `_handle_command`, add two branches before the final `else:` (after the `test_extraction` branch, line 342-343):

```python
        elif ctype == "compile_root":
            await self._handle_compile_root()
        elif ctype == "compile_field":
            await self._handle_compile_field(cmd)
```

And add the two handler methods (place them after `_handle_test_extraction`, before `_start_authoring_task`):

```python
    async def _handle_compile_root(self) -> None:
        if self._last_pick is None or self.page is None:
            return
        try:
            roots = await compile_root_from_pick(self.page, self._last_pick)
        except Exception as exc:
            log.warning("compile_root failed: %s", exc)
            roots = [self._last_pick.get("generalized") or ""]
        await self._publish({"t": "root_compiled", "roots": [r for r in roots if r]})

    async def _handle_compile_field(self, cmd: dict) -> None:
        if self._last_pick is None or self.page is None:
            return
        name = cmd.get("name") or "field"
        take = cmd.get("take") or "text"
        field = {
            "name": name,
            "description": cmd.get("description"),
            "example": self._last_pick.get("preview"),
            "take": take,
        }
        try:
            selectors = await compile_from_pick(
                self.page,
                self._last_pick,
                mode=cmd.get("mode") or "single",
                root=cmd.get("root"),
                field=field,
            )
        except Exception as exc:
            log.warning("compile_field failed: %s", exc)
            selectors = list(self._last_pick.get("selectors") or [])
        await self._publish({
            "t": "field_compiled",
            "field": {
                "name": name,
                "description": cmd.get("description"),
                "take": take,
                "example": self._last_pick.get("preview"),
                "selectors": selectors,
            },
        })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_recorder_compile.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Lint and commit**

```bash
cd backend; uv run ruff check app
git add backend/app/recorder/session.py backend/tests/test_recorder_compile.py
git commit -m "feat(recorder): compile_root/compile_field commands drive pick-time selector compilation"
```

---

## Phase C — Replay self-heal

### Task 7: Replay routes `engine=="compiled"` through self-heal + cache

**Files:**
- Modify: `backend/app/recorder/replay.py:8` (imports), `:117-126` (signature), `:181-194` (extract step), add `_extract_compiled`
- Modify: `backend/app/workers/handlers.py:122` (pass `workflow_id`)
- Test: `backend/tests/test_replay.py`

**Interfaces:**
- Consumes: `run_extraction` (now selectors-aware, Task 3), `reheal` (Task 5), `read_cache`/`upsert_cache` (Task 2), `semantic_extract` (existing floor), `_merge_extraction` (existing).
- Produces: `replay_workflow(..., workflow_id: uuid.UUID | None = None)`. For `config["engine"] == "compiled"`: overlay cached selectors → run deterministic extraction → for still-null fields re-heal via LLM, validate, persist to cache, re-extract → fall back to `semantic_extract` floor for any remaining nulls. Legacy `engine` values and absent engine are unchanged.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_replay.py` (top-level imports `quote`, `uuid`, `llm_extract` already present from earlier tasks; add `selector_compiler` import at the top of the file if absent):

```python
from app.recorder import selector_compiler


async def test_compiled_engine_uses_stored_selectors(monkeypatch):
    # No heal needed: the stored selector resolves. reheal must NOT be called.
    async def boom(*a, **k):
        raise AssertionError("reheal should not run when selectors resolve")

    monkeypatch.setattr(selector_compiler, "reheal", boom)
    html = "<div class='card'><h3 class='title'>Physics 101</h3></div>"
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "single",
                "engine": "compiled",
                "fields": [{"name": "title", "selectors": [".card .title"], "take": "text"}],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"]["title"] == "Physics 101"


async def test_compiled_engine_heals_broken_selector(monkeypatch):
    # Stored selector misses; reheal returns a working one (no DB persistence
    # because workflow_id is None in this test).
    async def fake_reheal(page, *, mode, root, field):
        return [".card .title"]

    monkeypatch.setattr(selector_compiler, "reheal", fake_reheal)
    html = "<div class='card'><h3 class='title'>Physics 101</h3></div>"
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "single",
                "engine": "compiled",
                "fields": [{"name": "title", "selectors": [".stale-selector"], "take": "text"}],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"]["title"] == "Physics 101"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_replay.py::test_compiled_engine_uses_stored_selectors tests/test_replay.py::test_compiled_engine_heals_broken_selector -v`
Expected: FAIL — `engine=="compiled"` is unhandled, so it falls into the legacy selector path; the heal test returns `title: None`.

- [ ] **Step 3: Update imports and signature**

In `backend/app/recorder/replay.py`, change the extraction import (line 8) to add the compiled-path deps. Replace line 8:

```python
from app.recorder.llm_extract import llm_fill_missing, semantic_extract
from app.recorder.selector_cache import read_cache, upsert_cache
from app.recorder import selector_compiler
```

Add `from app.db import async_session` to the existing imports at the top of the file if not already present.

Change the `replay_workflow` signature (line 117-123) to add `workflow_id`:

```python
async def replay_workflow(
    workflow_snapshot: dict,
    params: dict,
    storage_state: dict | None,
    execution_id: uuid.UUID,
    headless: bool | None = None,
    workflow_id: uuid.UUID | None = None,
) -> dict[str, Any]:
```

- [ ] **Step 4: Add the `_extract_compiled` orchestrator**

In `backend/app/recorder/replay.py`, add this module-level function near `_merge_extraction`:

```python
def _null_fields(row: dict, fields: list[dict]) -> list[dict]:
    return [f for f in fields if row.get(f["name"]) is None]


async def _extract_compiled(
    page: Page, config: dict, workflow_id: uuid.UUID | None, ref: str
) -> Any:
    """Deterministic ranked-selector extraction, with per-field LLM self-heal for
    fields that come back null. Healed selectors are persisted to the selector
    cache (when workflow_id is known) so the next call is deterministic again.
    Never raises."""
    fields = config.get("fields") or []
    mode = config.get("mode", "single")
    root = (config.get("roots") or [None])[0] if config.get("roots") else config.get("root")

    # 1. Overlay cached (previously-healed) selectors on top of the authored ones.
    if workflow_id is not None:
        try:
            async with async_session() as db:
                cached = await read_cache(db, workflow_id)
            if cached:
                config = _apply_cache_overlay(config, cached, ref)
        except Exception as exc:
            log.warning("selector cache read failed: %s", exc)

    # 2. Deterministic extraction.
    data = await run_extraction(page, config)

    # 3. Self-heal null fields (single: whole dict; list: per row is too costly,
    #    so heal at the field level using the first row as the probe).
    if mode == "single" and isinstance(data, dict):
        for field in _null_fields(data, fields):
            healed = await selector_compiler.reheal(page, mode="single", root=None, field=field)
            if not healed:
                continue
            value = await run_extraction(
                page, {"mode": "single", "fields": [{**field, "selectors": healed}]}
            )
            if value.get(field["name"]) is not None:
                data[field["name"]] = value[field["name"]]
                if workflow_id is not None:
                    await _persist_heal(workflow_id, ref, field["name"], healed)

    elif mode == "list" and isinstance(data, list) and data:
        # Only heal fields null in EVERY row (a broken selector), not fields
        # legitimately absent from some rows.
        broken = [f for f in fields if all(r.get(f["name"]) is None for r in data)]
        for field in broken:
            healed = await selector_compiler.reheal(page, mode="list", root=root, field=field)
            if not healed:
                continue
            merged_field = {**field, "selectors": healed}
            rows = await run_extraction(
                page, {"mode": "list", "roots": config.get("roots") or ([root] if root else []),
                        "root": root, "fields": [merged_field]}
            )
            filled = False
            for i, r in enumerate(rows):
                if i < len(data) and r.get(field["name"]) is not None:
                    data[i][field["name"]] = r[field["name"]]
                    filled = True
            if filled and workflow_id is not None:
                await _persist_heal(workflow_id, ref, field["name"], healed)

    # 4. Last-resort value-extraction floor for anything still null.
    floor = await semantic_extract(page, config)
    if floor is not None:
        data = _merge_extraction(data, floor)
    return data


def _apply_cache_overlay(config: dict, cached: dict, ref: str) -> dict:
    fields = []
    for f in config.get("fields") or []:
        key = (ref, f["name"])
        if key in cached:
            fields.append({**f, "selectors": cached[key]})
        else:
            fields.append(f)
    return {**config, "fields": fields}


async def _persist_heal(workflow_id: uuid.UUID, ref: str, field_name: str, selectors: list[str]) -> None:
    try:
        async with async_session() as db:
            await upsert_cache(db, workflow_id, ref, field_name, selectors)
    except Exception as exc:
        log.warning("selector cache upsert failed: %s", exc)
```

Note: `_merge_extraction` overlays the LLM floor ON TOP of `data`. Because `semantic_extract` filters to text-eligible fields and returns null for absent ones, and `_merge_extraction` uses `{**selector_data, **llm_data}`, a null from the floor would overwrite a good selector value. To prevent that, change the floor merge to prefer existing non-null values: replace step 4's merge with a null-safe overlay:

```python
    floor = await selector_compiler_floor(page, config)
```

Instead of adding a new function, keep it inline and null-safe — replace the step-4 block above with:

```python
    # 4. Last-resort value-extraction floor for anything STILL null (never
    #    overwrites a value a selector already produced).
    if _has_null(data, fields):
        floor = await semantic_extract(page, config)
        if isinstance(floor, dict) and isinstance(data, dict):
            for k, v in floor.items():
                if data.get(k) is None and v is not None:
                    data[k] = v
        elif isinstance(floor, list) and isinstance(data, list):
            for i, frow in enumerate(floor):
                if i < len(data) and isinstance(frow, dict):
                    for k, v in frow.items():
                        if data[i].get(k) is None and v is not None:
                            data[i][k] = v
    return data
```

And add the helper:

```python
def _has_null(data: Any, fields: list[dict]) -> bool:
    names = [f["name"] for f in fields]
    if isinstance(data, dict):
        return any(data.get(n) is None for n in names)
    if isinstance(data, list):
        return any(row.get(n) is None for row in data for n in names)
    return False
```

(Delete the earlier `floor = await semantic_extract(...)` / `_merge_extraction` lines from step 4 — the null-safe block above replaces them. `_merge_extraction` remains used by the legacy `engine=="llm"` path only.)

- [ ] **Step 5: Route the extract step and pass `workflow_id`**

In `backend/app/recorder/replay.py`, replace the `extract` step block (lines 181-194):

```python
                elif stype == "extract":
                    ref = step.get("ref", "main")
                    config = extraction.get(ref)
                    if config:
                        await _wait_for_extraction_ready(page, config)
                        if config.get("engine") == "compiled":
                            data = await _extract_compiled(page, config, workflow_id, ref)
                        elif config.get("engine") == "llm":
                            llm_data = await semantic_extract(page, config)
                            if llm_data is None:
                                data = await run_extraction(page, config)
                            else:
                                selector_data = await run_extraction(page, config)
                                data = _merge_extraction(selector_data, llm_data)
                        else:
                            data = await run_extraction(page, config)
                            data = await llm_fill_missing(page, config, data)
```

In `backend/app/workers/handlers.py`, update the call (line 122) to pass the workflow id (available as `api.workflow_id` in scope):

```python
            replay_workflow(
                workflow_snapshot, params, storage_state, execution_id,
                headless=headless, workflow_id=api.workflow_id,
            ),
```

Also update `_wait_for_extraction_ready` (line 99-114) so compiled single-mode configs get a readiness gate: when `mode != "list"` and the first field has no `selector`, gate on its first `selectors[0]`. Replace the selector-selection block (lines 104-108):

```python
    if config.get("mode") == "list":
        roots = config.get("roots") or ([config.get("root")] if config.get("root") else [])
        selector = roots[0] if roots else None
    else:
        fields = config.get("fields") or []
        first = fields[0] if fields else None
        selector = None
        if first:
            selector = (first.get("selectors") or [None])[0] or first.get("selector")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_replay.py -v`
Expected: PASS — the two new compiled-engine tests plus all existing replay tests (legacy `engine=="llm"` and selector paths untouched).

- [ ] **Step 7: Full backend sweep + lint**

Run: `cd backend; uv run pytest`
Expected: full suite passes.

Run: `cd backend; uv run ruff check app`

- [ ] **Step 8: Commit**

```bash
git add backend/app/recorder/replay.py backend/app/workers/handlers.py backend/tests/test_replay.py
git commit -m "feat(replay): compiled-engine deterministic extraction with LLM self-heal + cache"
```

---

## Phase D — Frontend pick-driven wizard

### Task 8: Types + `useRecorder` wizard state machine

**Files:**
- Modify: `frontend/src/lib/types.ts:17-48`
- Modify: `frontend/src/hooks/useRecorder.ts`

**Interfaces:**
- Consumes: WS events `pick_result` (now carries `pick_id`), `field_compiled`, `root_compiled`.
- Produces:
  - Extended `PickCandidate` (`pick_id?: string`).
  - `ExtractionField.selectors?: string[]`; `ExtractionConfig.roots?: string[]`; `ExtractionConfig.engine?: 'compiled' | 'llm' | 'selector'`.
  - `WizardStep = 'idle' | 'choose-mode' | 'pick-root' | 'choose-values'`.
  - `useRecorder` returns `wizard` state (`{ step, mode, root, fields, lastCompiled }`) and actions: `startWizard()`, `chooseWizardMode(mode)`, `confirmRoot()`, `addValue(name, description, take)`, `undoPick()`, `finishWizard()`, plus low-level `compileRoot()`/`compileField(...)` senders. The finished config is pushed via the existing `setExtraction`.

- [ ] **Step 1: Extend the types**

In `frontend/src/lib/types.ts`, replace `PickCandidate` (lines 17-22):

```typescript
export interface PickCandidate {
  selectors: string[]
  preview: string | null
  count: number
  generalized: string | null
  pick_id?: string
}
```

Replace `ExtractionField` and `ExtractionConfig` (lines 33-48):

```typescript
export interface ExtractionField {
  name: string
  selector?: string
  selectors?: string[]
  take: string
  transform?: string
  description?: string
  example?: string
}

export interface ExtractionConfig {
  mode: 'single' | 'list'
  root?: string
  roots?: string[]
  engine?: 'compiled' | 'llm' | 'selector'
  scope?: string
  fields: ExtractionField[]
}

export type WizardStep = 'idle' | 'choose-mode' | 'pick-root' | 'choose-values'

export interface CompiledField {
  name: string
  description?: string
  take: string
  example: string | null
  selectors: string[]
}
```

- [ ] **Step 2: Add wizard state and event handling to `useRecorder`**

In `frontend/src/hooks/useRecorder.ts`, add to the imports (line 2-10):

```typescript
import type {
  CompiledField,
  ExtractionConfig,
  ExtractionField,
  ExtractionFieldSuggestion,
  Parameter,
  ParameterSuggestion,
  PickCandidate,
  RecorderStatus,
  Step,
  WizardStep,
} from '../lib/types'
```

Add wizard fields to `RecorderState` (after line 24, inside the interface):

```typescript
  wizardStep: WizardStep
  wizardMode: 'single' | 'list'
  wizardRoots: string[]
  wizardFields: ExtractionField[]
  lastCompiled: CompiledField | null
```

Add their initial values to the `useState` initializer (after line 42, inside the object):

```typescript
    wizardStep: 'idle',
    wizardMode: 'single',
    wizardRoots: [],
    wizardFields: [],
    lastCompiled: null,
```

In the `ws.onmessage` switch, add two cases (after the `pick_result` case, line 76):

```typescript
        case 'root_compiled':
          setState((s) => ({
            ...s,
            wizardRoots: msg.roots ?? [],
            wizardStep: 'choose-values',
          }))
          break
        case 'field_compiled':
          setState((s) => ({ ...s, lastCompiled: msg.field }))
          break
```

- [ ] **Step 3: Add wizard actions**

In `frontend/src/hooks/useRecorder.ts`, after `setMode` (line 141), add the wizard actions:

```typescript
  const startWizard = useCallback(() => {
    setState((s) => ({
      ...s,
      wizardStep: 'choose-mode',
      wizardMode: 'single',
      wizardRoots: [],
      wizardFields: [],
      lastCompiled: null,
      mode: 'pick',
      pickResult: null,
    }))
    send({ t: 'set_mode', mode: 'pick' })
  }, [send])

  const chooseWizardMode = useCallback((mode: 'single' | 'list') => {
    setState((s) => ({
      ...s,
      wizardMode: mode,
      wizardStep: mode === 'list' ? 'pick-root' : 'choose-values',
    }))
  }, [])

  const confirmRoot = useCallback(() => {
    // Ask the worker to compile the picked element into ranked root selectors;
    // the 'root_compiled' event advances the wizard to 'choose-values'.
    send({ t: 'compile_root' })
  }, [send])

  const compileValue = useCallback(
    (name: string, description: string, take: string) => {
      setState((s) => ({ ...s, lastCompiled: null }))
      send({
        t: 'compile_field',
        mode: state.wizardMode,
        root: state.wizardRoots[0] ?? null,
        name,
        description,
        take,
      })
    },
    [send, state.wizardMode, state.wizardRoots],
  )

  const addCompiledField = useCallback(() => {
    setState((s) => {
      if (!s.lastCompiled) return s
      const field: ExtractionField = {
        name: s.lastCompiled.name,
        description: s.lastCompiled.description,
        take: s.lastCompiled.take,
        example: s.lastCompiled.example ?? undefined,
        selectors: s.lastCompiled.selectors,
        transform: 'none',
      }
      return { ...s, wizardFields: [...s.wizardFields, field], lastCompiled: null, pickResult: null }
    })
  }, [])

  const undoPick = useCallback(() => {
    setState((s) => ({ ...s, pickResult: null, lastCompiled: null }))
  }, [])

  const finishWizard = useCallback(() => {
    setState((s) => {
      const config: ExtractionConfig = {
        mode: s.wizardMode,
        engine: 'compiled',
        roots: s.wizardMode === 'list' ? s.wizardRoots : undefined,
        fields: s.wizardFields,
      }
      send({ t: 'set_extraction', config })
      send({ t: 'set_mode', mode: 'record' })
      return { ...s, wizardStep: 'idle', mode: 'record', pickResult: null, lastCompiled: null }
    })
  }, [send])

  const cancelWizard = useCallback(() => {
    setState((s) => ({ ...s, wizardStep: 'idle', mode: 'record', pickResult: null, lastCompiled: null }))
    send({ t: 'set_mode', mode: 'record' })
  }, [send])
```

- [ ] **Step 4: Export the wizard state and actions**

In the returned object (lines 159-184), add the wizard state values (after `pickResult:` line 165):

```typescript
    wizardStep: state.wizardStep,
    wizardMode: state.wizardMode,
    wizardRoots: state.wizardRoots,
    wizardFields: state.wizardFields,
    lastCompiled: state.lastCompiled,
```

And the actions (after `setMode,` line 172):

```typescript
    startWizard,
    chooseWizardMode,
    confirmRoot,
    compileValue,
    addCompiledField,
    undoPick,
    finishWizard,
    cancelWizard,
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend; npm run build`
Expected: `tsc -b` passes (unused-so-far actions are exported and consumed in Task 9). If tsc flags an action as unused within the hook, that is fine — they are returned, not locally unused.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/hooks/useRecorder.ts
git commit -m "feat(ui): wizard state machine + compiled-field types in useRecorder"
```

---

### Task 9: Wizard UI in the recorder (pop-out + main tab)

**Files:**
- Modify: `frontend/src/components/RecorderPipCard.tsx`
- Modify: `frontend/src/pages/RecorderSession.tsx`
- Create: `frontend/src/components/ExtractionWizard.tsx`

**Interfaces:**
- Consumes: `useRecorder` wizard state/actions (Task 8).
- Produces: `ExtractionWizard` — a self-contained wizard panel used both in the pop-out and the main tab. Replaces the old "Use as list root / Add as field" buttons.

- [ ] **Step 1: Create the wizard component**

Create `frontend/src/components/ExtractionWizard.tsx`:

```tsx
import { useState } from 'react'
import type { CompiledField, ExtractionField, PickCandidate, WizardStep } from '../lib/types'
import { Button, cardClasses } from './ui'

interface ExtractionWizardProps {
  step: WizardStep
  mode: 'single' | 'list'
  fields: ExtractionField[]
  pickResult: PickCandidate | null
  lastCompiled: CompiledField | null
  disabled: boolean
  onStart: () => void
  onChooseMode: (mode: 'single' | 'list') => void
  onConfirmRoot: () => void
  onCompileValue: (name: string, description: string, take: string) => void
  onAddField: () => void
  onUndoPick: () => void
  onFinish: () => void
  onCancel: () => void
}

const TAKES = ['text', 'attr:href', 'attr:src', 'html']

export default function ExtractionWizard({
  step, mode, fields, pickResult, lastCompiled, disabled,
  onStart, onChooseMode, onConfirmRoot, onCompileValue, onAddField,
  onUndoPick, onFinish, onCancel,
}: ExtractionWizardProps) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [take, setTake] = useState('text')

  if (step === 'idle') {
    return (
      <Button variant="ink" size="sm" onClick={onStart} disabled={disabled}>
        Pick element
      </Button>
    )
  }

  return (
    <div className={`${cardClasses({ variant: 'callout', accent: 'blue' })} space-y-2`}>
      {step === 'choose-mode' && (
        <div className="space-y-2">
          <p className="text-xs text-ink/70">What are you extracting?</p>
          <div className="flex gap-2">
            <Button variant="ink" size="sm" onClick={() => onChooseMode('single')}>Single record</Button>
            <Button variant="ink" size="sm" onClick={() => onChooseMode('list')}>List of records</Button>
          </div>
        </div>
      )}

      {step === 'pick-root' && (
        <div className="space-y-2">
          <p className="text-xs text-ink/70">Click one repeating row in the browser, then confirm the root.</p>
          {pickResult && (
            <>
              <p className="truncate font-mono text-xs text-ink/80">{pickResult.selectors[0]}</p>
              <p className="text-xs text-ink/60">{pickResult.count} similar element(s)</p>
              <div className="flex gap-2">
                <Button variant="ink" size="sm" onClick={onConfirmRoot}>Use as root</Button>
                <Button size="sm" onClick={onUndoPick}>Undo pick</Button>
              </div>
            </>
          )}
        </div>
      )}

      {step === 'choose-values' && (
        <div className="space-y-2">
          <p className="text-xs text-ink/70">
            Click a value in the browser{mode === 'list' ? ' (inside a row)' : ''}, name it, then add it.
          </p>
          {fields.length > 0 && (
            <ul className="space-y-0.5 text-xs text-ink/70">
              {fields.map((f) => (
                <li key={f.name} className="font-mono">✓ {f.name}</li>
              ))}
            </ul>
          )}
          {pickResult && (
            <div className="space-y-1.5">
              <p className="text-xs text-ink/60">
                Picked{pickResult.preview ? `: "${pickResult.preview.slice(0, 40)}"` : ''}
              </p>
              <input
                type="text" value={name} disabled={disabled}
                placeholder="field name, e.g. price"
                onChange={(e) => setName(e.target.value)}
                className="w-full rounded border border-sand bg-cream px-2 py-1 text-xs"
              />
              <input
                type="text" value={description} disabled={disabled}
                placeholder="what it is, e.g. nightly price in BDT"
                onChange={(e) => setDescription(e.target.value)}
                className="w-full rounded border border-sand bg-cream px-2 py-1 text-xs"
              />
              <select
                value={take} disabled={disabled}
                onChange={(e) => setTake(e.target.value)}
                className="w-full rounded border border-sand bg-cream px-2 py-1 text-xs"
              >
                {TAKES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
              <div className="flex gap-2">
                {lastCompiled ? (
                  <Button variant="ink" size="sm" onClick={() => { onAddField(); setName(''); setDescription(''); setTake('text') }}>
                    Add this value
                  </Button>
                ) : (
                  <Button variant="ink" size="sm" disabled={disabled || !name} onClick={() => onCompileValue(name, description, take)}>
                    Compile selector
                  </Button>
                )}
                <Button size="sm" onClick={onUndoPick}>Undo pick</Button>
              </div>
              {lastCompiled && (
                <p className="truncate font-mono text-[11px] text-ink/60">{lastCompiled.selectors[0]}</p>
              )}
            </div>
          )}
          <div className="flex gap-2 border-t border-sand pt-2">
            <Button variant="primary" size="sm" disabled={fields.length === 0} onClick={onFinish}>Done</Button>
            <Button variant="danger-ghost" size="sm" onClick={onCancel}>Cancel</Button>
          </div>
        </div>
      )}
    </div>
  )
}
```

Note: the "Compile selector" → "Add this value" two-step gives the user the quick-undo window and shows the compiled selector before committing. If `cardClasses`/`Button` variant names differ in `./ui`, match the names already used in `RecorderPipCard.tsx`.

- [ ] **Step 2: Use the wizard in the pop-out card**

In `frontend/src/components/RecorderPipCard.tsx`, replace the props interface and the old pick block. Change `RecorderPipCardProps` to drop `onUseAsListRoot`/`onAddField`/`pickResult`-specific handlers and accept a single `wizard` prop object plus `onSetMode` for the Record button. Concretely, replace lines 6-19 (the interface) with:

```tsx
import type { RecorderStatus, Step } from '../lib/types'
import RecorderStepList from './RecorderStepList'
import ExtractionWizard from './ExtractionWizard'
import { Badge, Button, cardClasses } from './ui'
import type { ComponentProps } from 'react'

interface RecorderPipCardProps {
  status: RecorderStatus
  steps: Step[]
  interactive: boolean
  wizard: ComponentProps<typeof ExtractionWizard>
  onRecord: () => void
  onUndo: (i: number) => void
  onMarkParam: (stepI: number, name: string) => void
  onSave: () => void
  onCancel: () => void
}
```

Replace the mode buttons + pick block (lines 52-92) with:

```tsx
      <div className="flex items-center gap-2">
        <Button variant="default" size="sm" onClick={onRecord} disabled={!interactive}>
          Record
        </Button>
        <ExtractionWizard {...wizard} disabled={!interactive} />
      </div>
```

Remove the now-unused destructured props (`mode`, `pickResult`, `onSetMode`, `onUseAsListRoot`, `onAddField`) from the function signature and keep `status, steps, interactive, wizard, onRecord, onUndo, onMarkParam, onSave, onCancel`.

- [ ] **Step 3: Drive the wizard from `RecorderSession`**

In `frontend/src/pages/RecorderSession.tsx`:

Pull the wizard state/actions from `useRecorder` (extend the destructure at lines 20-43):

```typescript
    wizardStep,
    wizardMode,
    wizardFields,
    lastCompiled,
    startWizard,
    chooseWizardMode,
    confirmRoot,
    compileValue,
    addCompiledField,
    undoPick,
    finishWizard,
    cancelWizard,
```

Build a single `wizardProps` object to share between the main tab and the pop-out (add after `const interactive = ...`, line 44):

```typescript
  const wizardProps = {
    step: wizardStep,
    mode: wizardMode,
    fields: wizardFields,
    pickResult,
    lastCompiled,
    disabled: !interactive,
    onStart: startWizard,
    onChooseMode: chooseWizardMode,
    onConfirmRoot: confirmRoot,
    onCompileValue: compileValue,
    onAddField: addCompiledField,
    onUndoPick: undoPick,
    onFinish: finishWizard,
    onCancel: cancelWizard,
  }
```

Delete the old `addFieldFromPick` and `useAsListRoot` functions (lines 78-92) and the old Pick-mode block on the main tab (lines 162-217), replacing that block with:

```tsx
      <div className="mb-4 flex items-center gap-2">
        <Button variant="default" size="sm" onClick={() => setMode('record')} disabled={!interactive}>
          Record
        </Button>
        <ExtractionWizard {...wizardProps} />
        <div className="ml-auto flex items-center gap-2">
          {pipSupported && (
            <Button
              variant="ghost" size="sm"
              onClick={() => (pipWindow ? closePip() : openPip(380, 560))}
              disabled={!interactive}
            >
              {pipWindow ? 'Close floating controls' : 'Pop out controls'}
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={bringToFront} disabled={!interactive}>
            Bring window to front
          </Button>
        </div>
      </div>
```

Add the import at the top: `import ExtractionWizard from '../components/ExtractionWizard'`.

Update the `RecorderPipCard` render (lines 313-330) to the new props:

```tsx
      {pipWindow &&
        createPortal(
          <RecorderPipCard
            status={status}
            steps={steps}
            interactive={interactive}
            wizard={wizardProps}
            onRecord={() => setMode('record')}
            onUndo={undoStep}
            onMarkParam={markParam}
            onSave={save}
            onCancel={cancel}
          />,
          pipWindow.document.body,
        )}
```

- [ ] **Step 4: Typecheck, lint, and verify in the browser**

Run: `cd frontend; npm run build`
Expected: tsc + vite clean. Fix any prop mismatches surfaced by tsc.

Run: `cd frontend; npm run lint`
Expected: oxlint clean.

Browser verification (use the preview tools): start the dev server, open a recorder session, and confirm the wizard flow renders — "Pick element" → Single/List choice → (list) pick-root → choose-values with inline name/description, Compile → Add → Done. Take a screenshot of the choose-values step for the user.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ExtractionWizard.tsx frontend/src/components/RecorderPipCard.tsx frontend/src/pages/RecorderSession.tsx
git commit -m "feat(ui): pick-driven extraction wizard replaces single/list pick buttons"
```

---

### Task 10: Remove Single/List + engine toggles; show compiled selectors

**Files:**
- Modify: `frontend/src/components/ExtractionEditor.tsx`
- Modify: `frontend/src/pages/WorkflowEditor.tsx`

**Interfaces:**
- Consumes: `ExtractionConfig` (now with `roots`/`engine:'compiled'`), `ExtractionField` (now with `selectors`).
- Produces: an ExtractionEditor with NO mode/engine radios; fields show their compiled selector read-only, and name/description/take/transform stay editable. WorkflowEditor stops coercing `engine`.

- [ ] **Step 1: Remove the mode/engine radios in ExtractionEditor**

In `frontend/src/components/ExtractionEditor.tsx`, delete the control row that holds the Single/List radios and the Engine radios (added in the prior plan's Task 7). Keep the fields `<table>`. Where the old code read `extraction.mode`/`extraction.engine` for the radios, remove those handlers. The editor no longer changes `mode`, `root`, `roots`, or `engine` — it only edits `fields`.

Replace the per-row Selector `<input>` cell with a read-only display of the compiled selector (first of `selectors`, falling back to legacy `selector`):

```tsx
              <td className="py-1 pr-1.5">
                <span className="block max-w-[220px] truncate font-mono text-[11px] text-ink/60">
                  {field.selectors?.[0] ?? field.selector ?? '—'}
                </span>
              </td>
```

Keep the Name, Description, Take, and Transform cells editable exactly as before. Update the `<thead>` "Selector (optional)" header to read `Selector` (read-only now).

- [ ] **Step 2: Keep `addBlankField` valid without a selector**

In `ExtractionEditor.tsx`, if `addBlankField` sets `selector: ''`, leave it — the read-only cell shows `—`. New rows added manually in the editor have no compiled selector; they are edge cases (the wizard is the authoring path). No behavior change needed beyond Step 1.

- [ ] **Step 3: Stop coercing engine in WorkflowEditor**

In `frontend/src/pages/WorkflowEditor.tsx`, in the extraction-load block, ensure a loaded config keeps its stored `engine` verbatim (no `?? 'llm'` / `?? 'compiled'`), and `EMPTY_EXTRACTION` no longer needs an engine (new configs are authored by the wizard, which sets `engine:'compiled'`). Set:

```typescript
const EMPTY_EXTRACTION: ExtractionConfig = { mode: 'single', fields: [] }
```

And in the load coercion, replace any `engine: loaded.engine ?? '...'` with:

```typescript
            engine: loaded.engine,
```

Leave the `example` prefill logic from the prior plan intact.

- [ ] **Step 4: Also default `RecorderSession`'s `EMPTY_EXTRACTION`**

In `frontend/src/pages/RecorderSession.tsx`, change `EMPTY_EXTRACTION` (line 15) to:

```typescript
const EMPTY_EXTRACTION: ExtractionConfig = { mode: 'single', fields: [] }
```

(The wizard now produces the real config; this is just the pre-wizard empty state.)

- [ ] **Step 5: Typecheck, lint, verify**

Run: `cd frontend; npm run build` → tsc + vite clean.
Run: `cd frontend; npm run lint` → oxlint clean.

Browser verification: open an existing workflow's edit page; confirm the ExtractionEditor shows fields with read-only selectors and no Single/List or engine toggle, and that renaming a field / changing transform still saves. Screenshot for the user.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/ExtractionEditor.tsx frontend/src/pages/WorkflowEditor.tsx frontend/src/pages/RecorderSession.tsx
git commit -m "feat(ui): drop single/list + engine toggles; show compiled selectors read-only"
```

---

## Task 11: End-to-end verification (manual)

No code — confirms the full chain against a real dynamic page.

- [ ] **Step 1:** Ensure `.env` has `LLM_ENABLED=true`, `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=<key>`, `GEMINI_MODEL=<vision-capable Gemma model>`. Restart the worker (`scripts\dev.ps1`).
- [ ] **Step 2:** Record a workflow against a dynamic site. Use the wizard: Pick element → Single record → pick `hotel_name`/`location`/`price` (describe each) → Done. Confirm the saved config has `engine:"compiled"` and each field has a `selectors` array.
- [ ] **Step 3:** Run the published API's Try-it. Confirm real values (not nav/promo garbage), `200 OK`, and — via worker logs — that NO LLM call happened at replay (selectors resolved).
- [ ] **Step 4:** Simulate drift: edit one field's `selectors` in the DB to a broken value, run again. Confirm the value still returns (self-heal), and a row appears in `extraction_selector_cache`. Run once more and confirm no LLM call (cache overlay makes it deterministic again).
- [ ] **Step 5:** Set `LLM_ENABLED=false`, restart, run. Confirm the API still returns (stored selectors work offline) without erroring. Restore `LLM_ENABLED=true`.
- [ ] **Step 6:** `cd backend; uv run pytest` — full suite green.

---

## Self-Review

**Spec coverage:**
- Compile at pick time, LLM off the hot path → Tasks 4-6 (pick → compile), Task 7 (replay uses stored selectors). ✅
- DOM + screenshot crop, multimodal Gemma → Task 1 (`images`), Task 4 (outline+rect), Task 5 (`_screenshot_b64`, prompt). ✅
- Ranked validated selector list per field → Task 5 (validation + ranking), Task 3 (extraction tries the list). ✅
- Self-heal → keyed cache (not snapshot) → Task 2 (table+repo), Task 7 (`reheal` + `_persist_heal` + overlay). ✅
- Full list-mode support (roots + row-relative) → Task 3 (`roots[]`), Task 5 (`compile_root_from_pick`, relative validation), Task 7 (list heal). ✅
- Pick-driven wizard, inline naming, quick undo, explicit Done → Tasks 8-9. ✅
- Editor stays for touch-up; Single/List + engine toggle removed → Task 10. ✅
- Value-extraction retained as floor → Task 7 (null-safe `semantic_extract` overlay). ✅
- Never breaks a replay; works with `LLM_ENABLED=false` → Task 5 (returns fallback/None), Task 7 (deterministic path independent of LLM). ✅
- No mutation of authored snapshot; JSONB replaced → Task 7 (`{**config, ...}` copies; cache in its own table). ✅
- Back-compat (legacy `engine:"llm"` + single `selector`/`root`) → Task 3 (legacy keys), Task 7 (legacy branches untouched). ✅

**Placeholder scan:** No TBD/TODO; every code step shows full code; every command lists expected output. The two fixture-dependent tests (Task 3, and the `db_session` fixture name in Task 2) carry explicit "adjust to your fixture" notes rather than placeholders.

**Type consistency:** `complete_json(..., images=None)` defined in Task 1, called with `images=` in Task 5. `compile_from_pick`/`compile_root_from_pick`/`reheal` signatures defined in Task 5, called in Tasks 6-7. `read_cache`/`upsert_cache` defined in Task 2, used in Task 7. `field_compiled`/`root_compiled` events emitted in Task 6, handled in Task 8. `ExtractionField.selectors`/`ExtractionConfig.roots`/`engine:'compiled'` defined in Task 8, produced by Task 6 and consumed by Tasks 3, 7, 9, 10. `WizardStep`/`CompiledField` defined in Task 8, consumed in Task 9.

# LLM-First Semantic Extraction (Gemma/Gemini) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make extraction robust on dynamic/personalized pages by having the LLM read the page's visible text and return the user's named fields as JSON, instead of trusting brittle positional CSS selectors captured at record time.

**Architecture:** The recorded CSS-selector path stays intact as a fast/offline fallback. A new `engine: "llm"` marker on the extraction config routes replay through a new `semantic_extract()` that sends the page's `innerText` (+ per-field name/description/example) to a Google-served model (Gemma 4 31B by default) via the existing OpenAI-compatible LLM client. Extraction never raises: if the LLM is unavailable or errors, replay falls back to the selector path. No DB migration — the extraction config is already a free-form JSONB dict.

**Tech Stack:** FastAPI + async worker (Playwright), `openai` AsyncOpenAI client pointed at Google AI Studio's OpenAI-compat endpoint, React + Vite + TypeScript + Tailwind v4, pytest (async, `asyncio_mode=auto`).

## Global Constraints

- Playwright runs ONLY in the worker process — never in FastAPI, never in Docker.
- Secrets only via `app/config.py` / `.env` (gitignored). Never commit `.env`. `.env.example` carries placeholders only.
- LLM job concurrency is 1 — serialize LLM calls with the existing `_LLM_LOCK` semaphore.
- Extraction failure must NEVER break a replay (mirrors "spec generation failure must never block publishing"). Any LLM error degrades to the selector path or nulls.
- Headless replay browsers launch with `--disable-gpu`; the app must work with `LLM_ENABLED=false`.
- Tailwind is v4 (CSS-first, no `tailwind.config.js`).
- Extraction config is stored as JSONB and validated loosely (`extraction: dict`) — new keys (`engine`, `description`, `example`, `scope`) need no schema/migration change.
- Backend tests: `cd backend; uv run pytest`. Lint: `cd backend; uv run ruff check app`. Frontend typecheck/build: `cd frontend; npm run build`.

---

## File Structure

- `backend/app/config.py` — add three `gemini_*` settings (modify).
- `backend/app/llm/client.py` — add the `gemini` provider branch to client/model/prompt-schema selection (modify).
- `backend/app/recorder/llm_extract.py` — add `_llm_configured` gemini branch + `semantic_extract()` and its single/list helpers (modify; reuses existing `_apply_transform`, `_item_texts`, `_build_schema`, `_LLM_LOCK`).
- `backend/app/recorder/replay.py` — branch the `extract` step on `engine` (modify).
- `.env.example` — document the gemini/gemma keys (modify).
- `backend/tests/test_llm_provider.py` — provider-selection unit tests (create).
- `backend/tests/test_llm_semantic.py` — semantic extractor unit tests (create).
- `backend/tests/test_replay.py` — add engine-branch integration tests (modify).
- `frontend/src/lib/types.ts` — extend `ExtractionField` / `ExtractionConfig` (modify).
- `frontend/src/components/ExtractionEditor.tsx` — add Description input, make Selector optional (modify).
- `frontend/src/pages/WorkflowEditor.tsx` — default `engine:'llm'`, prefill examples from `sample_output` (modify).
- `frontend/src/pages/RecorderSession.tsx` — default `engine:'llm'` in `EMPTY_EXTRACTION` (modify).

---

### Task 1: Gemini/Gemma provider wiring

**Files:**
- Modify: `backend/app/config.py:47-52`
- Modify: `backend/app/llm/client.py:16-36`, `backend/app/llm/client.py:83-97`
- Modify: `.env.example:27-36`
- Test: `backend/tests/test_llm_provider.py`

**Interfaces:**
- Consumes: `app.config.settings` (pydantic settings singleton).
- Produces:
  - `settings.gemini_base_url: str`, `settings.gemini_api_key: str`, `settings.gemini_model: str`
  - `app.llm.client._build_client() -> AsyncOpenAI` (now handles `gemini`)
  - `app.llm.client._model_name() -> str` (new helper)
  - `app.llm.client._uses_prompt_schema() -> bool` (new helper; True for `craftx` and `gemini`)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_llm_provider.py`:

```python
from app.llm import client


def test_gemini_client_uses_gemini_base_url_and_key(monkeypatch):
    monkeypatch.setattr(client.settings, "llm_provider", "gemini")
    monkeypatch.setattr(client.settings, "gemini_base_url", "https://gen.example/v1beta/openai/")
    monkeypatch.setattr(client.settings, "gemini_api_key", "aistudio-key")
    built = client._build_client()
    assert str(built.base_url).startswith("https://gen.example/v1beta/openai/")
    assert built.api_key == "aistudio-key"


def test_gemini_model_name_is_configured_model(monkeypatch):
    monkeypatch.setattr(client.settings, "llm_provider", "gemini")
    monkeypatch.setattr(client.settings, "gemini_model", "gemma-4-31b-it")
    assert client._model_name() == "gemma-4-31b-it"


def test_gemini_embeds_schema_in_prompt(monkeypatch):
    # Google's OpenAI-compat layer is unreliable with response_format json_schema,
    # so gemini must take the same prompt-embedded-schema path as craftx.
    monkeypatch.setattr(client.settings, "llm_provider", "gemini")
    assert client._uses_prompt_schema() is True
    monkeypatch.setattr(client.settings, "llm_provider", "llama")
    assert client._uses_prompt_schema() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; uv run pytest tests/test_llm_provider.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'gemini_base_url'` and `module 'app.llm.client' has no attribute '_model_name'`.

- [ ] **Step 3: Add the settings**

In `backend/app/config.py`, after the craftx settings (line 52, `craftx_model: str = ""`), add:

```python
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    gemini_api_key: str = ""
    gemini_model: str = ""
```

- [ ] **Step 4: Add the provider branches in the client**

In `backend/app/llm/client.py`, replace `_build_client` and the `MODEL_NAME` assignment (lines 16-36) with:

```python
def _build_client() -> AsyncOpenAI:
    if settings.llm_provider == "craftx":
        return AsyncOpenAI(
            base_url=settings.craftx_base_url,
            api_key=settings.craftx_api_key,
            timeout=180.0,
            max_retries=1,
        )
    if settings.llm_provider == "gemini":
        return AsyncOpenAI(
            base_url=settings.gemini_base_url,
            api_key=settings.gemini_api_key,
            timeout=180.0,
            max_retries=1,
        )
    return AsyncOpenAI(
        base_url=settings.llama_base_url,
        api_key="sk-local",  # ignored by llama-server unless --api-key is set
        timeout=180.0,
        max_retries=1,
    )


client = _build_client()


def _model_name() -> str:
    # llama-server serves a single model, so its name is cosmetic; craftx and
    # gemini (a Google-served Gemma/Gemini model) require the real model name.
    if settings.llm_provider == "craftx":
        return settings.craftx_model
    if settings.llm_provider == "gemini":
        return settings.gemini_model
    return "local"


MODEL_NAME = _model_name()


def _uses_prompt_schema() -> bool:
    # craftx 502s on any response_format; Google's OpenAI-compat layer is
    # unreliable with json_schema. Both embed the schema in the prompt and lean
    # on _extract_json to strip the code fence / prose the model wraps it in.
    return settings.llm_provider in ("craftx", "gemini")
```

- [ ] **Step 5: Route `complete_json` through the new helper**

In `backend/app/llm/client.py`, in `complete_json` (lines 83-97), replace the branch condition:

```python
async def complete_json(system: str, user: str, schema: dict, max_tokens: int = 2000) -> dict:
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
```

(The rest of `complete_json` is unchanged.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_llm_provider.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Document the keys in `.env.example`**

In `.env.example`, update the provider comment on line 28 and append a gemini section after the craftx block (after line 36):

Change line 28 to:
```
# Provider switch: "craftx" (hosted), "gemini" (Google AI Studio / Gemma), or "llama" (local llama.cpp).
```

Append after line 36:
```
# gemini / gemma (Google AI Studio, OpenAI-compatible) — used when LLM_PROVIDER=gemini
GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
GEMINI_API_KEY=your-aistudio-key
GEMINI_MODEL=gemma-4-31b-it
```

- [ ] **Step 8: Lint and commit**

```bash
cd backend; uv run ruff check app
git add backend/app/config.py backend/app/llm/client.py backend/tests/test_llm_provider.py .env.example
git commit -m "feat(llm): add gemini/gemma provider (Google OpenAI-compatible endpoint)"
```

---

### Task 2: Semantic extractor (`semantic_extract`)

**Files:**
- Modify: `backend/app/recorder/llm_extract.py:40-45` (`_llm_configured`) and append new functions
- Test: `backend/tests/test_llm_semantic.py`

**Interfaces:**
- Consumes: `complete_json` (Task 1 leaves its signature unchanged), existing `_apply_transform`, `_item_texts`, `_build_schema`, `_LLM_LOCK`, `MAX_ITEMS` in this module; `settings.gemini_api_key`/`settings.gemini_model` (Task 1).
- Produces: `async def semantic_extract(page: Page, config: dict) -> dict | list | None`
  - single mode → `dict` of `{field_name: value|null}`
  - list mode → `list[dict]`, one per `config["root"]` match (LLM-filled)
  - returns `None` when the LLM is not configured or any error occurs (caller falls back to selectors)

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_llm_semantic.py`:

```python
from app.recorder import llm_extract


class FakePage:
    """Minimal stand-in: semantic_extract only calls page.evaluate()."""

    def __init__(self, payload):
        self._payload = payload

    async def evaluate(self, js, arg):
        return self._payload


SINGLE_CONFIG = {
    "mode": "single",
    "engine": "llm",
    "fields": [
        {"name": "hotel_name", "description": "name of the first featured hotel"},
        {"name": "location", "description": "city and country"},
        {"name": "price", "description": "starting price in BDT", "transform": "number"},
    ],
}


async def test_single_mode_maps_named_fields_and_transforms(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)
    captured = {}

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        captured["user"] = user
        return {
            "hotel_name": "Aparthotel Stare Miasto",
            "location": "Old Town, Poland, Krakow",
            "price": "BDT 14,049",
        }

    monkeypatch.setattr(llm_extract, "complete_json", fake_complete_json)

    page = FakePage({
        "title": "Booking.com",
        "url": "https://www.booking.com/",
        "text": "Homes guests love\nAparthotel Stare Miasto\nOld Town, Poland, Krakow\nStarting from BDT 14,049",
    })
    out = await llm_extract.semantic_extract(page, SINGLE_CONFIG)

    assert out["hotel_name"] == "Aparthotel Stare Miasto"
    assert out["location"] == "Old Town, Poland, Krakow"
    assert out["price"] == 14049  # number transform applied after the LLM
    # The field description reaches the prompt so the model knows what "price" means.
    assert "starting price in BDT" in captured["user"]


async def test_returns_none_when_llm_not_configured(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: False)
    out = await llm_extract.semantic_extract(FakePage({"title": "", "url": "", "text": "x"}), SINGLE_CONFIG)
    assert out is None


async def test_returns_none_on_llm_error(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)

    async def boom(*a, **k):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(llm_extract, "complete_json", boom)
    out = await llm_extract.semantic_extract(FakePage({"title": "", "url": "", "text": "x"}), SINGLE_CONFIG)
    assert out is None  # caller will fall back to the selector path


async def test_list_mode_fills_every_item_by_index(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        return {"items": [{"index": 0, "name": "Alice"}, {"index": 1, "name": "Bob"}]}

    monkeypatch.setattr(llm_extract, "complete_json", fake_complete_json)

    config = {
        "mode": "list",
        "engine": "llm",
        "root": ".card",
        "fields": [{"name": "name", "description": "person name"}],
    }
    # In list mode the only page.evaluate call is _item_texts → returns the row texts.
    page = FakePage(["Alice — engineer", "Bob — designer"])
    out = await llm_extract.semantic_extract(page, config)
    assert out == [{"name": "Alice"}, {"name": "Bob"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_llm_semantic.py -v`
Expected: FAIL — `AttributeError: module 'app.recorder.llm_extract' has no attribute 'semantic_extract'`.

- [ ] **Step 3: Update `_llm_configured` for gemini**

In `backend/app/recorder/llm_extract.py`, replace `_llm_configured` (lines 40-45) with:

```python
def _llm_configured() -> bool:
    if not settings.llm_enabled:
        return False
    if settings.llm_provider == "craftx":
        return bool(settings.craftx_base_url and settings.craftx_api_key and settings.craftx_model)
    if settings.llm_provider == "gemini":
        return bool(settings.gemini_api_key and settings.gemini_model)
    return True
```

- [ ] **Step 4: Add the semantic extractor**

Append to `backend/app/recorder/llm_extract.py`:

```python
PAGE_TEXT_CAP = 12_000  # chars of page text sent in single mode (keeps us under Gemma's 16K TPM)


async def _page_text(page: Page, scope: str | None) -> dict:
    js = """
    (cfg) => {
      const el = cfg.scope ? document.querySelector(cfg.scope) : null;
      const base = el || document.body;
      return {
        title: document.title || '',
        url: window.location.href,
        text: ((base && base.innerText) || '').trim().slice(0, cfg.cap),
      };
    }
    """
    return await page.evaluate(js, {"scope": scope or "", "cap": PAGE_TEXT_CAP})


def _field_hint(field: dict) -> str:
    parts = []
    desc = field.get("description")
    if desc:
        parts.append(f"— {desc}")
    ex = field.get("example")
    if isinstance(ex, str) and ex.strip():
        parts.append(f"(example: {ex.strip()[:200]!r})")
    return (" " + " ".join(parts)) if parts else ""


def _single_prompt(fields: list[dict], page_data: dict) -> str:
    lines = [
        f"Extract these fields from the web page below.",
        f"Page title: {page_data.get('title', '')}",
        f"URL: {page_data.get('url', '')}",
        "",
    ]
    for f in fields:
        lines.append(f"- {f['name']}{_field_hint(f)}")
    lines.append("")
    lines.append(
        'Return JSON with exactly these keys: {"<field>": <value or null>, ...}. '
        "If a field is genuinely not present on the page, use null — never invent a value."
    )
    lines.append("")
    lines.append("Page text:")
    lines.append(page_data.get("text", ""))
    return "\n".join(lines)


def _list_prompt(fields: list[dict], items: list[tuple[int, str]]) -> str:
    lines = ["Extract these fields from each list item below:"]
    for f in fields:
        lines.append(f"- {f['name']}{_field_hint(f)}")
    lines.append("")
    lines.append(
        'Return JSON {"items": [{"index": <n>, ...fields}]} for exactly the indices shown. '
        "If an item genuinely does not contain a field, use null — never invent a value."
    )
    lines.append("")
    lines.append("Items:")
    for idx, text in items:
        lines.append(f"[index {idx}] {text}")
    return "\n".join(lines)


_SEMANTIC_SYSTEM = (
    "You extract structured field values from a web page's visible text. "
    "Return only valid JSON matching the schema. Never fabricate values."
)


async def _semantic_single(page: Page, config: dict, fields: list[dict]) -> dict:
    field_names = [f["name"] for f in fields]
    page_data = await _page_text(page, config.get("scope"))
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": field_names,
        "properties": {n: {"type": ["string", "null"]} for n in field_names},
    }
    user = _single_prompt(fields, page_data)
    async with _LLM_LOCK:
        out = await complete_json(_SEMANTIC_SYSTEM, user, schema, max_tokens=1000)
    transforms = {f["name"]: f.get("transform") for f in fields}
    return {n: _apply_transform(out.get(n), transforms.get(n)) for n in field_names}


async def _semantic_list(page: Page, config: dict, fields: list[dict]) -> list[dict]:
    field_names = [f["name"] for f in fields]
    texts = await _item_texts(page, config["root"])
    pending = [(i, t) for i, t in enumerate(texts) if t][:MAX_ITEMS]
    if not pending:
        return []
    if len(texts) > MAX_ITEMS:
        log.warning("semantic list extraction: capped at %s of %s items", MAX_ITEMS, len(texts))
    schema = _build_schema(field_names)
    user = _list_prompt(fields, pending)
    async with _LLM_LOCK:
        out = await complete_json(_SEMANTIC_SYSTEM, user, schema, max_tokens=min(4000, 200 * len(pending) + 500))
    by_index = {it.get("index"): it for it in out.get("items", []) if isinstance(it, dict)}
    transforms = {f["name"]: f.get("transform") for f in fields}
    return [
        {n: _apply_transform((by_index.get(i) or {}).get(n), transforms.get(n)) for n in field_names}
        for i in range(len(texts))
    ]


async def semantic_extract(page: Page, config: dict) -> Any:
    """LLM-first extraction: read the page's visible text and return the
    configured named fields. Returns None when the LLM is unavailable or any
    error occurs, so the caller can fall back to the selector path. Never raises."""
    if not _llm_configured():
        return None
    fields = config.get("fields") or []
    if not fields:
        return None
    try:
        if config.get("mode") == "list":
            if not config.get("root"):
                return None
            return await _semantic_list(page, config, fields)
        return await _semantic_single(page, config, fields)
    except Exception as exc:  # never let extraction break a replay
        log.warning("semantic extraction failed: %s", exc)
        return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_llm_semantic.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Run the existing extraction tests to confirm no regression**

Run: `cd backend; uv run pytest tests/test_llm_extract.py -v`
Expected: PASS (existing 4 tests unchanged).

- [ ] **Step 7: Lint and commit**

```bash
cd backend; uv run ruff check app
git add backend/app/recorder/llm_extract.py backend/tests/test_llm_semantic.py
git commit -m "feat(extract): add LLM-first semantic_extract for single and list modes"
```

---

### Task 3: Route the replay `extract` step through the engine

**Files:**
- Modify: `backend/app/recorder/replay.py:6-8` (import), `backend/app/recorder/replay.py:163-168` (extract step)
- Test: `backend/tests/test_replay.py`

**Interfaces:**
- Consumes: `semantic_extract` (Task 2), existing `run_extraction`, `llm_fill_missing`.
- Produces: replay behavior — `extraction.main.engine == "llm"` → `semantic_extract`, falling back to the selector path when it returns `None`; any other/absent engine → unchanged selector path.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_replay.py`:

```python
from urllib.parse import quote as _quote

from app.recorder import llm_extract


async def test_extract_llm_engine_reads_page_text(monkeypatch):
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        # The page text must have reached the prompt.
        assert "Aparthotel Stare Miasto" in user
        return {"hotel_name": "Aparthotel Stare Miasto", "price": "BDT 14,049"}

    monkeypatch.setattr(llm_extract, "complete_json", fake_complete_json)

    html = "<h3>Aparthotel Stare Miasto</h3><p>Starting from BDT 14,049</p>"
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{_quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "single",
                "engine": "llm",
                "fields": [
                    {"name": "hotel_name", "description": "the featured hotel name"},
                    {"name": "price", "description": "starting price", "transform": "number"},
                ],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"]["hotel_name"] == "Aparthotel Stare Miasto"
    assert result["data"]["price"] == 14049


async def test_llm_engine_falls_back_to_selectors_when_llm_down(fixture_site_url, monkeypatch):
    # engine is "llm" but the LLM is not configured → semantic_extract returns
    # None and replay must fall through to the recorded selector path.
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: False)
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"{fixture_site_url}/index.html"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "list",
                "engine": "llm",
                "root": ".book-item",
                "fields": [
                    {"name": "title", "selector": ".book-title", "take": "text"},
                    {"name": "price", "selector": ".book-price", "take": "text", "transform": "number"},
                ],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert len(result["data"]) == 3
    assert result["data"][0] == {"title": "Physics 101", "price": 350}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_replay.py::test_extract_llm_engine_reads_page_text tests/test_replay.py::test_llm_engine_falls_back_to_selectors_when_llm_down -v`
Expected: FAIL — the `extract` step ignores `engine` and runs the selector path, so `test_extract_llm_engine_reads_page_text` returns nulls/garbage (fake `complete_json` never called).

- [ ] **Step 3: Import `semantic_extract`**

In `backend/app/recorder/replay.py`, change the import (line 8):

```python
from app.recorder.llm_extract import llm_fill_missing, semantic_extract
```

- [ ] **Step 4: Branch the extract step on engine**

In `backend/app/recorder/replay.py`, replace the `extract` step block (lines 163-168):

```python
                elif stype == "extract":
                    config = extraction.get(step.get("ref", "main"))
                    if config:
                        await _wait_for_extraction_ready(page, config)
                        data = None
                        if config.get("engine") == "llm":
                            data = await semantic_extract(page, config)
                        if data is None:
                            data = await run_extraction(page, config)
                            data = await llm_fill_missing(page, config, data)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_replay.py -v`
Expected: PASS (existing replay tests + 2 new ones).

- [ ] **Step 6: Lint and commit**

```bash
cd backend; uv run ruff check app
git add backend/app/recorder/replay.py backend/tests/test_replay.py
git commit -m "feat(replay): route extract step through LLM engine with selector fallback"
```

---

### Task 4: Frontend — named fields + descriptions, marking optional

**Files:**
- Modify: `frontend/src/lib/types.ts:33-44`
- Modify: `frontend/src/components/ExtractionEditor.tsx`
- Modify: `frontend/src/pages/WorkflowEditor.tsx:39`, `:60-66`, `:80-83`
- Modify: `frontend/src/pages/RecorderSession.tsx:15`

**Interfaces:**
- Consumes: `ExtractionConfig` / `ExtractionField` types; the workflow load response `wf.extraction.main` and `wf.sample_output`.
- Produces: extraction configs saved with `engine: "llm"`, per-field `description` and optional `example`; backend reads these in Task 2.

- [ ] **Step 1: Extend the types**

In `frontend/src/lib/types.ts`, replace the `ExtractionField` and `ExtractionConfig` interfaces (lines 33-44):

```typescript
export interface ExtractionField {
  name: string
  selector: string
  take: string
  transform?: string
  description?: string
  example?: string
}

export interface ExtractionConfig {
  mode: 'single' | 'list'
  root?: string
  engine?: 'llm' | 'selector'
  scope?: string
  fields: ExtractionField[]
}
```

- [ ] **Step 2: Add a Description column and make Selector optional in the editor**

In `frontend/src/components/ExtractionEditor.tsx`:

In `addBlankField`, include a description so new rows carry the key:

```typescript
  function addBlankField() {
    onChange({
      ...extraction,
      fields: [
        ...extraction.fields,
        { name: `field${extraction.fields.length + 1}`, description: '', selector: '', take: 'text', transform: 'none' },
      ],
    })
  }
```

In the `<thead>` row, add a Description header before Selector and relabel Selector, so the header row reads:

```tsx
          <tr className="text-left">
            <th className="pb-1 text-[11px] font-bold uppercase tracking-wide text-ink/60">Name</th>
            <th className="pb-1 text-[11px] font-bold uppercase tracking-wide text-ink/60">Description</th>
            <th className="pb-1 text-[11px] font-bold uppercase tracking-wide text-ink/60">Selector (optional)</th>
            <th className="pb-1 text-[11px] font-bold uppercase tracking-wide text-ink/60">Take</th>
            <th className="pb-1 text-[11px] font-bold uppercase tracking-wide text-ink/60">Transform</th>
            <th />
          </tr>
```

In the `<tbody>` row, add the Description cell between the Name cell and the Selector cell:

```tsx
              <td className="py-1 pr-1.5">
                <input
                  type="text"
                  disabled={disabled}
                  value={field.description ?? ''}
                  placeholder="what this field is, e.g. starting price in BDT"
                  onChange={(e) => updateField(i, { description: e.target.value })}
                  className={CELL_INPUT}
                />
              </td>
```

- [ ] **Step 3: Default engine to `llm` and prefill examples in WorkflowEditor**

In `frontend/src/pages/WorkflowEditor.tsx`, change `EMPTY_EXTRACTION` (line 39):

```typescript
const EMPTY_EXTRACTION: ExtractionConfig = { mode: 'list', root: '', engine: 'llm', fields: [] }
```

Replace the extraction load line (line 65, `setExtraction(wf.extraction.main ?? EMPTY_EXTRACTION)`) with a coercion that upgrades legacy configs to the llm engine and prefills each field's `example` from the record-time sample (which was captured while the correct page was live):

```typescript
        const loaded = wf.extraction.main
        if (loaded) {
          const sample = (wf.sample_output ?? null) as Record<string, unknown> | null
          setExtraction({
            ...loaded,
            engine: loaded.engine ?? 'llm',
            fields: loaded.fields.map((f) => ({
              ...f,
              example:
                f.example ??
                (sample && typeof sample[f.name] === 'string' ? (sample[f.name] as string) : undefined),
            })),
          })
        } else {
          setExtraction(EMPTY_EXTRACTION)
        }
```

Note: this requires `wf.sample_output` to be present on the loaded workflow object. If the local `Workflow`/`WorkflowDetail` type used at line 34 does not already include `sample_output`, add `sample_output?: unknown` to that interface. (The backend `WorkflowOut` already returns it.)

- [ ] **Step 4: Default engine to `llm` in the recorder page**

In `frontend/src/pages/RecorderSession.tsx`, change `EMPTY_EXTRACTION` (line 15):

```typescript
const EMPTY_EXTRACTION: ExtractionConfig = { mode: 'list', root: '', engine: 'llm', fields: [] }
```

- [ ] **Step 5: Typecheck / build**

Run: `cd frontend; npm run build`
Expected: `tsc -b` passes with no type errors and `vite build` completes. If `sample_output` triggers a type error, apply the interface note in Step 3.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/components/ExtractionEditor.tsx frontend/src/pages/WorkflowEditor.tsx frontend/src/pages/RecorderSession.tsx
git commit -m "feat(ui): named fields + descriptions in extraction editor, engine defaults to llm"
```

---

### Task 5: End-to-end verification against the real Booking.com API (manual)

This task has no code — it confirms the whole chain works against the user's actual failing API. Do it after Tasks 1-4 are merged.

**Files:** none (config + manual UI + browser preview).

- [ ] **Step 1: Point the app at Gemma**

Ensure `.env` (repo root, gitignored) has:

```
LLM_ENABLED=true
LLM_PROVIDER=gemini
GEMINI_API_KEY=<the AI Studio key already set by the user>
GEMINI_MODEL=gemma-4-31b-it
```

- [ ] **Step 2: Restart the worker** (it owns Playwright + LLM). From the project root:

Run: `scripts\dev.ps1` (or restart the `python -m app.workers.main` process).

- [ ] **Step 3: Sanity-check the provider loads**

Run: `cd backend; uv run python -c "from app.llm.client import MODEL_NAME, client; print(MODEL_NAME, client.base_url)"`
Expected: prints `gemma-4-31b-it https://generativelanguage.googleapis.com/v1beta/openai/`.

- [ ] **Step 4: Re-author the Booking extraction**

In the app, open the Booking workflow's **Edit recording** page (`/workflows/<id>/edit`). For each field:
- Rename `field1…field5` to meaningful names: `hotel_name`, `location`, `price` (delete the extra `field4`/`field5` rows if unused).
- Fill the **Description** for each, e.g. `price` → "starting nightly price of the first featured hotel, in BDT".
- Leave Selector blank (optional now). Set `price`'s Transform to `number`.
- Save. Confirm the saved config carries `engine: "llm"`.

- [ ] **Step 5: Run the API and verify real values**

Open the API's **Try it** page and click **Run**. Confirm the JSON now returns the actual featured hotel's `hotel_name`, `location`, and numeric `price` — not the nav/promo garbage from before. Verify the run status is `200 OK`.

- [ ] **Step 6: Confirm graceful degradation**

Temporarily set `LLM_ENABLED=false` in `.env`, restart the worker, and Run again. Confirm the call still returns (falls back to the selector path / nulls) rather than erroring. Restore `LLM_ENABLED=true` and restart.

- [ ] **Step 7: Final backend test sweep**

Run: `cd backend; uv run pytest`
Expected: full suite passes.

---

## Self-Review

**Spec coverage:**
- LLM-first semantic extraction (single + list) → Task 2. ✅
- Named fields + optional descriptions, marking optional → Task 4 (Description column, Selector optional) + Task 2 (`_field_hint` uses name/description/example). ✅
- Gemma 4 31B default via Google OpenAI-compat endpoint, Flash Lite swappable via `GEMINI_MODEL` → Task 1 (provider branch) + Task 5 (.env). ✅
- Page text capped (~12k chars) for Gemma's 16K TPM → Task 2 (`PAGE_TEXT_CAP`). ✅
- Selectors retained for legacy + link/image (`attr:`) fields, LLM owns text/number → Task 3 (fallback to `run_extraction`) + Task 2 (`transform` applied post-LLM). ✅
- Never breaks a replay → Task 2 (`semantic_extract` returns None on error) + Task 3 (falls back to selector path). ✅
- No DB migration (JSONB free dict) → confirmed in Global Constraints; no migration task. ✅
- Backward compatibility (legacy snapshots run selectors) → Task 3 (only `engine == "llm"` diverts); editor upgrades on save (Task 4 Step 3). ✅

**Placeholder scan:** No TBD/TODO; every code step shows full code; every command has expected output.

**Type consistency:** `semantic_extract(page, config) -> dict | list | None` is defined in Task 2 and consumed in Task 3 with the None-fallback contract. `_uses_prompt_schema`/`_model_name`/`_build_client` defined in Task 1 and used in `complete_json` (Task 1). `ExtractionField.description`/`example` and `ExtractionConfig.engine` defined in Task 4 Step 1 and consumed by the editor (Task 4 Steps 2-4) and backend prompt builders (Task 2, via the JSONB dict). Field-key names (`hotel_name`, `location`, `price`) are consistent across Tasks 2, 3, and 5.

---

## Addendum: per-field routing (Tasks 6–7)

The final whole-branch review found that Tasks 1–4 made `engine:"llm"` an **all-or-nothing config switch**: when the LLM path succeeds it fully replaces the selector path, so `attr:href`/`attr:src` (link/image) fields — which the LLM only sees as `innerText` — return null/garbage. This contradicts the plan's own §3 / Scope intent ("selectors own links/images; LLM owns text/number"). It also let the editor silently backfill `engine:"llm"` onto edited legacy configs. Tasks 6–7 implement the per-field merge and give the engine an explicit, non-silent UI control.

### Task 6: Per-field routing — LLM owns text/number, selectors own attr/html

**Files:**
- Modify: `backend/app/recorder/extraction.py:31-40` (guard empty selectors)
- Modify: `backend/app/recorder/llm_extract.py` (`semantic_extract` filters to LLM-eligible fields; add `_is_llm_field`)
- Modify: `backend/app/recorder/replay.py:163-172` (merge selector + LLM results) and add `_merge_extraction`
- Test: `backend/tests/test_replay.py`, `backend/tests/test_extraction.py`

**Interfaces:**
- Consumes: existing `run_extraction`, `semantic_extract`, `llm_fill_missing`.
- Produces: `app.recorder.replay._merge_extraction(selector_data, llm_data) -> dict | list`; `semantic_extract` now returns only LLM-eligible field keys (or `None` when a config has no LLM-eligible field).

- [ ] **Step 1: Write the failing merge test**

Append to `backend/tests/test_replay.py` (uses the top-level `quote`, `uuid`, `llm_extract` imports already present after Task 3's fix):

```python
async def test_llm_engine_merges_llm_text_with_selector_attr(monkeypatch):
    # engine=llm with a mixed config: the text field comes from the LLM, the
    # attr:href field comes from the selector path. Proves per-field routing.
    monkeypatch.setattr(llm_extract, "_llm_configured", lambda: True)

    async def fake_complete_json(system, user, schema, max_tokens=2000):
        # The LLM is asked ONLY for text-eligible fields; it never returns "link".
        return {"title": "Physics 101"}

    monkeypatch.setattr(llm_extract, "complete_json", fake_complete_json)
    html = "<a class='lnk' href='https://example.com/x'>Physics 101</a>"
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"data:text/html,{quote(html)}"},
            {"i": 1, "type": "extract", "ref": "main"},
        ],
        "extraction": {
            "main": {
                "mode": "single",
                "engine": "llm",
                "fields": [
                    {"name": "title", "description": "the title text"},
                    {"name": "link", "selector": ".lnk", "take": "attr:href"},
                ],
            }
        },
    }
    result = await replay_workflow(snapshot, {}, None, uuid.uuid4())
    assert result["data"]["title"] == "Physics 101"           # from the LLM (fake returned it)
    assert result["data"]["link"] == "https://example.com/x"  # from the selector (fake did NOT return it)
```

- [ ] **Step 2: Write the failing empty-selector guard test**

Append to `backend/tests/test_extraction.py` (a test that a blank selector yields null instead of throwing `querySelector('')`). Reuse the existing `fixture_page` fixture used by every other test in this file:

```python
async def test_extract_empty_selector_yields_null(fixture_page):
    # A field with no selector (normal in LLM-mode configs) must not crash the
    # selector path with a querySelector('') SyntaxError — it yields null.
    config = {"mode": "single", "fields": [{"name": "blank", "selector": "", "take": "text"}]}
    result = await run_extraction(fixture_page, config)
    assert result == {"blank": None}
```

- [ ] **Step 3: Run both tests to verify they fail**

Run: `cd backend; uv run pytest tests/test_replay.py::test_llm_engine_merges_llm_text_with_selector_attr tests/test_extraction.py::test_extract_empty_selector_yields_null -v`
Expected: FAIL — merge test returns `link: null` (LLM result wholly replaced selectors) or errors on the blank `title` selector; the guard test raises a Playwright `SyntaxError` from `querySelector('')`.

- [ ] **Step 4: Guard empty selectors in the extraction JS**

In `backend/app/recorder/extraction.py`, in the `extractFields` function inside `EXTRACTION_JS`, add an empty-selector guard as the first line of the loop body:

```javascript
  function extractFields(scope, fields) {
    const obj = {};
    for (const f of fields) {
      if (!f.selector) { obj[f.name] = null; continue; }
      const el = scope.querySelector(f.selector);
      let value = el ? takeValue(el, f.take) : null;
      value = applyTransform(value, f.transform);
      obj[f.name] = value;
    }
    return obj;
  }
```

- [ ] **Step 5: Filter `semantic_extract` to LLM-eligible fields**

In `backend/app/recorder/llm_extract.py`, add a helper near the other module helpers:

```python
def _is_llm_field(field: dict) -> bool:
    # The LLM reads the page's visible text, so it can only produce text/number
    # fields. attr:/html fields stay on the selector path.
    return (field.get("take") or "text") == "text"
```

Then in `semantic_extract`, replace the `fields = config.get("fields") or []` / `if not fields: return None` lines with a filter to LLM-eligible fields:

```python
    fields = [f for f in (config.get("fields") or []) if _is_llm_field(f)]
    if not fields:
        return None
```

(The rest of `semantic_extract`, `_semantic_single`, `_semantic_list` are unchanged — they now operate on the filtered list, so the LLM prompt/schema/result cover only text-eligible fields.)

- [ ] **Step 6: Merge selector + LLM results in replay**

In `backend/app/recorder/replay.py`, add a module-level helper (near `_resolve_value`):

```python
def _merge_extraction(selector_data: Any, llm_data: Any) -> Any:
    # LLM owns text/number fields (overlaid on top); selectors own attr:/html
    # fields (kept from selector_data). Shapes match: both a single dict, or
    # both a list of dicts over the same root.
    if isinstance(llm_data, dict) and isinstance(selector_data, dict):
        return {**selector_data, **llm_data}
    if isinstance(llm_data, list) and isinstance(selector_data, list):
        n = max(len(selector_data), len(llm_data))
        return [
            {**(selector_data[i] if i < len(selector_data) else {}),
             **(llm_data[i] if i < len(llm_data) else {})}
            for i in range(n)
        ]
    return llm_data  # shape mismatch (shouldn't happen) — prefer the LLM result
```

Then replace the `extract` step block:

```python
                elif stype == "extract":
                    config = extraction.get(step.get("ref", "main"))
                    if config:
                        await _wait_for_extraction_ready(page, config)
                        if config.get("engine") == "llm":
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

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_replay.py tests/test_extraction.py tests/test_llm_semantic.py -v`
Expected: PASS — the new merge and guard tests pass, and the existing replay/extraction/semantic tests still pass (existing LLM-mode tests use only text fields, so the merge overlays them onto null-selector results with the same final values).

- [ ] **Step 8: Lint and commit**

```bash
cd backend; uv run ruff check app
git add backend/app/recorder/extraction.py backend/app/recorder/llm_extract.py backend/app/recorder/replay.py backend/tests/test_replay.py backend/tests/test_extraction.py
git commit -m "fix(extract): per-field routing — LLM owns text, selectors own attr/html"
```

### Task 7: Explicit engine control in the editor (no silent backfill)

**Files:**
- Modify: `frontend/src/pages/WorkflowEditor.tsx` (stop coercing loaded `engine`)
- Modify: `frontend/src/components/ExtractionEditor.tsx` (add an engine radio)

**Interfaces:**
- Consumes: `ExtractionConfig.engine` (Task 4).
- Produces: an explicit engine toggle; loaded legacy configs keep `engine: undefined` (→ selector path at replay) until the user opts in.

- [ ] **Step 1: Stop auto-upgrading loaded configs**

In `frontend/src/pages/WorkflowEditor.tsx`, in the extraction load block, change the engine coercion so a loaded config keeps its stored engine (absent stays absent — replay treats absent as selector):

```typescript
            engine: loaded.engine,
```

(Do NOT use `?? 'llm'` here. New/empty configs still default to `'llm'` via `EMPTY_EXTRACTION`; this line governs only already-saved configs, which must not silently flip to LLM on load.)

- [ ] **Step 2: Add an engine radio to the editor**

In `frontend/src/components/ExtractionEditor.tsx`, add an engine control in the top control row (next to the Single/List radios), using the existing `Radio` component. The checked state treats absent engine as `'selector'` (how it actually runs), and `EMPTY_EXTRACTION`'s `'llm'` default makes new configs show Smart:

```tsx
        <span className="ml-auto flex items-center gap-3">
          <span className="text-[11px] font-bold uppercase tracking-wide text-ink/60">Engine</span>
          <label className="flex items-center gap-1.5">
            <Radio
              disabled={disabled}
              checked={(extraction.engine ?? 'selector') === 'llm'}
              onChange={() => onChange({ ...extraction, engine: 'llm' })}
            />
            Smart (LLM)
          </label>
          <label className="flex items-center gap-1.5">
            <Radio
              disabled={disabled}
              checked={(extraction.engine ?? 'selector') === 'selector'}
              onChange={() => onChange({ ...extraction, engine: 'selector' })}
            />
            Selectors
          </label>
        </span>
```

Place this inside the existing `<div className="flex items-center gap-4 text-sm">` row that holds the Single/List radios (the `ml-auto` pushes it to the right).

- [ ] **Step 3: Typecheck / build / lint**

Run: `cd frontend; npm run build` → tsc + vite clean.
Run: `cd frontend; npm run lint` → oxlint clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/WorkflowEditor.tsx frontend/src/components/ExtractionEditor.tsx
git commit -m "feat(ui): explicit engine toggle; stop silent llm backfill on loaded configs"
```

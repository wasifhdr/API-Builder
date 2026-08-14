# Agent Authoring Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the autonomous agent from publishing a workflow that returns nulls, and make the start-URL confirmation card correctable instead of a dead end.

**Architecture:** Six independent corrections to the existing agent pipeline (`plan → drive → distill → extract → verify`). Verification gains two teeth — value presence instead of key presence, and an empirical check that no step was anchored to drive-time page content. The plan declares output cardinality so the drive brief can state where the data lives. The confirmation card gains an editable URL and a real cancelled terminal state. No new subsystems; production replay semantics are unchanged.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async + asyncpg), Playwright (async), pytest + pytest-asyncio, React 19 + Vite + Tailwind v4.

**Source spec:** [docs/superpowers/specs/2026-08-15-agent-authoring-correctness-design.md](../specs/2026-08-15-agent-authoring-correctness-design.md)

## Global Constraints

- Playwright runs **only** in the worker process — never in FastAPI, never in Docker. Tests that launch a browser run in-process under pytest, which is fine; do not add Playwright calls to any `app/api/` module.
- All backend commands run from the `backend/` directory via `uv run`.
- Lint with `uv run ruff check app` before every commit. Frontend: `npm run lint` (oxlint) and `npm run build` (tsc).
- SQLAlchemy enums use `enum_column` (`native_enum=False`, `VARCHAR(32)`, no CHECK constraint). **Adding an enum member needs no Alembic migration.**
- JSONB columns are replaced, never mutated in place.
- Do not change production replay behaviour. `replay_workflow`'s forgiving selector fallback stays exactly as it is; only new *reporting* is added, opt-in via a keyword argument that defaults to off.
- Do not modify the manual recorder's UX, publishing, metering, or OpenAPI generation.
- There is **no frontend test framework** in this repo. Frontend tasks are verified with `npm run build`, `npm run lint`, and a browser check.
- Commit after every task. Never `git add -A` — this repo has unrelated work in progress; add only the exact files each task touches.

---

## File Structure

**Modified — backend:**

| File | Responsibility after this change |
|---|---|
| `backend/app/agent/verify.py` | Owns *both* correctness rules: value presence (`missing_field_names`) and selector stability (`content_anchored_fallbacks`). Both are pure functions, exported for reuse by the runner. |
| `backend/app/agent/runner.py` | Gains a pre-verify sample gate, a `stable_selectors` repair hint, the `UrlDecision` confirmation contract, and stops mutating `plan["summary"]` to carry repair hints. |
| `backend/app/recorder/replay.py` | `_locate` reports which candidate matched; `replay_workflow` optionally reports fallbacks. Behaviour otherwise unchanged. |
| `backend/app/agent/planner.py` | Declares `result_shape`. |
| `backend/app/agent/driver.py` | States the shape and carries the repair hint in its own labelled section. |
| `backend/app/models/agent_run.py` | `CANCELLED` status. |
| `backend/app/services/agent_runs.py` | Shared idempotent refund; `cancel_run`. |
| `backend/app/schemas/agent.py` | `ConfirmUrlIn.url`. |
| `backend/app/api/agent.py` | Validates a user-supplied URL, 400 on bad input. |

**Modified — frontend:** `frontend/src/lib/agentTypes.ts`, `frontend/src/hooks/useAgentRun.ts`, `frontend/src/pages/AgentBuilder.tsx`.

**Test files:** `backend/tests/test_agent_verify.py`, `test_agent_runner.py`, `test_replay.py`, `test_agent_planner.py`, `test_agent_driver.py`, `test_agent_run_billing.py`, `test_agent_api.py`.

**No new files.** Every change lands in a module that already owns that responsibility.

---

### Task 1: Verify counts values, not keys

The defect that let a broken API publish. `fields_present` tests `n in row`, and every row dict carries every declared key, so a row of pure nulls passes.

**Files:**
- Modify: `backend/app/agent/verify.py:84-90`
- Test: `backend/tests/test_agent_verify.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `missing_field_names(data: object, fields: list[dict] | None) -> list[str]` in `app.agent.verify` — returns declared field names with no usable value anywhere in `data`. Tasks 2 and 4 both import it.

- [ ] **Step 1: Write the failing tests**

Add to the top of `backend/tests/test_agent_verify.py`, just below the existing imports:

```python
from unittest.mock import AsyncMock, patch

from app.agent.verify import missing_field_names, verify_workflow

FIELDS = [{"name": "title"}, {"name": "price"}]
```

(Replace the existing `from app.agent.verify import verify_workflow` line with the import above.)

Then append these tests to the end of the file:

```python
def test_all_null_rows_count_as_missing_every_field():
    """The Walton defect: every row dict carries every declared key, so key
    presence is not evidence that anything was extracted."""
    rows = [{"title": None, "price": None}, {"title": None, "price": None}]
    assert missing_field_names(rows, FIELDS) == ["title", "price"]


def test_a_key_present_but_null_is_not_a_present_field():
    rows = [{"title": "Smart Television", "price": None}]
    assert missing_field_names(rows, FIELDS) == ["price"]


def test_a_field_populated_in_only_one_row_is_present():
    """Sparse data is normal — a discount price on 1 of 3 rows is not a defect,
    so no fill-rate threshold is applied."""
    rows = [
        {"title": "a", "price": None},
        {"title": "b", "price": "99"},
        {"title": "c", "price": None},
    ]
    assert missing_field_names(rows, FIELDS) == []


def test_a_blank_string_is_missing():
    assert missing_field_names([{"title": "   ", "price": "1"}], FIELDS) == ["title"]


def test_a_single_dict_is_treated_as_one_row():
    assert missing_field_names({"title": "x", "price": None}, FIELDS) == ["price"]


def test_no_rows_means_every_field_missing():
    assert missing_field_names([], FIELDS) == ["title", "price"]
    assert missing_field_names(None, FIELDS) == ["title", "price"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_agent_verify.py -k missing -v
```

Expected: collection error — `ImportError: cannot import name 'missing_field_names'`.

- [ ] **Step 3: Implement `missing_field_names`**

In `backend/app/agent/verify.py`, add after the existing `_canonical` function:

```python
def _is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def missing_field_names(data: object, fields: list[dict] | None) -> list[str]:
    """Declared fields that carry no usable value anywhere in `data`.

    Key presence is not evidence. Every row dict the extractor builds carries
    every declared key, so `name in row` is satisfied by a row of pure nulls —
    which is how a workflow returning nothing for every field reached READY and
    got published. A blank string counts as missing for the same reason: an
    empty text node is not extracted data.

    A field populated in only ONE row is present. Sparse fields (a discount
    price on 3 of 20 rows) are normal data, so no fill-rate threshold applies.
    """
    declared = [f["name"] for f in fields or []]
    rows = _rows(data)
    if not rows:
        return declared
    return [n for n in declared if all(_is_empty(row.get(n)) for row in rows)]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_agent_verify.py -k missing -v
```

Expected: 6 passed.

- [ ] **Step 5: Wire it into `fields_present`**

In `backend/app/agent/verify.py`, replace these three lines inside `verify_workflow`:

```python
    declared = [f["name"] for f in plan.get("fields") or []]
    missing = [n for n in declared if not any(n in row for row in rows)] if rows else declared
    result.checks.append(CheckResult(
```

with:

```python
    missing = missing_field_names(data, plan.get("fields"))
    result.checks.append(CheckResult(
```

The `rows` local is still used by the `has_rows` check above, so leave it in place.

- [ ] **Step 6: Add the integration test proving a null-yielding extraction now fails**

Append to `backend/tests/test_agent_verify.py`:

```python
@pytest.mark.asyncio
async def test_all_null_rows_fail_verification(fixture_site_url):
    """End to end: rows exist, keys exist, every value is null. Before this
    change the run passed every check and published."""
    snapshot = {
        "steps": [
            {"type": "goto",
             "url": f"{fixture_site_url}/search.html?q=refrigerator",
             "url_template": f"{fixture_site_url}/search.html?q={{query}}"},
            {"type": "extract", "ref": "main"},
        ],
        "extraction": {"main": {
            "mode": "list",
            "root": "li.product",
            "fields": [
                {"name": "title", "selectors": [".no-such-title"], "take": "text"},
                {"name": "price", "selectors": [".no-such-price"], "take": "text"},
            ],
        }},
    }
    # The extraction path calls the LLM to fill nulls; pin it to a no-op so the
    # test asserts the CHECK, not the model's behaviour.
    async def _no_fill(page, config, data):
        return data

    with patch("app.recorder.replay.llm_fill_missing", AsyncMock(side_effect=_no_fill)):
        result = await verify_workflow(snapshot, PLAN, DRIVE_DATA)

    assert not result.passed
    check = next(c for c in result.checks if c.name == "fields_present")
    assert not check.passed
    assert "title" in check.detail and "price" in check.detail
```

- [ ] **Step 7: Run the whole verify suite**

```bash
cd backend && uv run pytest tests/test_agent_verify.py -v
```

Expected: all pass, including the pre-existing `test_a_correct_workflow_passes_every_check`.

- [ ] **Step 8: Lint and commit**

```bash
cd backend && uv run ruff check app
```

```bash
git add backend/app/agent/verify.py backend/tests/test_agent_verify.py
git commit -m "fix(agent): verify counts field values, not field keys"
```

---

### Task 2: Reject a dead extraction before spending a verify replay

`RecordingSession._capture_final_extraction` already stores what the marked elements produced. If that is empty there is no point replaying.

**Files:**
- Modify: `backend/app/agent/runner.py`
- Test: `backend/tests/test_agent_runner.py`

**Interfaces:**
- Consumes: `missing_field_names` from Task 1.
- Produces: `sample_failure_reason(sample: object, fields: list[dict] | None) -> str | None` in `app.agent.runner` — `None` when the drive-time sample is usable, otherwise a human-readable reason.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_agent_runner.py`:

```python
from app.agent.runner import sample_failure_reason

SAMPLE_FIELDS = [{"name": "title"}, {"name": "price"}]


def test_sample_failure_reason_is_none_when_every_field_has_a_value():
    sample = [{"title": "Blue Refrigerator", "price": "45000"}]
    assert sample_failure_reason(sample, SAMPLE_FIELDS) is None


def test_sample_failure_reason_names_the_empty_fields():
    sample = [{"title": "Blue Refrigerator", "price": None}]
    assert sample_failure_reason(sample, SAMPLE_FIELDS) == (
        "the marked elements produced no value for: price"
    )


def test_sample_failure_reason_rejects_a_sample_that_never_ran():
    assert sample_failure_reason(None, SAMPLE_FIELDS) == (
        "the marked elements produced no value for: title, price"
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_agent_runner.py -k sample_failure -v
```

Expected: collection error — `ImportError: cannot import name 'sample_failure_reason'`.

- [ ] **Step 3: Implement it**

In `backend/app/agent/runner.py`, change the verify import line:

```python
from app.agent.verify import VerifyResult, verify_workflow
```

to:

```python
from app.agent.verify import VerifyResult, missing_field_names, verify_workflow
```

Then add after the `repair_hint` function:

```python
def sample_failure_reason(sample: object, fields: list[dict] | None) -> str | None:
    """Rejects an attempt whose marked elements produced nothing, before a
    verify replay is spent on it.

    Applies exactly the rule verify applies to the replayed output: a sample of
    pure nulls is a broken extraction, not an empty result set. Catching it here
    saves a full headless replay on an attempt that cannot pass.
    """
    missing = missing_field_names(sample, fields)
    if not missing:
        return None
    return "the marked elements produced no value for: " + ", ".join(missing)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_agent_runner.py -k sample_failure -v
```

Expected: 3 passed.

- [ ] **Step 5: Wire it into the attempt loop**

In `backend/app/agent/runner.py`, inside `run_agent`, insert this block immediately **after** the `if outcome["gave_up_reason"]:` block ends (i.e. after its `continue`) and **before** the `try:` that calls `bind_parameters`:

```python
        bad_sample = sample_failure_reason(session.final_sample, plan.get("fields"))
        if bad_sample:
            log.info("agent %s attempt %s bad sample: %s", agent_run_id, attempt, bad_sample)
            if attempt >= MAX_ATTEMPTS:
                await _finish(
                    agent_run_id, succeeded=False, reason=bad_sample, tokens=total_tokens,
                )
                return
            hint = build_repair_context(
                session.steps, f"{bad_sample}\n{_STRATEGY_HINTS['fields_present']}"
            )
            continue
```

- [ ] **Step 6: Run the runner and integration suites**

```bash
cd backend && uv run pytest tests/test_agent_runner.py tests/test_agent_runner_integration.py -v
```

Expected: all pass. The integration test's scripted agent marks real elements on the fixture site, so its sample is populated and the new gate is a no-op for it.

- [ ] **Step 7: Lint and commit**

```bash
cd backend && uv run ruff check app
```

```bash
git add backend/app/agent/runner.py backend/tests/test_agent_runner.py
git commit -m "fix(agent): fail an attempt whose marked elements extracted nothing"
```

---

### Task 3: Replay reports which selector candidate matched

Groundwork for Invariant A. Reporting only — the fallback behaviour itself does not change.

**Files:**
- Modify: `backend/app/recorder/replay.py:319-329` (`_locate`), and `replay_workflow`
- Test: `backend/tests/test_replay.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `_locate(page, selectors) -> tuple[Locator, int]` (the int is the index in `selectors` that matched); `replay_workflow(..., record_fallbacks: bool = False)`; the returned dict gains `"selector_fallbacks": list[dict]` with entries `{"step_index": int, "skipped": list[str], "used": str}`. Task 4 consumes this key.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_replay.py` (it already imports `uuid`, `pytest`, and `replay_workflow`; if any is missing, add it):

```python
@pytest.mark.asyncio
async def test_replay_reports_a_selector_fallback(fixture_site_url):
    """The Walton failure mode: a text-anchored candidate captured during the
    drive misses under a different value, and a positional candidate silently
    matches a DIFFERENT element."""
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"{fixture_site_url}/search.html?q=television"},
            {"i": 1, "type": "click", "selectors": [
                'a:has-text("Blue Refrigerator")',
                "#results > li:nth-of-type(1)",
            ]},
        ],
        "extraction": {},
    }
    result = await replay_workflow(
        snapshot, {}, None, uuid.uuid4(), headless=True, record_fallbacks=True,
    )
    assert result["selector_fallbacks"] == [{
        "step_index": 1,
        "skipped": ['a:has-text("Blue Refrigerator")'],
        "used": "#results > li:nth-of-type(1)",
    }]


@pytest.mark.asyncio
async def test_replay_reports_nothing_when_the_first_candidate_matches(fixture_site_url):
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto", "url": f"{fixture_site_url}/search.html?q=television"},
            {"i": 1, "type": "click", "selectors": ["#results > li:nth-of-type(1)"]},
        ],
        "extraction": {},
    }
    result = await replay_workflow(
        snapshot, {}, None, uuid.uuid4(), headless=True, record_fallbacks=True,
    )
    assert result["selector_fallbacks"] == []
```

Note for the implementer: the first test takes roughly 15 s — a 10 s selector budget on the missing candidate plus the 5 s `POST_NAV_WAIT_MS` dwell. That is expected, not a hang.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_replay.py -k selector_fallback -v
```

Expected: FAIL with `TypeError: replay_workflow() got an unexpected keyword argument 'record_fallbacks'`.

- [ ] **Step 3: Make `_locate` report the matched index**

In `backend/app/recorder/replay.py`, replace the whole `_locate` function:

```python
async def _locate(page: Page, selectors: list[str]):
    last_exc: Exception | None = None
    for selector, timeout_ms in zip(selectors, SELECTOR_ATTEMPT_TIMEOUTS_MS):
        try:
            locator = await _first_visible(page, selector, timeout_ms)
            if locator is not None:
                return locator
        except Exception as exc:
            last_exc = exc
            continue
    raise ReplayError(f"none of the candidate selectors matched: {selectors}") from last_exc
```

with:

```python
async def _locate(page: Page, selectors: list[str]) -> tuple[Any, int]:
    """Returns (locator, index) — index is the position in `selectors` of the
    candidate that actually matched.

    Falling through to a lower-ranked candidate is correct at production replay
    time (it absorbs ordinary selector drift), but it is also how a step
    anchored to drive-time content silently binds to the WRONG element. The
    caller decides what to do with that fact; this function only reports it.
    """
    last_exc: Exception | None = None
    for index, (selector, timeout_ms) in enumerate(zip(selectors, SELECTOR_ATTEMPT_TIMEOUTS_MS)):
        try:
            locator = await _first_visible(page, selector, timeout_ms)
            if locator is not None:
                return locator, index
        except Exception as exc:
            last_exc = exc
            continue
    raise ReplayError(f"none of the candidate selectors matched: {selectors}") from last_exc
```

`Any` is already imported at the top of the file (`from typing import Any`).

- [ ] **Step 4: Add the parameter and the reporting closure**

In `replay_workflow`, change the signature line:

```python
    workflow_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any]:
```

to:

```python
    workflow_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    record_fallbacks: bool = False,
) -> dict[str, Any]:
```

Immediately below `data: Any = None` near the top of the function, add:

```python
    # Populated only when the caller asks (verify does; production replay does
    # not). Reporting only — the fallback behaviour above is unchanged.
    fallbacks: list[dict] = []
```

Inside the `async with lock, async_playwright() as pw:` block, directly after the `_settle_after_nav` function definition, add:

```python
        async def _locate_step(step: dict, index: int):
            selectors = step.get("selectors", [])
            locator, matched = await _locate(page, selectors)
            if record_fallbacks and matched > 0:
                fallbacks.append({
                    "step_index": step.get("i", index),
                    "skipped": selectors[:matched],
                    "used": selectors[matched],
                })
            return locator
```

- [ ] **Step 5: Route the four call sites through it**

Change the loop header:

```python
            for step in steps:
```

to:

```python
            for index, step in enumerate(steps):
```

Then replace each of the four `_locate` call sites in the step loop:

```python
                elif stype == "click":
                    locator = await _locate(page, step.get("selectors", []))
                    await locator.click()
                elif stype == "fill":
                    locator = await _locate(page, step.get("selectors", []))
                    await locator.fill(_resolve_value(step.get("value"), params))
                elif stype == "press":
                    locator = await _locate(page, step.get("selectors", []))
                    await locator.press(step["key"])
                elif stype == "select_option":
                    locator = await _locate(page, step.get("selectors", []))
                    await locator.select_option(value=_resolve_value(step.get("value"), params))
```

with:

```python
                elif stype == "click":
                    locator = await _locate_step(step, index)
                    await locator.click()
                elif stype == "fill":
                    locator = await _locate_step(step, index)
                    await locator.fill(_resolve_value(step.get("value"), params))
                elif stype == "press":
                    locator = await _locate_step(step, index)
                    await locator.press(step["key"])
                elif stype == "select_option":
                    locator = await _locate_step(step, index)
                    await locator.select_option(value=_resolve_value(step.get("value"), params))
```

Finally change the return statement at the end of the function:

```python
    return {"data": data}
```

to:

```python
    return {"data": data, "selector_fallbacks": fallbacks}
```

- [ ] **Step 6: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_replay.py -v
```

Expected: all pass, including every pre-existing replay test — they ignore the new dict key and never pass `record_fallbacks`.

- [ ] **Step 7: Confirm nothing else destructured the return value**

```bash
cd backend && uv run pytest tests/ -k "replay or execution or lifecycle" -v
```

Expected: all pass. Callers read `result["data"]`, so an extra key is inert.

- [ ] **Step 8: Lint and commit**

```bash
cd backend && uv run ruff check app
```

```bash
git add backend/app/recorder/replay.py backend/tests/test_replay.py
git commit -m "feat(replay): optionally report which selector candidate matched"
```

---

### Task 4: Verify fails a step anchored to drive-time content

Invariant A. This is the check that would have caught `a:has-text("WNR-6D6-GDFS-DI")`.

**Files:**
- Modify: `backend/app/agent/verify.py`, `backend/app/agent/runner.py:24-42` (`_STRATEGY_HINTS`)
- Test: `backend/tests/test_agent_verify.py`

**Interfaces:**
- Consumes: `replay_workflow(..., record_fallbacks=True)` and the `"selector_fallbacks"` key from Task 3.
- Produces: `content_anchored_fallbacks(fallbacks: list[dict]) -> list[dict]` in `app.agent.verify`; a new `CheckResult` named `stable_selectors`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_agent_verify.py`:

```python
def test_a_skipped_text_selector_is_content_anchored():
    fallbacks = [{"step_index": 3,
                  "skipped": ['a:has-text("WNR-6D6-GDFS-DI")'],
                  "used": "#products > div:nth-of-type(1) > a"}]
    assert content_anchored_fallbacks(fallbacks) == fallbacks


def test_a_skipped_href_selector_is_content_anchored():
    fallbacks = [{"step_index": 3,
                  "skipped": ['a[href="/product/4521"]'],
                  "used": "#products > div:nth-of-type(1) > a"}]
    assert content_anchored_fallbacks(fallbacks) == fallbacks


def test_a_skipped_class_selector_is_a_flake_not_a_defect():
    """Narrowing to content-anchored candidates is a false-positive filter: an
    id/class candidate missing for timing reasons must not cost an attempt."""
    fallbacks = [{"step_index": 2,
                  "skipped": ["button.search-btn"],
                  "used": "#search-form > button"}]
    assert content_anchored_fallbacks(fallbacks) == []


def test_no_fallbacks_is_no_finding():
    assert content_anchored_fallbacks([]) == []


def test_a_fallback_with_no_skipped_list_is_ignored():
    assert content_anchored_fallbacks([{"step_index": 1, "used": "#x"}]) == []
```

And update the import line added in Task 1 to:

```python
from app.agent.verify import (
    content_anchored_fallbacks,
    missing_field_names,
    verify_workflow,
)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_agent_verify.py -k content_anchored -v
```

Expected: collection error — `ImportError: cannot import name 'content_anchored_fallbacks'`.

- [ ] **Step 3: Implement the filter**

In `backend/app/agent/verify.py`, add `import re` to the imports at the top, then add after `missing_field_names`:

```python
_CONTENT_ANCHORED_RE = re.compile(r":has-text\(|\[href=")


def content_anchored_fallbacks(fallbacks: list[dict]) -> list[dict]:
    """Fallbacks where the SKIPPED candidate was anchored to page content —
    visible text or an href.

    Those literals were captured from the drive value's results, so they can
    never match another value. Replay then falls through to a positional
    candidate that matches a DIFFERENT element and proceeds without error,
    which is exactly how a workflow that drilled into one search result got
    published returning nulls.

    Fallbacks on id/class candidates are deliberately left alone: those miss
    for ordinary timing reasons, and failing a run over one would cost the user
    an attempt for nothing.
    """
    return [
        f for f in fallbacks
        if any(_CONTENT_ANCHORED_RE.search(s) for s in f.get("skipped") or [])
    ]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_agent_verify.py -k content_anchored -v
```

Expected: 5 passed.

- [ ] **Step 5: Add the `stable_selectors` check to `verify_workflow`**

In `backend/app/agent/verify.py`, change the replay call inside `verify_workflow`:

```python
        replay = await replay_workflow(
            snapshot, params, None, uuid.uuid4(), headless=True, workflow_id=workflow_id
        )
```

to:

```python
        replay = await replay_workflow(
            snapshot, params, None, uuid.uuid4(), headless=True,
            workflow_id=workflow_id, record_fallbacks=True,
        )
```

Then, immediately before the final `return result`, add:

```python
    unstable = content_anchored_fallbacks(replay.get("selector_fallbacks") or [])
    result.checks.append(CheckResult(
        "stable_selectors", not unstable,
        "; ".join(
            f"step {f['step_index']} is anchored to drive-time page content "
            f"({f['skipped'][0]}) and matched a different element via {f['used']}"
            for f in unstable
        ) if unstable else "no step depended on drive-time page content",
    ))
```

- [ ] **Step 6: Add the integration test**

Append to `backend/tests/test_agent_verify.py`:

```python
@pytest.mark.asyncio
async def test_a_step_anchored_to_drive_content_fails_verification(fixture_site_url):
    """Replays with `television` a workflow recorded against `refrigerator`.
    The text-anchored click cannot match, a positional candidate does, and the
    run must be rejected rather than quietly proceeding on the wrong element."""
    snapshot = {
        "steps": [
            {"i": 0, "type": "goto",
             "url": f"{fixture_site_url}/search.html?q=refrigerator",
             "url_template": f"{fixture_site_url}/search.html?q={{query}}"},
            {"i": 1, "type": "click", "selectors": [
                'a:has-text("Blue Refrigerator")',
                "#results > li:nth-of-type(1)",
            ]},
            {"i": 2, "type": "extract", "ref": "main"},
        ],
        "extraction": EXTRACTION,
    }
    result = await verify_workflow(snapshot, PLAN, DRIVE_DATA)

    assert not result.passed
    check = next(c for c in result.checks if c.name == "stable_selectors")
    assert not check.passed
    assert "step 1" in check.detail
```

- [ ] **Step 7: Add the repair hint**

In `backend/app/agent/runner.py`, add this entry to the `_STRATEGY_HINTS` dict:

```python
    "stable_selectors": (
        "A step was anchored to text or a link captured from the first "
        "parameter value's results, so it can never match a different value. "
        "Do not click an individual result to reach the data — extract from "
        "the page the parameterized interaction itself produces."
    ),
```

- [ ] **Step 8: Run the full verify and runner suites**

```bash
cd backend && uv run pytest tests/test_agent_verify.py tests/test_agent_runner.py -v
```

Expected: all pass. `test_a_correct_workflow_passes_every_check` must still pass — its workflow has no click step, so no fallback is possible.

- [ ] **Step 9: Lint and commit**

```bash
cd backend && uv run ruff check app
```

```bash
git add backend/app/agent/verify.py backend/app/agent/runner.py backend/tests/test_agent_verify.py
git commit -m "fix(agent): reject a workflow step anchored to drive-time content"
```

---

### Task 5: The plan declares output cardinality

**Files:**
- Modify: `backend/app/agent/planner.py`
- Test: `backend/tests/test_agent_planner.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `build_plan()`'s returned dict gains `"result_shape": "list" | "detail"`. Task 6 reads it.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_agent_planner.py`:

```python
@pytest.mark.asyncio
async def test_build_plan_keeps_a_declared_result_shape():
    with patch("app.agent.planner.complete_json",
               AsyncMock(return_value=_plan(result_shape="detail"))):
        plan = await build_plan("get the specs of the Walton WNR-6D6")
    assert plan["result_shape"] == "detail"


@pytest.mark.asyncio
async def test_build_plan_falls_back_to_list_when_the_shape_is_missing():
    """An unparseable cardinality is not worth failing a run over — the
    value-presence and selector-stability checks catch a wrong fallback."""
    with patch("app.agent.planner.complete_json", AsyncMock(return_value=_plan())):
        plan = await build_plan("make me an API to search walton")
    assert plan["result_shape"] == "list"


@pytest.mark.asyncio
async def test_build_plan_falls_back_to_list_on_a_garbage_shape():
    with patch("app.agent.planner.complete_json",
               AsyncMock(return_value=_plan(result_shape="table"))):
        plan = await build_plan("p")
    assert plan["result_shape"] == "list"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_agent_planner.py -k result_shape -v
```

Expected: FAIL with `KeyError: 'result_shape'`.

- [ ] **Step 3: Implement it**

In `backend/app/agent/planner.py`, add the module constant below `_NAME_RE`:

```python
VALID_RESULT_SHAPES = {"list", "detail"}
```

In `PLAN_SCHEMA`, add to `properties`:

```python
        "result_shape": {"type": "string", "enum": ["list", "detail"]},
```

and change the schema's `"required"` list to:

```python
    "required": ["url", "parameters", "fields", "result_shape"],
```

Append to `PLAN_SYSTEM`:

```python
    "\n- result_shape is 'list' when the request describes a search, a browse, "
    "or a listing (search for products, list flights, top headlines), and "
    "'detail' when it names one record's attributes (the specs of product X, "
    "today's USD-BDT rate)."
```

In `build_plan`, add just before the final `return`:

```python
    # Falls back rather than raising: a model that omits the cardinality has
    # still produced a usable plan, and shipping the wrong shape is caught
    # downstream by fields_present and stable_selectors.
    shape = raw.get("result_shape")
    if shape not in VALID_RESULT_SHAPES:
        shape = "list"
```

and add `"result_shape": shape,` to the returned dict.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_agent_planner.py -v
```

Expected: all pass, including the pre-existing plan tests.

- [ ] **Step 5: Lint and commit**

```bash
cd backend && uv run ruff check app
```

```bash
git add backend/app/agent/planner.py backend/tests/test_agent_planner.py
git commit -m "feat(agent): the plan declares list-vs-detail output cardinality"
```

---

### Task 6: The drive brief states the shape, and the repair hint stops posing as the goal

**Files:**
- Modify: `backend/app/agent/driver.py`, `backend/app/agent/runner.py`
- Test: `backend/tests/test_agent_driver.py`

**Interfaces:**
- Consumes: `plan["result_shape"]` from Task 5.
- Produces: `_task_brief(plan: dict, hint: str = "") -> str`; `drive(page, plan, max_turns=25, on_progress=None, hint="")`. The runner no longer mutates `plan["summary"]`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_agent_driver.py` (add `from app.agent.driver import _task_brief` to its imports):

```python
def _brief_plan(**overrides):
    plan = {
        "url": "https://example.com",
        "summary": "Search products",
        "result_shape": "list",
        "parameters": [{"name": "query", "type": "string", "drive_value": "refrigerator"}],
        "fields": [{"name": "title"}, {"name": "price"}],
    }
    plan.update(overrides)
    return plan


def test_task_brief_forbids_opening_a_result_for_a_list_shape():
    brief = _task_brief(_brief_plan())
    assert "Do NOT open an individual result" in brief


def test_task_brief_permits_reaching_one_record_for_a_detail_shape():
    brief = _task_brief(_brief_plan(result_shape="detail"))
    assert "Do NOT open an individual result" not in brief
    assert "single record" in brief


def test_task_brief_defaults_to_the_list_rule_when_the_shape_is_absent():
    plan = _brief_plan()
    del plan["result_shape"]
    assert "Do NOT open an individual result" in _task_brief(plan)


def test_a_repair_hint_is_labelled_and_not_folded_into_the_task():
    """The hint used to be appended to plan['summary'], so the model read a
    failure transcript where it expected a goal statement."""
    brief = _task_brief(_brief_plan(), hint="Previous attempt failed: nulls")
    task_line = brief.splitlines()[0]
    assert "Previous attempt failed" not in task_line
    assert "PREVIOUS ATTEMPT" in brief
    assert "Previous attempt failed: nulls" in brief


def test_no_hint_section_when_there_is_no_hint():
    assert "PREVIOUS ATTEMPT" not in _task_brief(_brief_plan())
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_agent_driver.py -k "task_brief or repair_hint or hint_section" -v
```

Expected: FAIL — `_task_brief() takes 1 positional argument but 2 were given`, and the shape assertions fail.

- [ ] **Step 3: Rewrite `_task_brief`**

In `backend/app/agent/driver.py`, replace the whole `_task_brief` function:

```python
_SHAPE_RULES = {
    "list": (
        "The data lives on the page your interaction produces. Mark a "
        "representative result row THERE. Do NOT open an individual result — "
        "an API built from one record's page returns that one record forever."
    ),
    "detail": (
        "Reach the single record the request describes, then mark its fields."
    ),
}


def _task_brief(plan: dict, hint: str = "") -> str:
    params = "\n".join(
        f"- {p['name']} ({p['type']}): use the value {p['drive_value']!r}"
        for p in plan["parameters"]
    )
    fields = ", ".join(f["name"] for f in plan["fields"])
    shape = plan.get("result_shape")
    if shape not in _SHAPE_RULES:
        shape = "list"
    sections = [
        f"Task: {plan.get('summary') or 'build the described API'}",
        f"You are already at the start URL ({plan['url']}) — the observation "
        "below shows the current page. Do not navigate there again.",
        _SHAPE_RULES[shape],
        f"Parameters to exercise:\n{params or '- (none)'}",
        f"Data fields the API must return: {fields}",
    ]
    if hint:
        # Its own labelled section: appending this to the task statement made
        # the model read a failure transcript as its goal.
        sections.append(f"PREVIOUS ATTEMPT (do not repeat this route):\n{hint}")
    return "\n".join(sections)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_agent_driver.py -v
```

Expected: all pass.

- [ ] **Step 5: Add the matching rule to `DRIVE_SYSTEM`**

In `backend/app/agent/driver.py`, insert this bullet into `DRIVE_SYSTEM` directly after the existing "Reach results by INTERACTING with the page" bullet:

```python
    "- Extract at the altitude the task states. For a list task the answer is "
    "the repeating rows on the page your interaction produced; clicking into "
    "one of them produces an API that returns that one record for every "
    "input.\n"
```

- [ ] **Step 6: Thread `hint` through `drive` instead of through the summary**

In `backend/app/agent/driver.py`, change the `drive` signature:

```python
async def drive(page: Page, plan: dict, max_turns: int = 25, on_progress=None) -> DriveResult:
```

to:

```python
async def drive(
    page: Page, plan: dict, max_turns: int = 25, on_progress=None, hint: str = "",
) -> DriveResult:
```

and change the first message construction:

```python
        user_message(f"{_task_brief(plan)}\n\n{observation.tree}", observation.screenshot_b64)
```

to:

```python
        user_message(
            f"{_task_brief(plan, hint)}\n\n{observation.tree}", observation.screenshot_b64
        )
```

- [ ] **Step 7: Stop the runner mutating the plan**

In `backend/app/agent/runner.py`, delete this line from the attempt loop:

```python
        task_plan = plan if not hint else {**plan, "summary": f"{plan.get('summary', '')}\n\n{hint}".strip()}
```

and change the driver closure:

```python
        async def agent_driver(session, _task_plan=task_plan, _outcome=outcome) -> None:
            result = await drive(session.page, _task_plan, on_progress=_on_progress)
```

to:

```python
        async def agent_driver(session, _hint=hint, _outcome=outcome) -> None:
            result = await drive(session.page, plan, on_progress=_on_progress, hint=_hint)
```

- [ ] **Step 8: Run the agent suites**

```bash
cd backend && uv run pytest tests/ -k agent -v
```

Expected: all pass, including `test_agent_runner_integration.py`.

- [ ] **Step 9: Lint and commit**

```bash
cd backend && uv run ruff check app
```

```bash
git add backend/app/agent/driver.py backend/app/agent/runner.py backend/tests/test_agent_driver.py
git commit -m "feat(agent): state output altitude in the drive brief, label repair hints"
```

---

### Task 7: A cancelled run is cancelled, not failed

**Files:**
- Modify: `backend/app/models/agent_run.py`, `backend/app/services/agent_runs.py`
- Test: `backend/tests/test_agent_run_billing.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `AgentRunStatus.CANCELLED`; `agent_runs.cancel_run(run: AgentRun, db: AsyncSession) -> None` (does NOT commit, matching `finish_run`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_agent_run_billing.py`. It already defines the `_make_pro(db, make_user, amount="100.00")` helper and imports `agent_runs`, `wallet`, `AgentRunStatus`, and `Decimal` — reuse them, do not add new helpers. Note the balance accessor is `wallet.balances(user_id, db)`, which returns a `(balance, _)` tuple.

```python
@pytest.mark.asyncio
async def test_cancel_run_refunds_and_marks_cancelled(db, make_user):
    user = await _make_pro(db, make_user)
    run = await agent_runs.create_run(user, "p", db)
    await db.commit()

    await agent_runs.cancel_run(run, db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    assert run.status == AgentRunStatus.CANCELLED
    assert balance == Decimal("100.00")


@pytest.mark.asyncio
async def test_cancel_run_refunds_only_once(db, make_user):
    user = await _make_pro(db, make_user)
    run = await agent_runs.create_run(user, "p", db)
    await db.commit()

    await agent_runs.cancel_run(run, db)
    await db.commit()
    await agent_runs.cancel_run(run, db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    assert balance == Decimal("100.00")


@pytest.mark.asyncio
async def test_a_cancelled_run_cannot_later_be_marked_failed(db, make_user):
    """Guards the race between a user cancelling and the runner's own timeout
    path finishing the same run."""
    user = await _make_pro(db, make_user)
    run = await agent_runs.create_run(user, "p", db)
    await db.commit()

    await agent_runs.cancel_run(run, db)
    await agent_runs.finish_run(run, succeeded=False, reason="timeout", db=db)
    await db.commit()

    balance, _ = await wallet.balances(user.id, db)
    assert run.status == AgentRunStatus.CANCELLED
    assert balance == Decimal("100.00")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_agent_run_billing.py -k cancel -v
```

Expected: collection error — `ImportError: cannot import name 'cancel_run'`.

- [ ] **Step 3: Add the status**

In `backend/app/models/agent_run.py`, add to `AgentRunStatus`:

```python
    CANCELLED = "cancelled"
```

and change `TERMINAL_STATUSES` to:

```python
TERMINAL_STATUSES = {
    AgentRunStatus.SUCCEEDED, AgentRunStatus.FAILED, AgentRunStatus.CANCELLED,
}
```

No migration: `enum_column` uses `native_enum=False` and SQLAlchemy 2.0 defaults `create_constraint=False`, so the column is a plain `VARCHAR(32)`.

- [ ] **Step 4: Extract the refund and add `cancel_run`**

In `backend/app/services/agent_runs.py`, replace the tail of `finish_run` and add the new function, so the two terminal paths share one idempotent refund:

```python
async def _refund(run: AgentRun, db: AsyncSession) -> None:
    """Returns the run's charge. Idempotent: a second call never double-credits.
    The user received nothing, so charging would be charging for the system's
    inability to do the job — or, for a cancel, for a job never started."""
    if await _already_refunded(run, db):
        return
    price = await _debit_amount(run, db)
    if price is None or price <= 0:
        return
    await wallet.credit(run.user_id, price, REASON_AGENT_REFUND, db, agent_run_id=run.id)


async def finish_run(
    run: AgentRun, *, succeeded: bool, reason: str | None, db: AsyncSession
) -> None:
    """Terminates a run. A failed run is refunded in full. Does NOT commit."""
    if run.status in TERMINAL_STATUSES:
        return

    run.status = AgentRunStatus.SUCCEEDED if succeeded else AgentRunStatus.FAILED
    run.failure_reason = reason

    if succeeded:
        return
    await _refund(run, db)


async def cancel_run(run: AgentRun, db: AsyncSession) -> None:
    """The user stopped the run at the confirmation gate. Terminal and refunded,
    but NOT a failure: nothing was attempted, so the UI must not offer the
    'record it manually instead' recovery that a real failure warrants.
    Does NOT commit."""
    if run.status in TERMINAL_STATUSES:
        return
    run.status = AgentRunStatus.CANCELLED
    await _refund(run, db)
```

Update the import at the top of the file to bring in `TERMINAL_STATUSES`:

```python
from app.models.agent_run import AgentRun, AgentRunStatus, TERMINAL_STATUSES
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_agent_run_billing.py tests/test_agent_run_model.py tests/test_agent_reconcile.py -v
```

Expected: all pass. `test_agent_reconcile.py` exercises the abandoned-run path and must still work with the widened terminal set.

- [ ] **Step 6: Lint and commit**

```bash
cd backend && uv run ruff check app
```

```bash
git add backend/app/models/agent_run.py backend/app/services/agent_runs.py backend/tests/test_agent_run_billing.py
git commit -m "feat(agent): cancelled is its own terminal state, refunded like a failure"
```

---

### Task 8: The confirmation card can correct the URL

**Files:**
- Modify: `backend/app/schemas/agent.py`, `backend/app/api/agent.py`, `backend/app/agent/runner.py`
- Test: `backend/tests/test_agent_api.py`

**Interfaces:**
- Consumes: `cancel_run` and `AgentRunStatus.CANCELLED` from Task 7.
- Produces: `ConfirmUrlIn(ok: bool, url: str | None = None)`; `runner.UrlDecision(confirmed: bool, url: str | None)`; `runner.valid_start_url(url: str | None) -> str | None`; `await_url_confirmation(...) -> UrlDecision`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_agent_api.py`:

```python
from app.agent.runner import UrlDecision, valid_start_url


def test_valid_start_url_accepts_an_http_url():
    assert valid_start_url("https://waltonbd.com/search") == "https://waltonbd.com/search"


def test_valid_start_url_strips_surrounding_whitespace():
    assert valid_start_url("  https://waltonbd.com  ") == "https://waltonbd.com"


def test_valid_start_url_rejects_a_javascript_url():
    assert valid_start_url("javascript:alert(1)") is None


def test_valid_start_url_rejects_a_scheme_without_a_host():
    assert valid_start_url("https://") is None


def test_valid_start_url_rejects_an_overlong_url():
    assert valid_start_url("https://x.com/" + "a" * 2100) is None


def test_valid_start_url_passes_none_through():
    assert valid_start_url(None) is None
```

And add the route tests, following the file's existing pattern for building an owned run and calling `agent_api.confirm_url` directly:

```python
@pytest.mark.asyncio
async def test_confirm_publishes_an_edited_url(db, make_user, redis):
    user = await _funded_pro(db, make_user)
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.AWAITING_CONFIRM)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    pubsub = redis.pubsub()
    await pubsub.subscribe(cmd_channel(run.id))

    await agent_api.confirm_url(
        run.id, ConfirmUrlIn(ok=True, url="https://waltonbd.com/search"), user, db,
    )

    for _ in range(20):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message and message["type"] == "message":
            assert json.loads(message["data"]) == {
                "t": "confirm_url", "ok": True, "url": "https://waltonbd.com/search",
            }
            break
    else:
        pytest.fail("no confirm_url command was published")

    await pubsub.unsubscribe(cmd_channel(run.id))
    await pubsub.aclose()


@pytest.mark.asyncio
async def test_confirm_rejects_a_javascript_url_with_400(db, make_user):
    user = await _funded_pro(db, make_user)
    run = AgentRun(user_id=user.id, prompt="p", status=AgentRunStatus.AWAITING_CONFIRM)
    db.add(run)
    await db.commit()
    await db.refresh(run)

    with pytest.raises(HTTPException) as exc:
        await agent_api.confirm_url(
            run.id, ConfirmUrlIn(ok=True, url="javascript:alert(1)"), user, db,
        )
    assert exc.value.status_code == 400
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && uv run pytest tests/test_agent_api.py -k "valid_start_url or edited_url or javascript" -v
```

Expected: collection error — `ImportError: cannot import name 'UrlDecision'`.

- [ ] **Step 3: Widen the schema**

In `backend/app/schemas/agent.py`, replace:

```python
class ConfirmUrlIn(BaseModel):
    ok: bool
```

with:

```python
class ConfirmUrlIn(BaseModel):
    ok: bool
    # The planner picks the start URL from model knowledge alone, with no
    # search and no verification, so it is sometimes wrong. This is the
    # user's correction channel.
    url: str | None = None
```

- [ ] **Step 4: Add the validator and the decision type**

In `backend/app/agent/runner.py`, add `from dataclasses import dataclass` and `from urllib.parse import urlparse` to the imports, then add below the `MAX_ATTEMPTS` / `WALL_CLOCK_SECONDS` constants:

```python
MAX_START_URL_CHARS = 2048


@dataclass(frozen=True)
class UrlDecision:
    confirmed: bool
    url: str | None = None


def valid_start_url(url: str | None) -> str | None:
    """Normalizes a user-supplied start URL, or None if it is unusable.

    Applied on both sides of the command channel: the route uses it to answer
    400 so the card can show the error inline, and the runner re-applies it
    because Redis is not a trusted input.
    """
    if not url:
        return None
    candidate = url.strip()
    if len(candidate) > MAX_START_URL_CHARS:
        return None
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return candidate
```

- [ ] **Step 5: Return a decision instead of a bool**

In `backend/app/agent/runner.py`, replace the body of `await_url_confirmation` (keeping its docstring) so its signature and return become:

```python
async def await_url_confirmation(run_id: uuid.UUID, timeout_s: float = 300.0) -> UrlDecision:
    """Blocks until the user confirms or rejects the resolved URL.

    Uses the same command-channel pattern the recorder already uses for
    pick-mode and undo — no new transport. A timeout is a cancellation, not a
    failure: the user walked away from a gate, they did not get a broken API.
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
                if not command.get("ok"):
                    return UrlDecision(confirmed=False)
                return UrlDecision(confirmed=True, url=valid_start_url(command.get("url")))
        return UrlDecision(confirmed=False)
    finally:
        await pubsub.unsubscribe(cmd_channel(run_id))
        await pubsub.aclose()
```

- [ ] **Step 6: Apply the decision in `run_agent`**

In `backend/app/agent/runner.py`, replace this block:

```python
    await _set_status(agent_run_id, AgentRunStatus.AWAITING_CONFIRM, resolved_url=plan["url"])
    if not await await_url_confirmation(agent_run_id):
        await _finish(
            agent_run_id, succeeded=False,
            reason="user did not confirm the resolved URL", tokens=0,
        )
        return
```

with:

```python
    await _set_status(agent_run_id, AgentRunStatus.AWAITING_CONFIRM, resolved_url=plan["url"])
    decision = await await_url_confirmation(agent_run_id)
    if not decision.confirmed:
        await _cancel(agent_run_id)
        return
    if decision.url:
        # The user corrected the planner's guess. It becomes the plan's start
        # URL and the workflow's, so every later step and every replay uses it.
        plan["url"] = decision.url
        async with async_session() as db:
            run = await db.get(AgentRun, agent_run_id)
            run.plan = {**plan}
            run.resolved_url = decision.url
            await db.commit()
```

Then add the `_cancel` helper next to `_finish`:

```python
async def _cancel(run_id: uuid.UUID) -> None:
    async with async_session() as db:
        run = await db.get(AgentRun, run_id)
        if run is None:
            return
        await agent_runs.cancel_run(run, db)
        await db.commit()
    await publish(run_id, {"t": "status", "state": "cancelled", "reason": None})
```

- [ ] **Step 7: Validate in the route**

In `backend/app/api/agent.py`, change the import:

```python
from app.agent.runner import cmd_channel
```

to:

```python
from app.agent.runner import cmd_channel, valid_start_url
```

and replace the body of `confirm_url` after the ownership check:

```python
    await _owned_run(run_id, user, db)
    await redis_client.publish(cmd_channel(run_id), json.dumps({"t": "confirm_url", "ok": body.ok}))
```

with:

```python
    await _owned_run(run_id, user, db)

    url = None
    if body.ok and body.url:
        url = valid_start_url(body.url)
        if url is None:
            raise HTTPException(
                status_code=400,
                detail="Enter a full http:// or https:// address.",
            )

    await redis_client.publish(
        cmd_channel(run_id),
        json.dumps({"t": "confirm_url", "ok": body.ok, "url": url}),
    )
```

- [ ] **Step 8: Run the tests to verify they pass**

```bash
cd backend && uv run pytest tests/test_agent_api.py tests/test_agent_runner_integration.py -v
```

Expected: all pass. The integration test's `_confirm_repeatedly` helper publishes `{"t": "confirm_url", "ok": True}` with no `url` key, which `valid_start_url(None)` turns into `None` — the planner's URL is kept, unchanged behaviour.

- [ ] **Step 9: Lint and commit**

```bash
cd backend && uv run ruff check app
```

```bash
git add backend/app/schemas/agent.py backend/app/api/agent.py backend/app/agent/runner.py backend/tests/test_agent_api.py
git commit -m "feat(agent): let the user correct the resolved start URL"
```

---

### Task 9: The confirmation card, in the browser

**Files:**
- Modify: `frontend/src/lib/agentTypes.ts`, `frontend/src/hooks/useAgentRun.ts`, `frontend/src/pages/AgentBuilder.tsx`
- Test: none — this repo has no frontend test framework. Verified by `tsc`, `oxlint`, and a browser check.

**Interfaces:**
- Consumes: the `cancelled` status and the `url` confirm field from Tasks 7 and 8.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add the status to the type union**

In `frontend/src/lib/agentTypes.ts`, change:

```ts
  | 'succeeded'
  | 'failed'
```

to:

```ts
  | 'succeeded'
  | 'failed'
  | 'cancelled'
```

and add `result_shape?: 'list' | 'detail'` to the `AgentPlan` interface:

```ts
export interface AgentPlan {
  url?: string
  summary?: string
  result_shape?: 'list' | 'detail'
  parameters?: AgentPlanParameter[]
  fields?: AgentPlanField[]
}
```

- [ ] **Step 2: Teach the hook that cancelled is terminal, and send the URL**

In `frontend/src/hooks/useAgentRun.ts`, change:

```ts
const TERMINAL_STATES = new Set(['succeeded', 'failed'])
```

to:

```ts
const TERMINAL_STATES = new Set(['succeeded', 'failed', 'cancelled'])
```

and change `confirmUrl` to carry the edited value:

```ts
  const confirmUrl = useCallback(
    async (ok: boolean, url?: string) => {
      if (!runId) return
      await api.post(`/agent/runs/${runId}/confirm`, { ok, url })
    },
    [runId],
  )
```

- [ ] **Step 3: Add the labels**

In `frontend/src/pages/AgentBuilder.tsx`, add to `STATUS_LABEL`:

```ts
  cancelled: 'Cancelled',
```

and to `STATUS_BADGE`:

```ts
  cancelled: 'pending',
```

- [ ] **Step 4: Make the URL editable**

In `frontend/src/pages/AgentBuilder.tsx`, add `Input` and `FieldError` to the imports from `'../components/ui'`.

Replace the `handleConfirm` function and the `awaiting_confirm` card. First, the state and handler at the top of `RunProgress`:

```tsx
  const { run, activity, checks, connectionError, confirmUrl } = useAgentRun(runId)
  const [confirming, setConfirming] = useState(false)
  const [urlDraft, setUrlDraft] = useState<string | null>(null)
  const [urlError, setUrlError] = useState<string | null>(null)
```

```tsx
  async function handleConfirm(ok: boolean) {
    setConfirming(true)
    setUrlError(null)
    try {
      await confirmUrl(ok, ok ? (urlDraft ?? run?.resolved_url ?? undefined) : undefined)
    } catch (err) {
      setUrlError(err instanceof Error ? err.message : 'Could not confirm that address')
    } finally {
      setConfirming(false)
    }
  }
```

Then replace the whole `awaiting_confirm` block:

```tsx
      {run.status === 'awaiting_confirm' && run.resolved_url && (
        <div className={cardClasses({ variant: 'callout', accent: 'blue' })}>
          <CapsLabel tone="blue" className="mb-2">
            Confirm the target site
          </CapsLabel>
          <p className="mb-3 text-sm text-ink/70">
            The agent picked this from the description. Edit it if it&apos;s wrong.
          </p>
          <Input
            aria-label="Target site URL"
            value={urlDraft ?? run.resolved_url}
            error={!!urlError}
            disabled={confirming}
            onChange={(e) => setUrlDraft(e.target.value)}
            className="mb-1"
          />
          {urlError && <FieldError>{urlError}</FieldError>}
          {run.plan.result_shape && (
            <p className="mb-4 mt-2 text-sm text-ink/60">
              Returns {run.plan.result_shape === 'list' ? 'a list of results' : 'one record'}.
            </p>
          )}
          <div className="mt-4 flex gap-3">
            <Button variant="primary" disabled={confirming} onClick={() => handleConfirm(true)}>
              Confirm
            </Button>
            <Button variant="ghost" disabled={confirming} onClick={() => handleConfirm(false)}>
              Cancel run
            </Button>
          </div>
        </div>
      )}
```

- [ ] **Step 5: Give cancelled its own terminal card**

In `frontend/src/pages/AgentBuilder.tsx`, add this block directly above the existing `run.status === 'failed'` block:

```tsx
      {run.status === 'cancelled' && (
        <div className={cardClasses({ variant: 'callout', accent: 'gold' })}>
          <CapsLabel tone="gold" className="mb-2">
            Cancelled
          </CapsLabel>
          <p className="mb-4 text-ink/80">
            You stopped this run before it started. You haven&apos;t been charged.
          </p>
          <Link to="/build" className={buttonClasses('primary')}>
            Start over
          </Link>
        </div>
      )}
```

The failed card keeps its "Record it manually instead" link; a cancelled run does not get it, because nothing failed.

- [ ] **Step 6: Typecheck and lint**

```bash
cd frontend && npm run build
```

Expected: `tsc -b` passes with no errors, Vite build succeeds. A missing `cancelled` key in either `Record<AgentRunStatus, …>` map is a compile error, so this step is the real test for Step 3.

```bash
cd frontend && npm run lint
```

Expected: no findings.

- [ ] **Step 7: Verify in the browser**

Start the dev servers and open the agent builder. With a run at the confirmation gate:

1. The URL appears in an editable input, pre-filled.
2. Editing it and pressing Confirm starts the run against the edited address.
3. Entering `javascript:alert(1)` and pressing Confirm shows the inline error and leaves the run at the gate.
4. Pressing "Cancel run" lands on the gold Cancelled card, not the red failure card, and offers "Start over".

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/agentTypes.ts frontend/src/hooks/useAgentRun.ts frontend/src/pages/AgentBuilder.tsx
git commit -m "feat(frontend): editable start URL and a real cancelled state"
```

---

### Task 10: Full-suite regression and the end-to-end acceptance check

**Files:** none modified unless a regression is found.

- [ ] **Step 1: Run the whole backend suite**

```bash
cd backend && uv run pytest -q
```

Expected: all pass. Investigate any failure before proceeding — do not adjust an assertion to make it green without understanding why it moved.

- [ ] **Step 2: Run the opt-in live acceptance check**

`backend/tests/test_agent_integration.py` runs the real thing against `waltonbd.com` — real plan, real drive loop with no LLM mocking, real distill/extract/verify. It is skipped unless `RUN_AGENT_INTEGRATION=1`, hits the live network, and spends real LLM API calls.

```bash
cd backend && RUN_AGENT_INTEGRATION=1 uv run pytest tests/test_agent_integration.py -v -s
```

Expected outcome: the same prompt that produced the null-returning API either produces a working list API, or fails visibly with `fields_present` or `stable_selectors` named in the failure reason. **Publishing nulls is the one outcome that must not occur.**

- [ ] **Step 3: Record the result**

If the run now fails rather than succeeds, that is an acceptable outcome for this change — the goal was to stop shipping broken APIs, not to guarantee this site works. Note the observed behaviour in the commit message.

- [ ] **Step 4: Commit any fixes**

```bash
cd backend && uv run ruff check app
```

```bash
git add <the exact files you changed>
git commit -m "test(agent): full-suite regression after the correctness changes"
```

Skip this commit if nothing changed. Name the changed files explicitly — `git add backend/` would violate this plan's Global Constraints and sweep up unrelated work.

---

## Notes for the implementer

- **Tasks 1 and 2 are the highest-value pair.** If time runs short, they alone convert "published a broken API" into "attempt failed, retrying".
- **Task 3 must not change production behaviour.** If any pre-existing replay test changes its result, the implementation is wrong — revisit before proceeding.
- **The `stable_selectors` false positive is known and accepted.** A genuinely static `button:has-text("Search")` that flakes will fail a run. It costs one retry; the alternative costs a wrong API. Do not add exemptions to suppress it without a spec change.
- **Do not add a fill-rate threshold** to `missing_field_names`. Sparse fields are real data; see Task 1 Step 1's third test for the reasoning.

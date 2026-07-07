# AI-Assisted Authoring — Implementation Plan

**Status:** ready to build · **Audience:** the coding agent (Sonnet) implementing this
**Prereq reading:** [BLUEPRINT.md](BLUEPRINT.md) §4.5 (steps), §4.6 (extraction), §4.7
(parameters), §6 (LLM integration) · [DESIGN.md](DESIGN.md) for any UI.

## Goal

Add an **AI-assisted authoring** feature to the recorder: the LLM reads the recorded
steps (and a DOM/extraction sample) and *suggests* what should become API parameters,
what to name/type them, and what to name extraction fields. Every suggestion is
**advisory** — the user accepts it, which flows through the *existing* `mark_param` /
`set_extraction` paths. Nothing is auto-applied.

Three sub-features, built in order, sharing one LLM call and one prompt family:

1. **Parameter naming & typing** — given a `fill` step's selector + typed value, propose
   `{name, type, example, description}`.
2. **Parameter suggestion** — scan the recorded value steps and propose *which* inputs
   should be parameters at all (the ranked list).
3. **Extraction field naming** — given the current extraction selectors + a sample row,
   propose `{name, take, transform}` per field.

## Provider change: local llama.cpp → CraftX (hosted, OpenAI-compatible)

We are **not** using the Anthropic SDK. CraftX exposes an **OpenAI-compatible**
`/api/v1/chat/completions` endpoint, and our existing client
([backend/app/llm/client.py](../backend/app/llm/client.py)) already uses `AsyncOpenAI`.
So the swap is a base-URL + key + model change behind a provider switch — the local
llama.cpp path must keep working (`LLM_PROVIDER=llama`).

Config already added to `.env` / `.env.example`:

```
LLM_PROVIDER=craftx              # "craftx" | "llama"
CRAFTX_BASE_URL=https://api.craftx.corecraftsolutions.com/api/v1
CRAFTX_API_KEY=cc-...            # real key is in .env (gitignored)
CRAFTX_MODEL=CraftX Qwen2.5 72B Instruct
```

`CraftX Qwen2.5 72B Instruct` is a **non-thinking instruct model** — chosen deliberately
over the Thinking variant: no reasoning tokens to burn the output budget, ~7.5× cheaper
output (৳0.060 vs ৳0.45 / 1K), faster, and fully adequate for bounded structured
extraction. `CRAFTX_MODEL` is env-configurable if you ever want to swap.

### ⚠️ Gotchas that WILL bite you

1. **Don't assume the whole response is clean JSON.** Even an instruct model may wrap
   output in a Markdown ` ```json … ``` ` fence or add a sentence of prose.
   `response_format: {"type": "json_schema", ...}` is honored by llama.cpp but a gateway
   in front of vLLM/Qwen may ignore or reject it. **Keep sending `response_format`
   (harmless if honored) but never depend on it** — add a `_extract_json(content)` helper
   that strips code fences / any `<think>…</think>` block and pulls the first balanced
   top-level `{…}` before `json.loads`. This also future-proofs a swap back to the
   Thinking model.
2. **Token budget.** No reasoning overhead now, so the existing `max_tokens` is roughly
   fine, but bump the `complete_json` default to **~2000** for headroom on the
   multi-parameter output. Keep the client `timeout` generous (**180s**) — 72B latency
   over a shared gateway is unpredictable.
3. **Sampling params.** Keep `temperature=0.2`; if CraftX 400s on any sampling field,
   drop it. This is the OpenAI shape — don't send Anthropic-only fields (`output_config`,
   `thinking`).

### Security note
The `CRAFTX_API_KEY` was shared in plaintext in the chat that produced this plan. It
lives only in `.env` (gitignored), but if that transcript is shared, **rotate the key**
in the CraftX dashboard and update `.env`.

---

## Phase 0 — Provider abstraction (foundation)

Make `complete_json` provider-aware; keep the fallback discipline intact.

**Files:** [backend/app/config.py](../backend/app/config.py),
[backend/app/llm/client.py](../backend/app/llm/client.py)

- `config.py`: add `llm_provider: str = "llama"`, `craftx_base_url: str = ""`,
  `craftx_api_key: str = ""`, `craftx_model: str = ""`.
- `client.py`: build the `AsyncOpenAI` client from the active provider
  (`base_url`/`api_key`/model from settings). Add a `_extract_json(content: str) -> dict`
  helper that strips Markdown code fences and any `<think>…</think>` block, then parses
  the first balanced `{…}`. Route `complete_json`'s return through it instead of a bare
  `json.loads`. Raise a clear error (so callers' fallbacks fire) when no JSON is found.
- Keep `complete_json`'s signature identical so `enrich.py` is untouched.

**Acceptance:** with `LLM_PROVIDER=craftx`, existing **spec enrichment** (the
`jobs:llm` → `generate_spec` → `enrich_spec` path) still produces a valid, enriched
OpenAPI spec end-to-end; with `LLM_PROVIDER=llama` behavior is unchanged; with the
CraftX endpoint unreachable, `generate_spec` still ships the fallback skeleton
(`x-llm-enriched: false`). Add one unit test for `_extract_json` covering a
code-fence-wrapped payload, a `<think>`-wrapped payload, and bare JSON.

## Phase 1 — Parameter naming & suggestion

New module: `backend/app/llm/authoring.py`. New prompts in
[backend/app/llm/prompts.py](../backend/app/llm/prompts.py).

**Input** (all already in-memory in the recorder — see Phase 3): the `steps` list.
**Candidates are only `fill` / `select_option` steps** (`VALUE_STEP_TYPES` in
session.py) still holding `value: {literal}` — those are the only steps
`_handle_mark_param` can convert, so anything else is a dead-end suggestion the user
can't accept. Do **not** mine `goto` URL query params (`?page=2`): replay has no URL
templating, so a URL param can't become an API parameter today — leave it as noted
future work, never a suggestion. Give the model each candidate's first selector +
literal value, plus `humanize_steps` output for flow context.

**Redaction (privacy — mandatory):** recorded literals can contain credentials — the
user may log in mid-recording, and injected.js does no input-type filtering, so a typed
password lands in `steps` as a plain `fill` literal. Before prompt-building, drop any
candidate whose selectors match `password|passwd|pwd|otp|pin|cvv|secret` (case-
insensitive); cap every literal sent to the API at ~120 chars. Redacted steps are never
sent and never suggested.

**Output schema** (pass as `response_format`, but parse defensively):

```json
{
  "parameters": [
    {"step_i": 1, "name": "query", "type": "string|integer|number|boolean",
     "example": "python", "description": "Search term", "confidence": 0.0-1.0}
  ]
}
```

- `authoring.py`: `async def suggest_parameters(steps) -> list[dict]`. Build the prompt,
  call `complete_json`, validate each item (`step_i` in range, step is a value-type step,
  `name` is a safe identifier), drop invalid ones. Reuse `enrich.py`'s **two-attempt
  retry** shape (append the validation error to the prompt on retry).
- Only propose params for steps not *already* marked (`value` has `literal`, not `param`).

**Acceptance:** given a recorded search flow, `suggest_parameters` returns the search
field as a `string` param named sensibly with the typed value as `example`; a numeric
quantity field comes back typed `integer`. Suggestions only ever reference
`fill`/`select_option` steps holding literals; hallucinated `step_i` values and
non-candidate steps are dropped, not returned; password-like fields are never sent to
the API or suggested. Unit-tested against a fixture `steps` list with the CraftX call
mocked.

## Phase 2 — Extraction field naming

Add `async def suggest_extraction_fields(config: dict, sample: dict|list) -> list[dict]`
to `authoring.py`.

**Input:** the current `extraction["main"]` config (field selectors + `take`) and a
freshly-run **sample row** (the recorder already produces this via
`run_extraction` → `test_extraction`, [session.py](../backend/app/recorder/session.py)
`_handle_test_extraction`). Give the model each field's selector + a truncated sample
value.

**Output:** `[{"selector": "...", "name": "price", "take": "text",
"transform": "number|abs_url|null"}]` — must line up with §4.6's field shape so the panel
can drop them straight into the extraction config.

**Acceptance:** for a book-list sample, proposes `title/text`, `price/number`,
`url/abs_url` (matching BLUEPRINT §4.6's example). Runs only when a sample exists;
errors surface as a normal `error` event, never a crash.

## Phase 3 — Wire into the live recorder + panel

Follow the **existing command/event pattern** exactly (cmd over `cmd_channel`, results
via `_publish` on `evt_channel`). No new Redis Stream, no new DB round-trip — suggestions
are ephemeral and advisory.

**Backend** — [backend/app/recorder/session.py](../backend/app/recorder/session.py):
- **Extend `_handle_mark_param`** to honor optional `type` and `description` fields on
  the command (validate `type` against `{"string","integer","number","boolean"}`,
  falling back to `"string"`; `description` defaults to `None`). Today it hardcodes
  `type="string"`, `description=None` — without this, accepting a suggestion silently
  discards the LLM's type/description and Phase 1's typing work is wasted at the moment
  of acceptance. Manual marking (bare command, no extra fields) behaves exactly as
  before, and there is still exactly one write path to `self.parameters`. The declared
  type is load-bearing downstream: replay coerces params by type (the 422 path) and the
  OpenAPI skeleton emits it.
- Add a `suggest_authoring` command in `_handle_command` (next to `mark_param` /
  `set_extraction`). It must **spawn a background `asyncio.Task`** (not `await` inline)
  so a slow LLM call doesn't block the command loop. Task lifecycle rules: keep the
  reference on `self._authoring_task` (a bare `create_task` with no reference can be
  GC'd mid-flight); ignore repeat commands while one is in flight; cancel it alongside
  the heartbeat/watchdog/command tasks in `_run_in_context`'s `finally` so it can't
  publish after the session closes or touch a closed browser context.
- The task calls `suggest_parameters(self.steps)` and, if `self.extraction.get("main")`
  and the page is live, `run_extraction` + `suggest_extraction_fields`. Guard the whole
  thing with `settings.llm_enabled`; on any exception publish
  `{"t": "error", "message": "..."}` — never let it kill the session.
- Publish `{"t": "authoring_suggestions", "parameters": [...], "extraction_fields": [...]}`.

**Frontend** — [useRecorder.ts](../frontend/src/hooks/useRecorder.ts) +
[RecorderStepList.tsx](../frontend/src/components/RecorderStepList.tsx):
- Add a "✨ Suggest parameters" button that sends `{t: "suggest_authoring"}` (show a
  pending/spinner state — the call is slow).
- Handle the `authoring_suggestions` event: render suggested params inline on their steps
  (name/type/example, with confidence) each with an **Accept** action that sends
  `{t: "mark_param", step_i, name, type, description}` (the extended command above) and
  a **Dismiss** action. Clear a step's suggestion when its `param_marked` event arrives
  or the step is undone. Render suggested extraction field names as accept-able edits to
  the extraction config (merge into local state, send the existing `set_extraction`).
  Keep suggestions on the main page only for v1 — skip the PiP card to bound scope.
- Style per [DESIGN.md](DESIGN.md) (Warm Editorial). Suggestions must read as
  *proposals* (dismissible, clearly AI-generated), not applied state.

**Acceptance:** in a live recording, clicking "Suggest parameters" shows suggestions
within the pending state; Accept marks the param via the normal path and it appears in
the step list exactly as a manual mark would; with `LLM_ENABLED=false` or CraftX down,
the button surfaces a graceful error and manual marking is unaffected.

---

## Guardrails (do not violate)

- **LLM never blocks authoring.** Same discipline as spec generation (§6.4): every LLM
  path has a working manual fallback. `LLM_ENABLED=false` must leave the recorder fully
  usable.
- **LLM runs only in the worker process** (the recorder session lives there) — never in
  FastAPI. FastAPI ↔ recorder stays Redis-only.
- **Suggestions are advisory.** Acceptance always routes through the existing
  `mark_param` / `set_extraction` handlers — do not add a second write path to
  `self.parameters` / `self.extraction`.
- **Parse defensively.** Assume the model returns `<think>`-wrapped, possibly-malformed
  JSON; validate every field before trusting it.
- Keep `jobs:llm` concurrency at 1 in [main.py](../backend/app/workers/main.py) (the
  comment's VRAM rationale is now moot, but serializing hosted calls is still fine and
  cheap). This feature runs *inside the recorder session*, not on `jobs:llm`, so it is
  independent of that queue.

## Test checklist

- `_extract_json`: `<think>`-wrapped payload, bare JSON, no-JSON (raises).
- `suggest_parameters`: happy path, hallucinated `step_i` dropped, already-marked steps
  skipped (CraftX mocked).
- Redaction: a fixture step with a password-like selector never reaches the prompt and
  never comes back as a suggestion.
- `mark_param` with `type`/`description`: accepted values persist to the parameter;
  an invalid type falls back to `"string"`; the bare command is byte-for-byte unchanged
  in behavior.
- `suggest_extraction_fields`: book-list fixture → sensible names/transforms.
- Manual E2E: `LLM_PROVIDER=craftx` real call against a recorded flow; confirm latency,
  token usage, and that Accept → step list matches manual marking.
- Regression: `LLM_PROVIDER=llama` spec enrichment unchanged; CraftX-down fallback works.
- `cd backend; uv run ruff check app` and `uv run pytest` clean.

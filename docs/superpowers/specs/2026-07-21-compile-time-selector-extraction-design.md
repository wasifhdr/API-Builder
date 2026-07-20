# Compile-Time Selector Extraction (LLM-as-Selector-Compiler) Design

**Status:** Approved (2026-07-21)
**Supersedes (behaviorally):** the runtime value-extraction model of
`docs/superpowers/plans/2026-07-20-llm-first-semantic-extraction.md`. That plan's
`semantic_extract` value-reader is retained only as a last-resort replay floor.

## Problem

The current `engine:"llm"` path reads a page's visible `innerText` at replay time and
asks the LLM to return field values on **every API call**. In practice this returns
`null` in many cases because:

- **Single-mode extraction lost its readiness gate.** `_wait_for_extraction_ready`
  gates on `fields[0].selector`; LLM-first configs leave selectors blank, so the wait
  returns immediately and extraction races an un-rendered SPA — `innerText` is empty and
  every field is `null`.
- **The 12k `PAGE_TEXT_CAP` truncation** slices `body.innerText`; on real sites the first
  12k chars are cookie/nav/promo boilerplate and the marked content is cut off.
- **Flattened `innerText` destroys structure**, so the model cannot reliably associate a
  price with its hotel name, and — correctly told never to invent — resolves to `null`.

Beyond nulls, running the LLM per request makes the published API non-deterministic and
adds a token round-trip to every call — the wrong shape for a "stable JSON HTTP API"
product.

## Approach

Move the LLM from a **runtime value-reader** to a **compile-time selector-generator**:

- **Authoring** produces durable, ranked, validated CSS selectors per field.
- **The published API** runs pure deterministic selector extraction — fast, free per
  call, byte-for-byte stable output.
- **The LLM re-engages only when a stored selector has genuinely broken** (self-heal),
  which is rare and is exactly when a human would otherwise re-record by hand.

### Decisions (locked)

1. **Compile at pick time**, in the worker — LLM stays entirely off the API hot path;
   the user holds the exact picked element and the correct live page state.
2. **DOM + screenshot crop** as compiler input (Gemma is multimodal). DOM context is the
   primary signal ("which anchors are stable vs. auto-generated"); the crop disambiguates
   DOM-identical-but-visually-distinct cases.
3. **Ranked list of validated selectors per field** (best-first). Replay tries each in
   order; a small DOM tweak that breaks the top anchor falls through without an LLM call.
4. **Self-heal persists to a keyed cache**, not the authored snapshot.
5. **Full list-mode support** — compile a ranked `roots` list plus row-relative selectors
   per field.
6. **Pick-driven wizard** replaces the Single/List toggle; inline per-field naming.

## Data model

Per-field extraction config (JSONB — loosely validated, no schema migration):

```jsonc
{
  "name": "price",
  "description": "starting nightly price in BDT",   // from the inline wizard form
  "example": "BDT 14,049",                            // captured at pick time (preview)
  "take": "text",                                     // or "attr:href" / "attr:src" / "html"
  "transform": "number",
  "selectors": [                                      // NEW: ranked, compiled, validated
    ".hotel-card [data-testid=price]",
    ".hotel-card .price",
    "div > span:nth-of-type(2)"
  ]
}
```

Config level: `mode: "single" | "list"`; for list mode a ranked `roots: [...]`. `engine`
persists as an internal authoring marker but is **not** a user-facing toggle.

**Back-compat:** replay reads `selectors` (plural) when present, else falls back to a
legacy single `selector`. Legacy `engine:"llm"` value-extraction configs keep running via
the retained `semantic_extract` floor.

### Selector cache (new)

A small durable table, upserted by self-heal:

```
extraction_selector_cache(workflow_id, ref, field_name, selectors JSONB, healed_at)
  primary key (workflow_id, ref, field_name)
```

Keyed upsert sidesteps the concurrent-replay race on the workflow snapshot JSONB and
keeps the authored snapshot immutable. (Considered and rejected: a Redis hash — no
migration, but flushable; durability wins here.)

## Components

### Pick-time compiler — `backend/app/recorder/selector_compiler.py` (worker-only)

`async def compile_selectors(page, picked, config, mode) -> list[str]`

On value confirmation, the worker:

1. Stamps the picked element with a unique `data-ab-pick` attribute so candidates are
   verified against *that exact element*.
2. Gathers a compact DOM outline (element + ancestors: tags, classes, `data-*`, `role`,
   `aria`, text — generated-looking values stripped), the heuristic `rankSelectors`
   candidates, the `example` value, and a **screenshot cropped to the element's bounding
   rect**.
3. Calls the LLM (multimodal) → a ranked candidate list (semantic → structural →
   positional).
4. **Validates every candidate against the live DOM.** Single: `querySelector` resolves
   to the stamped element. List: within the element's root-row, the row-relative selector
   resolves to it. Unvalidated candidates are dropped.
5. If nothing validates (or the LLM is down), returns the heuristic `rankSelectors`
   output — **authoring never blocks**.

Requires a **multimodal path in `complete_json`** (`backend/app/llm/client.py`): accept
image content parts (base64 `image_url`), which Google's OpenAI-compat layer supports.
Reuses the existing `_LLM_LOCK` (concurrency 1).

### Replay + self-heal — `backend/app/recorder/replay.py`

For a compiled config's `extract` step, per field:

1. Try stored `selectors` in order (cache overlay first, then authored). Hit → done;
   deterministic, zero LLM cost (the common case).
2. All miss → **self-heal**: re-run `compile_selectors` against the live page, anchored on
   the field's `description` + `example` (no human present). Validate the result resolves
   to a non-empty value.
3. Persist the healed selectors to `extraction_selector_cache` via upsert.
4. Self-heal fails → last-resort fallback to `semantic_extract` (retained value-reader),
   else `null`. **Never raises.**

**Per-field routing preserved:** `attr:*`/`html` fields always stay on the selector path;
the LLM only ever finds the *element*, never produces the attribute value. The existing
`_merge_extraction` overlay stays.

### Wizard UX — recorder pop-out (`RecorderPipCard.tsx`, `RecorderSession.tsx`, `useRecorder`)

State machine replacing the free-form toggle + buttons:

```
Pick element ─▶ [ Single record | List of records ]
   Single ─▶ Choose values ⇄ (pick → confirm → name+describe inline → added) ─▶ Done
   List   ─▶ Pick a root (pick → confirm) ─▶ Choose values ⇄ (…same…) ─▶ Done
```

- Same click-element-then-confirm mechanic; the confirm button is contextual
  ("Use as root" / "Add this value").
- **Quick undo** after a pick to discard a mis-click before it's committed.
- Inline per-field form in the pop-out: name + one-line description, with the captured
  example shown read-only.
- A running list of added fields; explicit **Done** finalizes the config.
- The picked selectors are compiled by the worker; the wizard shows the compiled
  selector + re-validated example.

### Workflow edit page — `ExtractionEditor.tsx`, `WorkflowEditor.tsx`

ExtractionEditor stays for touch-up (rename / describe / `take` / `transform` / delete).
**The Single/List and engine toggles are removed** — mode and engine are properties of how
the config was authored.

## Communication

FastAPI ↔ worker stays Redis-only; the WS endpoint stays a dumb bridge. The pop-out sends
a "compile field" command over the existing recorder command channel; the worker runs the
compiler and returns ranked selectors + the re-validated example to the pop-out. Playwright
and the LLM run **only** in the worker.

## Error handling (project-rule aligned)

- Authoring: LLM down → heuristic selectors; the wizard still works.
- Replay: stored selectors work fully offline; self-heal engages only when the LLM is
  configured; `semantic_extract` floor; extraction never blocks a replay.
- LLM concurrency 1 via the existing `_LLM_LOCK`; headless replay keeps `--disable-gpu`.

## Testing

- **Unit:** compiler prompt assembly + candidate validation (mock LLM); self-heal trigger
  + cache upsert/read; `complete_json` multimodal message shape; per-field merge.
- **Frontend:** wizard state-machine transitions (single/list, undo, done).
- **Integration:** replay selector-path + self-heal against the fixture site; back-compat —
  existing `engine:"llm"` and legacy single-`selector` configs still run.
- **Migration:** adds `extraction_selector_cache` only.

## Out of scope

- Re-compiling all existing published workflows (they keep running on their stored path).
- Vision-only "find the value" extraction (the value-reader remains a floor, not primary).
- Any change to auth/billing/OpenAPI generation.

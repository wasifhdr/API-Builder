# Autonomous API Authoring ("Agent Builder") — Design

**Date:** 2026-08-13 · **Status:** design approved, not yet planned · **Owner:** Wasif Haider

**Prereq reading:** [BLUEPRINT.md](../../BLUEPRINT.md) §4.5 (steps), §4.6 (extraction), §4.7
(parameters), §6 (LLM) · [PRD.md](../../PRD.md) §6.2–6.4 · [DESIGN.md](../../DESIGN.md) for UI.

> **Not to be confused with** [AI_AUTHORING_PLAN.md](../../AI_AUTHORING_PLAN.md), which is the
> *shipped* AI-**assisted** authoring feature: advisory parameter/field suggestions offered to a
> human who is recording manually. This document describes AI-**autonomous** authoring: no human
> records anything. The two features coexist; neither replaces the other.

---

## 1. Goal

The user types a sentence — *"make me an API to search for products on the Walton website"* — and
the system produces a published, parameterized JSON API without the user recording anything.

The target is **full autonomy**. The existing manual recorder is not removed, modified, or
degraded; it remains the separate entry point it is today and serves as the user's fallback when
an agent run fails.

### Success criterion

An agent run succeeds only when the workflow it produced **replays correctly against a parameter
value the agent never saw during authoring**. "The agent said it was done" is not success.

---

## 2. Core insight

`Workflow` is already a plain JSON artifact — [`steps`, `parameters`, `extraction`](../../../backend/app/models/workflow.py)
— and the recorder is only one *producer* of it. Replay, publish, OpenAPI generation, caching,
metering, sharing, and admin moderation all consume that artifact and are indifferent to its
origin.

This feature is therefore **a second producer**, not a second system. Everything downstream of a
finished workflow is untouched.

A second, load-bearing fact: [`session.py`](../../../backend/app/recorder/session.py) captures
interactions by injecting a script into the page and exposing `__abEmit` — it listens to **real DOM
events**, not to Playwright's API. Playwright's `click()` / `fill()` dispatch trusted native events
through CDP, so those listeners fire identically for an agent and for a human hand.

**Consequence: an agent driving the browser through Playwright is recorded by the existing recorder,
for free** — ranked selector candidates, step DSL, live WebSocket panel, and undo all come along.
The agent never authors a selector. It supplies intent; the existing selector compiler supplies
durability.

> **Verified empirically 2026-08-13.** `fill`, `click`, `select_option`, and `press` are all
> captured with full ranked selector candidates when driven by Playwright. `isTrusted` is not a
> factor — Playwright's `fill()` produces a trusted input event, and `injected.js` does not filter
> on it regardless.
>
> The probe did surface one hazard the implementation must handle: `fill` is emitted on a **400 ms
> debounce** while `click` emits synchronously, so a fill immediately followed by a click is
> recorded in the **wrong order** — replay would click Search before typing the query. A human
> never types and clicks that fast; an agent does it on every search. The agent's action loop
> therefore settles longer than the debounce between tool calls, with a regression test pinning it.

---

## 3. Decisions taken

| Decision | Choice | Rationale |
|---|---|---|
| Autonomy | Fully autonomous end-to-end | Product goal; manual recorder stays as a separate path |
| Relationship to recorder | Build alongside, change nothing | Fallback path costs nothing and de-risks failure |
| Perception | Maximal — a11y tree + interactive elements + screenshot every step | Token cost is not a constraint; curated for *signal*, not truncated for budget |
| Replay | Fully deterministic — no LLM in the per-call path | Preserves the 90 s budget, per-call pricing, caching, reproducibility |
| Target URL | Agent resolves, user confirms once | Keeps the one-sentence UX; a wrong target is caught before a run is wasted |
| Failure handling | Bounded retries with a *different strategy*, then fail | Chosen over partial-draft handoff; agent tries harder rather than leaning on the user |
| Auth scope (v1) | Public sites only | The agent must never handle credentials |
| Cost | Wallet debit per attempt, Pro/Max only, refunded on failure | Mirrors FR-M4 |
| UI | A new, separate page | Keeps the recorder route untouched |
| Reference target | `waltonbd.com` | Local, conventional e-commerce, no enterprise bot protection |

### Explicitly out of scope for v1

- Logged-in / gated targets. The agent stops at a login wall and fails with that reason.
- Agent-in-the-loop replay of any kind. Replay stays deterministic.
- Partial-draft handoff into the manual recorder's step list. (The failure page instead offers a
  fresh manual recording with the URL prefilled — no partial-state merge to build.)
- Multi-tab and iframe flows, matching the recorder's existing limitations.
- Anti-bot circumvention. Unchanged non-goal from PRD §3.2.

---

## 4. Prerequisites

These are real gaps that must close before the feature can work. None are optional.

### 4.1 URL templating in replay

[AI_AUTHORING_PLAN.md](../../AI_AUTHORING_PLAN.md) (Phase 1) records that replay has **no URL
templating**, which is why `goto` query params are excluded from parameter suggestions today.

For autonomous authoring this is blocking, not cosmetic: URL-driven search (`/search?q=…`) is the
most common way an agent will reach a result set, and on many sites it is the *only* reliable way.
Without templating, the agent's parameter would be baked into a literal URL and the API would
ignore its own input.

[`_resolve_value`](../../../backend/app/recorder/replay.py) must gain a sibling that substitutes
parameters into `goto` URLs, and the distill pass (§6) must be able to emit a templated `goto`.

### 4.2 A tool-calling LLM primitive

[`complete_json`](../../../backend/app/llm/client.py) is single-shot: prompt in, one JSON object
out. An agent loop needs **multi-turn conversation with tool calls** and **image content parts**
for screenshots.

Add a sibling primitive on the same client, behind the same provider switch
(`llm_provider`, currently defaulting to `gemini`). Gemini's OpenAI-compatible endpoint supports
both function calling and base64 image parts; both should be verified early, as the whole design
rests on them.

`complete_json` and its callers stay untouched.

### 4.3 Credential redaction on every LLM path

The redaction rule from AI_AUTHORING_PLAN.md Phase 1 — drop any value whose selector matches
`password|passwd|pwd|otp|pin|cvv|secret`, cap literals at ~120 chars — applies to **every** point
where this feature sends recorded data to the model, including the distill and repair passes that
feed the transcript back.

v1 being scoped to public sites reduces but does not eliminate the exposure: an agent can wander
into a login form on an otherwise public site.

---

## 5. Runtime and process model

The agent run is a **new job type in the existing worker**. Playwright stays worker-only; FastAPI
never touches a browser.

**Concurrency.** An agent run consumes the single recording slot, because it *is* a recording
session. The `1 recording · 2 replays · 1 LLM job` budget gains no new dimension. One hazard to
design around: an agent run holds the recording slot while its verify phase requests a replay slot,
so verify must never be able to queue behind work that waits on the recorder.

**Headless, not headful.** This is the one deliberate departure from the current recorder:

1. **Verification is only meaningful if authoring conditions match replay conditions.** A workflow
   authored headful and verified headless tests two different browsers. This is the decisive reason.
2. The agent needs no window, and a headful run would seize the user's desktop for minutes on a
   feature sold as hands-off.
3. It leaves the desktop free.

Everything else from the recorder's context setup is retained: real Chrome channel,
[`stealth.py`](../../../backend/app/recorder/stealth.py) init script, realistic viewport and UA.
`browser_settings` can flip a run headful if a site rejects headless, without a design change.

**Transport.** Unchanged rules. FastAPI enqueues `{agent_run_id}` on the job stream. The worker
publishes progress to an `agent:{run_id}` pub/sub channel. The new page subscribes through the
existing dumb-bridge WebSocket. URL confirmation is a command over the same channel the recorder
already uses for pick-mode and undo — the worker publishes `awaiting_confirm` and blocks until the
user's command arrives. No new transport is introduced.

---

## 6. The pipeline

```
Plan ──► Drive ──► Distill ──► Verify ──┬── pass ──► ready ──► publish
  ▲                                     │
  └──────────── Repair ◄────────────────┘  (bounded)
```

### 6.1 Plan — before the browser opens

The LLM reads the sentence and emits, as structured output:

- the resolved target URL (surfaced for user confirmation),
- the declared **parameters**: name, type, required, plus **two** example values — a `drive_value`
  and a distinct `verify_value`,
- the declared **output fields**: names and types.

Planning first is what makes parameter binding tractable (§6.3) and gives verify something to check
against (§6.4). It also means the new page has something to render while the browser is still
working.

### 6.2 Drive

The agent drives a live recording session with a small tool set:

```
observe()  → a11y tree + interactive elements + screenshot, with ref handles
navigate(url)          click(ref)         fill(ref, value)      press(key)
scroll(direction)      mark_target(ref)   done()                give_up(reason)
```

Element refs come from `observe()`; the worker resolves a ref to a Playwright locator and acts on
it, and the injected recorder captures the resulting step with full ranked selectors.

`scroll` earns its place for lazily-loaded product grids — the dominant pattern on the reference
target. `mark_target` records which elements hold the data to extract.

Perception is maximal every step. It is *curated* — interactive elements and the accessibility
tree rather than raw DOM — for signal, not for budget: burying twenty real controls in 200 KB of
inline SVG and framework hashes degrades the agent rather than helping it.

The agent drives using `drive_value` for each declared parameter.

### 6.3 Distill — transcript to clean workflow

**Parameter binding is a string match, not an inference.** The plan declared `drive_value`, so
distill scans the transcript for the step whose value equals it and rewrites that value to
`{"param": "<name>"}` — the shape `_resolve_value` already expects. The scan covers both `fill`
step values **and** `goto` URLs (§4.1).

**Dead-end pruning.** Hand the numbered transcript to the LLM and ask for the minimal load-bearing
subset. This is unreliable on its own, which is acceptable: verify is the check, and repair can
re-add steps. Leave-one-out replay trials are not worth their cost.

**Extraction.** `mark_target` elements feed the existing pick → wizard path
([`selector_compiler.py`](../../../backend/app/recorder/selector_compiler.py) +
[`llm/authoring.py`](../../../backend/app/llm/authoring.py)). The plan's declared field names are
the targets, so the model maps to a declared schema instead of inventing names.

Output: an ordinary `Workflow` at `status = draft`.

### 6.4 Verify

Replay the distilled workflow headlessly using `verify_value` — a value the agent never drove with.
Four checks:

1. Replay completes without error.
2. Every declared field is present in the output.
3. For a list extraction, the output holds **at least one row**. (A stricter minimum is not
   imposed: a legitimate search can return a single result, and a zero-row result is
   indistinguishable from a broken extraction.)
4. **The output differs from the drive-time output.**

Check 4 is the one that justifies the pipeline. The most common autonomous-authoring failure is not
a crash — it is a workflow that *ignores its parameter*, because the agent navigated to a literal
result URL instead of driving the search. Checks 1–3 all pass. The API returns the same products
regardless of input. Only a differential check catches it, and without it the system would publish
broken APIs that look healthy.

Passing verify promotes the workflow to `ready`, and it proceeds through the existing publish path
unchanged.

### 6.5 Repair

On any failed check, feed back: which check failed, the failure screenshot and HTML dump that
[`replay.py`](../../../backend/app/recorder/replay.py) already writes to
`data/failures/{execution_id}/`, and the transcript.

Repair re-drives with a **different strategy** — category navigation instead of the search box, a
different extraction root, a different entry point — rather than retrying the same route.

Bounded at **3 attempts** and a **~10 minute wall clock**, comfortably inside the recorder's
existing 30-minute hard session ceiling. Exhaustion ends the run as `failed`, retaining the reason
and artifacts.

---

## 7. Data model

A new `AgentRun`, rather than overloading `Workflow`: it must outlive the workflow it produces
(failed runs have none) and it is the natural key for the wallet debit and its refund.

```
AgentRun
  id, user_id, workflow_id (nullable)
  prompt, resolved_url
  status, attempt
  plan (JSONB)          -- declared params (incl. drive/verify values) + output fields
  transcript (JSONB)
  failure_reason, token_usage, wallet_txn_id
```

`status`: `planning → awaiting_confirm → driving → distilling → verifying → (repairing → driving)* → succeeded | failed`

The workflow produced is indistinguishable downstream from a hand-recorded one. One nullable
`Workflow.agent_run_id` is added purely so the UI can badge an API as AI-made.

---

## 8. Frontend

A new page, separate from the recorder route:

1. Prompt box.
2. URL confirmation card — the single interruption in an otherwise autonomous flow.
3. Live progress: current phase, the plan rendered as soon as it exists, the live step list
   (reusing the existing recorder panel component), and verify results per check.
4. **Success** → hands off to the existing publish flow.
5. **Failure** → reason, artifacts, and a *"record it manually instead"* button that opens the
   existing recorder with the URL prefilled.

Styling per [DESIGN.md](../../DESIGN.md) (Warm Editorial). AI-authored APIs are badged as such
wherever they appear in listings.

---

## 9. Cost and quota

- **Tier gate:** Pro/Max only, following the FR-S1 pattern.
- **Debit:** atomic wallet debit at enqueue, via the same path as per-call metering (FR-M4). Gate
  and debit are one operation.
- **Refund:** full refund when a run ends `failed`. Non-optional — a user whose agent gave up
  received nothing. Direct analogue of FR-M4's refund-on-failed-replay.
- **Plan settings:** `agent_run_price` and `agent_runs_per_day` join the runtime-editable settings
  (FR-M1).
- **Calibration:** `AgentRun.token_usage` records real spend per run so the price can be set from
  data rather than guessed.
- Super admins bypass via the existing explicit `is_super` branch, never by faking a tier (FR-M8).

---

## 10. Error handling

| Condition | Behavior |
|---|---|
| URL resolution fails | Run ends `failed` before any browser opens; refunded |
| User never confirms the URL | Run expires on the idle timeout; refunded |
| Login wall encountered | `give_up` with reason `auth_required`; refunded (out of scope, v1) |
| Agent exhausts 3 attempts | `failed` with last failure reason + artifacts; refunded |
| Wall clock exceeded | `failed` with reason `timeout`; refunded |
| LLM unavailable | Run fails cleanly and refunds. Consistent with the "LLM never blocks" guardrail: the *manual recorder remains fully usable*, which is the fallback this feature's failure degrades to |
| Worker dies mid-run | Run surfaces as dead in the panel via the existing heartbeat/watchdog; refunded by the periodic reconciler |
| Site blocks headless | `failed` with the failure screenshot; `browser_settings` allows a headful retry |

The existing guardrail that **the LLM must never block authoring** is honored in its meaningful
form: this feature is entirely LLM-dependent by nature, so with the LLM down the feature is
unavailable — but the manual recorder, publishing, and every existing path are unaffected.

---

## 11. Testing

Constrained by the standing NFR: **no external-network tests.**

- **Fixture site** grows a search form and a results grid — the product's central motif — so the
  full pipeline runs offline.
- **Mocked LLM** with canned tool-call sequences; assert distill produces the expected
  `{steps, parameters, extraction}`.
- **The critical test:** a fixture scenario where the agent navigates to a hardcoded result URL
  instead of driving the search. Assert **verify check 4 catches it** and the run does not reach
  `ready`. This is the single most important test in the feature.
- **URL templating:** a `goto` with a templated query param resolves correctly at replay and coerces
  by declared type (the existing 422 path).
- **Redaction:** a transcript containing a password-like literal never reaches any prompt, on the
  distill and repair paths as well as plan.
- **Refund:** a run that ends `failed` restores the wallet balance exactly; a `succeeded` run does
  not. Ledger legs sum, per the existing wallet-integrity assertions.
- **Concurrency:** an agent run holding the recording slot can still obtain a replay slot for
  verify (no deadlock).
- **Opt-in integration test** against `waltonbd.com`, matching the existing opt-in LLM test pattern.
- `uv run ruff check app` and `uv run pytest` clean.

---

## 12. Open risks

| Risk | Mitigation |
|---|---|
| ~~`fill` may not be captured by the injected recorder~~ | **Retired** — verified 2026-08-13, all action types captured with selectors (§2) |
| Agent-issued actions can be recorded out of order (`fill`'s 400 ms debounce vs `click`'s synchronous emit) | The action loop settles above the debounce between tool calls; a regression test asserts ordering and documents the inverted case |
| Gemini's OpenAI-compat layer may not support tool calling or image parts as needed | Verify in the first implementation phase, before anything depends on it |
| Autonomous success rate on unfamiliar sites will be well below the manual recorder's >80 % publish rate | Verify-repair loop; honest failure surface; manual fallback one click away |
| Agent-driven browsing is more bot-detectable than a human | Retained stealth setup; reference target chosen for low protection; unchanged non-goal |
| Prompt injection — the agent reads page content, which is attacker-controlled | New exposure with no equivalent in the manual recorder. Agent output is constrained to the fixed tool set and never executes page-supplied instructions; worth a dedicated review before shipping |
| An agent wandering an arbitrary user-named site is broader ToS exposure than a human recording their own session | Existing disclaimer; admin can deactivate any API; responsibility rests with the user (PRD §10) |

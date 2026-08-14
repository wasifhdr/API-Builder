# Agent Authoring Correctness — Design

**Date:** 2026-08-15 · **Status:** design approved, not yet planned · **Owner:** Wasif Haider

**Prereq reading:** [2026-08-13-autonomous-api-authoring-design.md](2026-08-13-autonomous-api-authoring-design.md)
(the feature this corrects) · [BLUEPRINT.md](../../BLUEPRINT.md) §4.5 (steps), §4.6 (extraction) ·
[DESIGN.md](../../DESIGN.md) for the confirmation-card UI.

---

## 1. Problem

An agent run against `waltonbd.com` ("make me an API to search for products") produced a workflow
that was marked `READY`, published, and returns this for `query="water heater"`:

```json
[ { "title": null,           "price": null, "url": null, "image_url": null },
  { "title": "Search",       "price": null, "url": null, "image_url": null },
  { "title": "WIWH-GSN-45A", "price": null, "url": null, "image_url": null } ]
```

The authored steps were:

```
0  goto https://www.waltonbd.com/
1  fill  #searchs = {query}
2  press Enter on #searchs
3  click a:has-text("WNR-6D6-GDFS-DI")      <-- drilled into one product
4  extract
```

**The run reported success.** That is the defect being fixed. A wrong answer that announces itself
as wrong is a bad run; a wrong answer that ships is a broken product.

### 1.1 Root causes

Four independent defects had to line up. Each is fixed separately.

**RC1 — nothing constrains where the data lives.** The plan declares *what* fields to return but
never *at what cardinality*. [`DRIVE_SYSTEM`](../../../backend/app/agent/driver.py) forbids only
"navigate straight to a guessed result URL"; the agent obeyed that exactly and instead *clicked*
its way into a product page. Searching, then opening a result, is the model's natural reading of
"find the product".

**RC2 — the drill-in step is anchored to drive-time content.** `a:has-text("WNR-6D6-GDFS-DI")`
embeds text produced by the *drive* parameter value. It can never be correct for another value.

**RC3 — replay substitutes silently.** [`_locate`](../../../backend/app/recorder/replay.py) tries
ranked candidates in order. The `:has-text` candidate misses under a different query, so it falls
through to the anchored `cssPath` candidate, which **does** match — a different link on the new
results page. Replay clicks the wrong element and continues without error. Forgiving fallback is
correct behaviour for production replay; it is wrong as the basis for *verification*.

**RC4 — verify accepts all-null rows.** [`fields_present`](../../../backend/app/agent/verify.py)
tests `n in row` — *key* presence. Every row dict always carries every declared key, so a row of
pure nulls passes. `has_rows` passed (3 rows); `differs_from_drive` passed (garbage ≠ drive data).
`_extract_compiled`'s LLM self-heal floor then invented `"title": "Search"`.

RC4 is the one that let the run ship. Fixing it alone converts this from "published a broken API"
into "attempt 1 failed, retrying".

### 1.2 Second problem — the confirmation card is a dead end

The plan phase picks a start URL from model knowledge alone, with no search and no verification, so
it is sometimes wrong. [`ConfirmUrlIn`](../../../backend/app/schemas/agent.py) carries only `ok`,
so there is no channel for a corrected URL, and Cancel routes to `_finish(succeeded=False)` →
status `failed`. The user is refunded ([`finish_run`](../../../backend/app/services/agent_runs.py)
credits back every non-success) but the run is dead and the prompt must be retyped.

---

## 2. Design principles for this change

The first draft of this design keyed everything off a "submission boundary" — the last step that
consumed a parameter — and forbade interaction past it. That was over-fitted to search-shaped
workflows and was rejected. It breaks on:

- **parameterless APIs** ("today's BBC headlines") — no boundary exists at all;
- **navigation-driven listings** ("laptops under Electronics on Daraz") — reaching the listing *is*
  a sequence of clicks, every one of which the rule would reject;
- **legitimate post-results interaction** — cookie banners, "Load more", pagination, sort toggles;
- **filters applied after results appear** — a parameter consumed past its own boundary.

The replacement holds to two invariants that make no assumption about the *shape* of the workflow.

> **Invariant A — parameter stability.** No step may be anchored to text or href that varies with
> the parameter value.
>
> **Invariant B — right altitude.** The extraction must come from the level of the page that
> matches the declared output cardinality.

Invariant A is decidable empirically and is enforced as a hard gate. Invariant B is not decidable
by any static rule available to us; it is *steered* by the prompt and made **loud** by verification
rather than prevented. This asymmetry is deliberate and is stated as a limitation in §8, not
papered over.

---

## 3. Section 1 — the plan declares output cardinality

`PLAN_SCHEMA` gains a required field:

```
result_shape: "list" | "detail"
```

`PLAN_SYSTEM` gains the rule: **`list`** when the request describes a search, browse, or listing
("search for products", "list flights", "top headlines"); **`detail`** when it names one record's
attributes ("the specs of product X", "today's USD-BDT rate").

`build_plan` validates it and falls back to `"list"` for a missing or unrecognised value rather
than raising — an unparseable cardinality is not worth failing a run over, and §5/§6 catch the
consequences if the fallback is wrong.

`result_shape` is surfaced on the confirmation card (§7) so the user sees the agent's
interpretation before any money is spent.

This is about cardinality, not about searching: it applies equally to a parameterless listing and
to a single-record lookup.

---

## 4. Section 2 — the drive brief states the shape

[`_task_brief`](../../../backend/app/agent/driver.py) gains one line per shape:

- **list** — "The data lives on the page your interaction produces. Mark a representative result
  row *there*. Do NOT open an individual result — an API built from one record's page returns that
  record forever."
- **detail** — "Reach the single record the request describes, then mark its fields."

`DRIVE_SYSTEM` gains the matching rule alongside the existing "do not guess a result URL" rule.

**Separately:** the repair hint currently lands inside the task statement —
[`runner.py`](../../../backend/app/agent/runner.py) appends it to `plan["summary"]`, so on attempt 2
the whole redacted transcript of the failed attempt is rendered as the `Task:` line. It moves to
its own labelled section of the brief. The model should read a failure report as a failure report,
not as a goal.

---

## 5. Section 3 — Invariant A, enforced by strict verification

**Not** a static selector sanitizer. Static analysis has to *guess* which literals are
content-derived; verification can simply *observe* it, because verify already replays the whole
workflow with `verify_value` — a value the agent never drove with.

**Replay change** ([`replay.py`](../../../backend/app/recorder/replay.py)):

- `_locate` reports *which* candidate matched, not just the locator.
- `replay_workflow` gains `record_fallbacks: bool = False`. When set, every case where a
  higher-ranked candidate missed and a lower-ranked one matched is appended to a list returned as
  `selector_fallbacks: [{step_index, skipped, used}]` in the result dict.
- Production replay behaviour is **unchanged**: same forgiving fallback, same tolerance for
  selector drift. Only the reporting is new, and only verify asks for it.

**Verify change** ([`verify.py`](../../../backend/app/agent/verify.py)): a new check
`stable_selectors`. It fails when a recorded fallback **skipped a content-anchored candidate** —
one containing `:has-text(` or `[href=`.

> **Revised during planning (2026-08-15).** An earlier draft exempted candidates whose literal was
> one of the plan's parameter values. That exemption was wrong: replay never templates a *selector*,
> only step values and goto URLs, so a selector anchored to the drive value is exactly the bug class
> this check exists to catch. No exemption.

The narrowing to content-anchored candidates is a false-positive filter, not the detector: a
timing flake on an id- or class-based candidate is common and harmless, and must not fail a run.
The detector is the empirical fallback observation itself.

This catches the `#product-4521 > a` case that a static text-stripping rule would miss, fires
nothing on parameterless workflows (no parameter ⇒ nothing can vary with it), and is indifferent
to whether the workflow searched, browsed, or filtered.

**Repair hint** for this check names the offending step and its selector, so attempt 2 receives
"step 3 was anchored to drive-time page content" rather than a generic retry instruction.

**Cost accepted:** this catches the bad step one verify replay later than a distill-time sanitizer
would. That is the price of not shipping a rule that misfires on category browsing.

---

## 6. Section 4 — verification stops accepting nulls

**`fields_present`** changes from key presence to **value** presence: every declared field must be
non-null in at least one row. On the Walton run this flips `price`, `url`, and `image_url` to
failed, and the workflow never reaches `READY`.

Deliberately **not** added: a per-row fill-rate threshold. Legitimately sparse fields — a discount
price present on 3 of 20 rows — are normal and must not fail a run.

**New drive-time check.** `RecordingSession._capture_final_extraction` already populates
`session.final_sample` at save time. The runner applies the same non-null rule to it *before*
distilling, so a broken extraction fails the attempt without spending a verify replay. Failure
feeds the existing repair loop with the `has_rows` / `fields_present` strategy hints.

Both checks are shape-agnostic and origin-agnostic — they would catch an equivalently broken
hand-recorded workflow.

**Invariant B's coverage, stated plainly:** for `result_shape: "list"`, extraction is expected to
resolve to `mode == "list"` with ≥2 rows. That is a weak structural signal — a "related products"
carousel on a detail page satisfies it. The real protection is that such a carousel will not carry
the declared fields, so `fields_present` fails. Detection, not prevention. See §8.

---

## 7. Section 5 — editable URL, and cancel that means cancel

**Contract:**

- `ConfirmUrlIn` gains `url: str | None`.
- The API route validates any supplied URL — `http`/`https` scheme, non-empty netloc,
  `urlparse`-able, length ≤ 2048 — and returns **400** with a message on failure, so the card can
  show the error inline without a Redis round trip.
- `await_url_confirmation` returns a `UrlDecision(confirmed: bool, url: str | None)` instead of a
  bare bool, and re-validates defensively (the Redis command channel is not a trusted input).
- A confirmed URL replaces `plan["url"]`, the workflow's `start_url`, and `run.resolved_url`.

**Cancel** gets its own terminal state: `AgentRunStatus.CANCELLED`. `enum_column` uses
`native_enum=False`, and SQLAlchemy 2.0 defaults `create_constraint=False`, so the column is a
plain `VARCHAR(32)` — **no migration required**.

`agent_runs` refactors the idempotent refund out of `finish_run` into a shared `_refund(run, db)`,
used by both `finish_run` and a new `cancel_run(run, db)`. The early-return guard in `finish_run`
extends to include `CANCELLED` so a cancelled run can never be re-terminated as failed.

**Frontend** ([`AgentBuilder.tsx`](../../../frontend/src/pages/AgentBuilder.tsx)): the resolved URL
becomes a pre-filled text input; **Confirm** submits whatever it contains; **Cancel** cancels the
run. `STATUS_LABEL` / `STATUS_BADGE` gain a `cancelled` entry with distinct copy — "Cancelled",
not "Couldn't finish this one", and without the "record it manually instead" prompt, which is noise
when the user chose to stop. `AgentRunStatus` in
[`agentTypes.ts`](../../../frontend/src/lib/agentTypes.ts) and `TERMINAL_STATES` in
[`useAgentRun.ts`](../../../frontend/src/hooks/useAgentRun.ts) both gain `cancelled`.

The existing 300-second confirmation timeout is unchanged and resolves to cancelled, not failed.

---

## 8. Limitations, stated

1. **Invariant B is detection, not prevention.** Nothing structurally stops the agent from marking
   a carousel on a detail page. §6 makes it fail loudly; it does not make it impossible.
2. **The repair loop can still exhaust its three attempts** on a site the agent genuinely cannot
   drive. That is correct behaviour — the run fails, the user is refunded, the manual recorder is
   offered.
3. **`stable_selectors` has a residual false-positive risk**: a timing flake on a *content-anchored*
   candidate for a genuinely static control (`button:has-text("Search")` failing to attach in time)
   would fail a run that was fine. Judged rare, and the cost is one retry rather than a wrong API.
4. **The planner still picks the start URL from model knowledge alone** — no search, no
   verification. §7 makes that correctable by hand; it does not make it accurate.

---

## 9. Out of scope

- An explicit `click_result(n)` ordinal-drill-in tool. Considered and deferred: it only protects the
  path the agent deliberately takes, whereas §5 protects every path. Revisit if `detail`-shape runs
  are observed thrashing the repair loop.
- A distill-time static selector sanitizer. Superseded by §5.
- Stamping a page URL onto every recorded step. It was load-bearing only for the rejected boundary
  rule; the §5 violation message identifies a step by index and selector, which is sufficient.
- Any change to production replay semantics, the manual recorder's UX, publishing, metering, or
  OpenAPI generation.

---

## 10. Testing

Unit, no browser required, against fixture step-lists and replay results:

- `fields_present` fails on all-null rows; passes on sparse-but-real rows (the discount-price case).
- The drive-time sample check fails an attempt before distilling.
- `stable_selectors` fails on a skipped `:has-text` candidate and on a skipped `[href=]` candidate;
  passes when the skipped candidate is a class selector (flake filter).
- `build_plan` falls back to `list` on a missing or garbage `result_shape`.
- The confirm route accepts an edited URL, rejects a `javascript:` URL with 400, and rejects a
  non-owner.
- `cancel_run` sets `CANCELLED`, refunds exactly once, and is idempotent against a later
  `finish_run`.

Integration: the existing opt-in `waltonbd.com` end-to-end test from commit `4266959` is the
acceptance check — the same prompt must now either produce a working list API or fail visibly,
never publish nulls.

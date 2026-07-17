# In-app API Tester — Design

**Date:** 2026-07-17
**Status:** Approved for planning

## Problem

Testing a published API is the central point of the product, but there is no
first-party way to run one from the app. Today a user has only:

- A **static curl snippet** in the "Try it" box on the API detail page
  (`frontend/src/pages/ApiDetail.tsx`) — it hardcodes `ab_...` for the key and
  does not list the API's parameters.
- The **Scalar docs embed** at `/docs/{slug}` — a third-party dark-themed UI
  that requires the OpenAPI spec to be `ready` and the caller to paste a key.

Neither lets a user fill in *this API's* parameters, run it, and see the JSON
response inside the app's own UI.

## Goal

Add a **"Try it" panel** to the API detail page that:

- Builds a form from the API's real parameters.
- Sends a live request to the **real public endpoint** `GET /v1/run/{slug}` —
  the exact path a real consumer hits (auth, param coercion, rate limits,
  billing/quotas, caching, the 200/202 async envelope).
- Handles the async execution path (202 → poll).
- Renders the JSON response with `cached` / duration metadata.

Shown to anyone with access to the API — the owner and grantees.

## Non-goals

- No "owner preview" bypass endpoint. The tester exercises the true public
  path, not a shortcut. (Considered and rejected during brainstorming — the
  user explicitly wanted the real end-to-end path.)
- No changes to `/v1/run` or `/v1/executions`. The tester consumes them
  unchanged, as a real client does.
- No replacement of the Scalar docs page. The curl snippet is kept as a
  copy-paste reference below the new panel.

## Key decisions (from brainstorming)

1. **Test path:** the real public endpoint `GET /v1/run/{slug}`, reached
   same-origin as `/v1/run/{slug}` (Vite already proxies `^/v1(/|$)` →
   `http://localhost:8000`, so no CORS and no hardcoded host).
2. **Key handling is role-conditioned:**
   - **Owner** → one-click "Generate test key".
   - **Grantee / consumer** → paste-your-key field.
3. Parameters are exposed to the frontend via a **dedicated endpoint**, not by
   bloating `CustomApiOut` (which the list endpoint also serializes).

## Backend changes

### New endpoint: `GET /api/apis/{id}/parameters`

- **Response model:** `list[ParameterOut]` where `ParameterOut` mirrors the
  existing frontend `Parameter` shape:
  `name: str`, `type: str`, `required: bool`, `example: str | None`,
  `description: str | None`, `source_step: int | None`.
- **Source:** `api.workflow_snapshot.get("parameters", [])`. Each entry is
  validated/normalized into `ParameterOut` (defaults: `type="string"`,
  `required=True`, the rest `None`).
- **Auth / access:** session-authed; reuses the existing `_get_visible_api`
  helper in `backend/app/api/apis.py` so owner and grantees pass and a
  no-access user gets 404/403 exactly as the other detail sub-resources do.
- **Location:** `backend/app/api/apis.py`, alongside the other
  `/{api_id}/...` routes. `ParameterOut` goes in
  `backend/app/schemas/api.py`.

No other backend changes. The public endpoints are consumed as-is.

## Frontend changes

### New component: `frontend/src/components/TryItPanel.tsx`

Extracted into its own file so `ApiDetail.tsx` does not grow further.

**Props:** `{ apiId: string; slug: string; isOwner: boolean }` — `apiId` for the
parameters fetch, `slug` for the run URL, `isOwner` to select the key flow.

**On mount:** fetch `GET /api/apis/{apiId}/parameters` → build the form.

**Key acquisition (role-conditioned):**

- **Owner:**
  - "Generate test key" button → `POST /api/keys` with label `in-app tester`
    → store the returned full key in `localStorage` under a single reused slot
    (e.g. `apibuilder.testerKey`).
  - Reuse that stored key on subsequent runs (across tabs/reloads) so exactly
    one tester key exists rather than sprawling.
  - If a run returns **401** (key revoked/deleted), clear the slot,
    regenerate once, and retry the run automatically.
- **Grantee (non-owner):**
  - Password-style paste field for a full key (`ab_...`).
  - Remembered in `sessionStorage` for the tab so it need not be re-pasted
    on every run.
  - On 401, show an "invalid or revoked key" message (no auto-regenerate —
    the key is the user's own to manage).

**Parameter form:**

- One input per parameter:
  - `string` → text input
  - `integer` / `number` → number input
  - `boolean` → checkbox
- Prefilled from each parameter's `example` when present.
- Required parameters visually marked.
- Empty **optional** params are omitted from the query string; empty
  **required** params are left to the server to reject (422) so the tester
  surfaces the real contract, but may also be flagged client-side as a hint.

### Data flow (run)

1. Build the query string from filled params →
   `fetch('/v1/run/{slug}?<qs>', { headers: { 'X-API-Key': key } })`.
2. **200** → render `data` + `meta` (cached badge, `duration_ms` chip).
3. **202** `{ execution_id, status_url }` → enter a "running…" state and poll
   `GET /v1/executions/{execution_id}` (same `X-API-Key`) every ~1.2s until it
   returns `data` or a terminal error. Cap total polling at ~30s, then show a
   timeout message that surfaces the `status_url`.
4. **422** → render the per-parameter coercion errors (`detail` is a
   `list[str]`).
5. **401** → owner: regenerate key once and retry; grantee: "invalid/revoked
   key" message.
6. **403 / 429 / 402** → surface the endpoint's own message (no access / rate
   limit exceeded / insufficient wallet balance), including the structured
   fields those responses carry (e.g. `reset_seconds`, `balance_bdt`).

### Result display

- JSON response pretty-printed in the existing `CodeBlock` component.
- A status badge (200 / 202-running / error).
- `meta.cached` and `meta.duration_ms` shown as chips when present.
- The resolved request URL shown for reference.
- Errors render inside the same panel in red (`text-red-deep`).

### ApiDetail.tsx integration

- Replace the current static "Try it" `<section>` body with `<TryItPanel />`.
- Keep the curl snippet as a labeled reference block **below** the panel.

## Styling

Follow `docs/DESIGN.md` ("Warm Editorial") and the existing `components/ui`
primitives — `cardClasses`, `Button`, `Input`, `FieldLabel`, `Badge`,
`CodeBlock`, `StatChip`, `InlineCode`. No new design tokens.

## Testing

- **Backend (pytest):** `GET /apis/{id}/parameters`
  - owner gets the parameter list
  - a grantee with access gets it
  - a no-access user is rejected (matches sibling sub-resource behavior)
  - parameters are correctly extracted/normalized from `workflow_snapshot`
    (including defaults for missing `type`/`required`)
- **Frontend:** manual verification in the browser preview before claiming
  done — generate a test key as owner, fill params, run, confirm the JSON
  response renders; confirm a bad required param shows the 422 error.

## Files touched

- `backend/app/schemas/api.py` — add `ParameterOut`.
- `backend/app/api/apis.py` — add `GET /{api_id}/parameters`.
- `backend/tests/...` — new test for the parameters endpoint.
- `frontend/src/components/TryItPanel.tsx` — new component.
- `frontend/src/pages/ApiDetail.tsx` — swap the "Try it" section to use it.
- `frontend/src/lib/types.ts` — reuse existing `Parameter`; add response
  types for the run/execution envelopes if not already present.

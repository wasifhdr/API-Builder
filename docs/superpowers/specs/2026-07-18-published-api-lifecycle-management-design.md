# Published API Lifecycle Management — Design

**Date:** 2026-07-18
**Status:** Approved (pending spec review)

Lets **API owners** view, re-record, edit, and delete their APIs *after* publishing — not only
while they are drafts — and gives **super admins** a read-only view plus the delete they already
have. Regular users (shared-access consumers) get none of these controls.

This document extends [BLUEPRINT.md](../../BLUEPRINT.md) and follows the constraints in
[MULTI_TENANCY_PLAN.md](../../MULTI_TENANCY_PLAN.md) (roles, super-admin powers, audit logging) and
[DESIGN.md](../../DESIGN.md) (Warm Editorial UI). Where those documents already define behaviour
(cascades, guards, audit helper), reuse it rather than reinventing.

---

## Locked decisions (from brainstorming)

1. **Edit steps = re-record only.** Owners re-record the whole flow (replaces all recorded steps)
   and keep editing name / parameters / extraction. No per-step (selector/value/reorder) editing.
2. **Go-live is explicit.** Edits and re-records are staged on the underlying `Workflow`; the live
   API keeps serving its existing `workflow_snapshot` until the owner clicks **Sync changes to live
   API**. Sync preserves slug, API keys, executions/stats, grants, pricing, cache TTL, and
   `is_active`; it re-triggers OpenAPI spec regeneration.
3. **Delete is total.** Deleting a published API removes the API **and** its underlying workflow
   entirely (cascades executions, grants, invites). Nothing returns to Drafts. *(Changed from the
   earlier "keep workflow as draft" decision.)*
4. **Super admins: admin panel only, View + Delete.** Read-only view of any user's
   steps/params/extraction, plus the existing delete. No editing or re-recording of other users'
   flows — that would run a headful browser under another user's saved logins.
5. **Regular users get none of it.** The existing owner-only guards (`_get_owned_api`,
   `_get_owned_workflow`) already return 404 for shared-access consumers; new endpoints reuse them.

---

## Background: how publishing works today

- **A published API is a frozen snapshot.** `publish_workflow` copies the workflow's
  steps/parameters/extraction/output_schema/browser_settings into
  `custom_apis.workflow_snapshot` (a JSONB column). The live API executes off that snapshot, **not**
  the live `Workflow` row. Editing the workflow after publishing has no effect on the running API
  until something re-copies the snapshot. — `app/services/publish.py`
- **The `Workflow` row survives behind every API.** `custom_apis.workflow_id` is a FK with
  `ON DELETE CASCADE`, so deleting the workflow cascades to the API (and, transitively, its
  executions/grants/invites). This is exactly what `delete_admin_workflow` relies on. —
  `app/api/admin.py`
- **The recorder always creates a *new* workflow.** `POST /recordings` inserts a fresh `Workflow`
  and enqueues a `jobs:rec` job carrying `workflow_id` + `user_id`. There is no "record into an
  existing workflow" path. — `app/api/recordings.py`
- **`CustomApiOut` already exposes `workflow_id`**, so the frontend can navigate API → workflow with
  no schema change. — `app/schemas/api.py`
- **Owner reachability gap.** The dashboard only links to `WorkflowEditor` for *unpublished*
  workflows (`list_workflows` excludes any workflow that has a published API). Once published, the
  workflow is unreachable in the UI, and "Unpublish" merely toggles `is_active`.

## Core model of this feature

> The **workflow is the edit surface**. The **snapshot is what callers hit**. **Sync** is the one
> action that copies workflow → snapshot in place.

Re-record and field edits mutate the `Workflow`. The live API is unaffected until Sync. Delete
removes the whole workflow (and thus the API). This keeps a single edit path and reuses the
existing publish/snapshot machinery.

---

## Backend changes

### `app/services/publish.py`
- Extract `build_snapshot(workflow: Workflow) -> dict` — the snapshot dict currently inline in
  `publish_workflow` (steps, parameters, extraction, output_schema, browser_settings).
- Add `sync_workflow_to_api(api: CustomApi, workflow: Workflow, db) -> None` — sets
  `api.workflow_snapshot = build_snapshot(workflow)`, `api.spec_status = SpecStatus.PENDING`,
  commits, and enqueues the LLM spec job (`jobs:llm`, payload `{"api_id": ...}`) exactly as publish
  does.
- `publish_workflow` reuses `build_snapshot`. **No behaviour change to publishing.**

### `app/api/apis.py`
- **`POST /apis/{api_id}/sync`** → `CustomApiOut`. Owner-only (`_get_owned_api`). Loads the API's
  `Workflow` by `api.workflow_id`; **400** if the workflow is missing or `status != READY`
  (extraction required, same gate as publish); calls `sync_workflow_to_api`; returns the refreshed
  API. Preserves slug/keys/stats/grants/pricing/`cache_ttl`/`is_active`.
- **`DELETE /apis/{api_id}`** → 204. Owner-only (`_get_owned_api`). Resolves `api.workflow_id` and
  issues a core `DELETE` on the **workflow** row (`delete(Workflow).where(Workflow.id == wf_id)`),
  letting the `custom_apis.workflow_id` cascade remove the API and its executions/grants/invites —
  identical mechanism to `delete_admin_workflow`, minus the audit entry (owner action on own data).
  If `workflow_id` is somehow null/missing, fall back to deleting the `CustomApi` directly so the
  endpoint still fully removes the API.

### `app/api/workflows.py`
- **`POST /workflows/{workflow_id}/rerecord`** → 202/200. Owner-only (`_get_owned_workflow`). Sets
  `status = WorkflowStatus.RECORDING` and enqueues a `jobs:rec` job with
  `{workflow_id, user_id, rerecord: true}`. **Consumes no creation quota** (re-recording an existing
  API is not a new creation). **409** if the workflow is already `RECORDING`. The live API is
  untouched — its snapshot changes only on a later Sync.
- **`WorkflowOut`** (`app/schemas/workflow.py`): add `published_api_id: uuid.UUID | None` and
  `published_api_slug: str | None`, resolved from the `custom_apis` row referencing this workflow
  (None when unpublished). This is how the editor and recorder know the workflow is live.

### `app/recorder/session.py` — re-record safety
The recording session already overwrites `workflow.steps/parameters/extraction` on a successful
save and, on cancel/timeout, sets `status = ARCHIVED` without writing steps. For a re-record of a
**published** workflow that archival/downgrade is wrong — it would hide a live API's workflow.
- The session learns it is a re-record from the job payload (`rerecord: true`).
- On **cancel or timeout during a re-record**, the workflow must retain its **previous status**
  (e.g. `READY`) and its previous steps — never `ARCHIVED`, never `DRAFT`. (The previously published
  steps also remain safe in the API snapshot regardless.)
- On **successful save of a re-record**, behaviour is as today: steps/params/extraction overwrite
  the workflow; status resolves to `READY` (has extraction) or `DRAFT`. The live API is still
  unchanged until Sync.

### `app/api/admin.py` — super-admin read-only view
- **`GET /api/admin/workflows/{workflow_id}`** → returns the workflow's `steps`, `parameters`,
  `extraction` (and `name`, `status`) for the read-only viewer. `require_super_admin`. List and
  delete endpoints already exist; only this detail read is new. No audit entry for a read.

### Permissions matrix

| Action | Owner | Super admin | Regular user (shared) |
|---|---|---|---|
| View steps/params/extraction | ✅ inline (WorkflowEditor) | ✅ read-only (admin panel) | ❌ 404 |
| Edit name/params/extraction | ✅ | ❌ | ❌ |
| Re-record | ✅ | ❌ | ❌ |
| Sync to live | ✅ | ❌ | ❌ |
| Delete API (+ workflow) | ✅ (own) | ✅ (admin panel, audited) | ❌ 404 |

Super-admin delete stays on the existing audited admin endpoints; the new owner `DELETE /apis/{id}`
is owner-only and needs no audit.

---

## Frontend changes

All UI reuses existing `components/ui` primitives and the Warm Editorial system — no new colors,
fonts, or spacing.

### `pages/ApiDetail.tsx` (owner view)
- A **Manage** action group (owner-only), near the header:
  - **Edit recording** → `Link` to `/workflows/{customApi.workflow_id}/edit`.
  - **Delete API** → confirm modal ("Delete this API and its recording? This can't be undone.") →
    `DELETE /apis/{id}` → navigate to `/dashboard`.
- "Unpublish" (toggles `is_active`) is unchanged.

### `pages/WorkflowEditor.tsx` — make it published-aware
- Load now includes `published_api_id` / `published_api_slug`.
- **When published:**
  - Banner: "Live as `/v1/run/{slug}`" linking to `/apis/{published_api_id}`.
  - Replace **Publish as API** with **Sync changes to live API** → `POST /apis/{id}/sync`, then a
    success message; on failure show the API error (e.g. "workflow must be ready").
  - Add **Re-record** → `POST /workflows/{id}/rerecord` → navigate to `/recorder/{id}`.
  - Hide the draft **Delete workflow** button (deletion of a live API happens on the API page); the
    banner points there.
- **When not published:** behaves exactly as today (Publish + Delete draft).

### Recorder reuse
Re-record reuses the existing `RecorderSession` page at `/recorder/{workflowId}` — the same route
the dashboard already uses for a `recording`-status workflow. After `rerecord` flips the status and
the user finishes/saves, they return to the editor (still published) to Sync.

### Admin read-only viewer
- From the admin APIs tab and/or the T5 user-detail workflows list, a **View** action opens a
  read-only panel/modal showing steps (via `lib/steps.describeStep`), parameters, and extraction,
  fetched from `GET /api/admin/workflows/{id}`. Delete stays as-is.

---

## Testing

**Backend (`backend/tests/`, `uv run pytest`):**
- `sync`: updates `workflow_snapshot`, sets `spec_status=pending`, preserves slug/keys/grants/stats;
  400 when workflow not `READY`; 404 for non-owner and shared-access user.
- `DELETE /apis/{id}` (owner): removes API + workflow + executions/grants/invites; 404 for
  non-owner/shared user; still fully deletes when `workflow_id` fallback path is hit.
- `rerecord`: flips status to `RECORDING`, enqueues job with `rerecord: true`, consumes **no** quota;
  409 when already recording; 404 for non-owner.
- re-record cancel/timeout does **not** archive or downgrade a published workflow (session-level).
- admin `GET /workflows/{id}`: 200 for super admin, 403 for regular user, 404 for missing.

**Frontend (browser preview):** owner walkthrough edit → sync → re-record → delete; the published
banner and Sync/Re-record buttons appear only when published; admin read-only view renders.

**Regression:** existing publish flow and draft edit/delete/publish unchanged; existing admin
delete + Google login unaffected.

---

## Suggested phasing (for the implementation plan)

- **P1 — Backend delete + sync.** `build_snapshot`/`sync_workflow_to_api` refactor,
  `POST /apis/{id}/sync`, owner `DELETE /apis/{id}`, tests.
- **P2 — Re-record.** `POST /workflows/{id}/rerecord`, `WorkflowOut` published fields, session
  cancel/timeout safety for re-records, tests.
- **P3 — Owner frontend.** ApiDetail Manage group; WorkflowEditor published-aware (banner, Sync,
  Re-record); browser verification.
- **P4 — Admin read-only viewer.** `GET /api/admin/workflows/{id}` + admin-panel view UI.

Each phase leaves the app runnable and is committed independently.

## Out of scope
- Per-step (selector/value/insert/reorder) editing — re-record replaces the whole flow.
- Super admins editing or re-recording other users' flows.
- Preserving the slug/endpoint URL across a delete + fresh re-publish (delete means gone).
- Any new DB migration (all columns already exist; new response fields are derived).

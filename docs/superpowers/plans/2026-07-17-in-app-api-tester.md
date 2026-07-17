# In-app API Tester Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Try it" panel to the API detail page that fills a form from the API's real parameters, calls the real public `GET /v1/run/{slug}` endpoint (handling the async 202→poll path), and renders the JSON response.

**Architecture:** A new session-authed backend endpoint exposes an API's parameter list to the frontend. A new React component (`TryItPanel`) builds the form, acquires an `X-API-Key` (owner: one-click generated key reused via localStorage; grantee: pasted key in sessionStorage), sends a same-origin request to `/v1/run/{slug}` (Vite proxies `/v1` → :8000), polls `/v1/executions/{id}` on a 202, and renders the result. The public endpoints are consumed unchanged.

**Tech Stack:** FastAPI + Pydantic + SQLAlchemy async (backend), React 19 + TypeScript + Tailwind v4 (frontend), pytest (backend tests). Spec: `docs/superpowers/specs/2026-07-17-in-app-api-tester-design.md`.

## Global Constraints

- Backend tests run from `backend/`: `uv run pytest`. Lint: `uv run ruff check app`.
- Frontend typecheck/build from `frontend/`: `npm run build` (`tsc -b && vite build`). Lint: `npm run lint` (oxlint). No frontend unit-test runner exists — frontend tasks verify via `npm run build` + manual browser preview.
- Pydantic schemas use `BaseModel`; response models mirror existing `snake_case` field names.
- Frontend uses the existing `api` client (`frontend/src/lib/api.ts`) for `/api/*` calls and native `fetch` for `/v1/*` calls (the public API needs the `X-API-Key` header, not the session cookie).
- Styling follows `docs/DESIGN.md` ("Warm Editorial") using the existing `components/ui` primitives — no new tokens. Import UI primitives from `../components/ui`.
- The owner's generated test key uses label exactly `in-app tester`.
- The public run URL is same-origin relative: `/v1/run/{slug}` — never hardcode `http://localhost:8000`.

---

## Task 1: Backend — expose an API's parameters

**Files:**
- Modify: `backend/app/schemas/api.py` (add `ParameterOut`)
- Modify: `backend/app/api/apis.py` (add `GET /{api_id}/parameters` + import)
- Test: `backend/tests/test_api_parameters.py` (create)

**Interfaces:**
- Consumes: existing `_get_visible_api(api_id: uuid.UUID, user: User, db: AsyncSession) -> CustomApi` in `apis.py`; `CustomApi.workflow_snapshot` (JSONB dict with a `"parameters"` list).
- Produces: `GET /api/apis/{api_id}/parameters` → `list[ParameterOut]`; handler `apis_api.get_api_parameters(api_id, user, db)`. `ParameterOut` fields: `name: str`, `type: str = "string"`, `required: bool = True`, `example: str | None = None`, `description: str | None = None`, `source_step: int | None = None`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_api_parameters.py`. It reuses the `_make_api` helper pattern from `test_api_stats.py` (inline a local copy — tests are read independently). A grantee test uses a **super-admin owner** so `has_access` passes without depending on subscription-tier `can_share` math.

```python
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api import apis as apis_api
from app.models.api import ApiAccessGrant, ApiVisibility, CustomApi, GrantSource
from app.models.user import UserRole
from app.models.workflow import Workflow

PARAMS = [
    {"name": "city", "type": "string", "required": True, "example": "Dhaka",
     "description": "City name", "source_step": 2},
    {"name": "page", "type": "integer", "required": False},  # missing optional fields
]


async def _make_api(db, owner, *, visibility=ApiVisibility.PRIVATE, parameters=None):
    workflow = Workflow(user_id=owner.id, name="test wf", start_url="https://example.com")
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    api = CustomApi(
        workflow_id=workflow.id,
        owner_id=owner.id,
        slug=f"test-{workflow.id.hex[:8]}",
        name="Test API",
        workflow_snapshot={"steps": [], "parameters": parameters or [], "extraction": {}},
        visibility=visibility,
    )
    db.add(api)
    await db.commit()
    await db.refresh(api)
    return api


async def test_owner_gets_parameter_list(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner, parameters=PARAMS)
    result = await apis_api.get_api_parameters(api_id=api.id, user=owner, db=db)
    assert [p.name for p in result] == ["city", "page"]


async def test_missing_optional_fields_get_defaults(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner, parameters=PARAMS)
    page = next(p for p in await apis_api.get_api_parameters(api_id=api.id, user=owner, db=db)
                if p.name == "page")
    assert page.type == "integer"
    assert page.required is False
    assert page.example is None
    assert page.description is None
    assert page.source_step is None


async def test_api_with_no_parameters_returns_empty(db, make_user):
    owner = await make_user()
    api = await _make_api(db, owner, parameters=[])
    assert await apis_api.get_api_parameters(api_id=api.id, user=owner, db=db) == []


async def test_grantee_can_read_parameters(db, make_user):
    owner = await make_user()
    owner.role = UserRole.SUPER_ADMIN  # super-admin owner always allows sharing
    db.add(owner)
    await db.commit()
    grantee = await make_user()
    api = await _make_api(db, owner, visibility=ApiVisibility.SHARED, parameters=PARAMS)
    db.add(ApiAccessGrant(api_id=api.id, user_id=grantee.id, granted_via=GrantSource.INVITE))
    await db.commit()
    result = await apis_api.get_api_parameters(api_id=api.id, user=grantee, db=db)
    assert [p.name for p in result] == ["city", "page"]


async def test_unrelated_user_gets_404(db, make_user):
    owner = await make_user()
    other = await make_user()
    api = await _make_api(db, owner, parameters=PARAMS)
    with pytest.raises(HTTPException) as exc_info:
        await apis_api.get_api_parameters(api_id=api.id, user=other, db=db)
    assert exc_info.value.status_code == 404


async def test_missing_api_returns_404(db, make_user):
    owner = await make_user()
    with pytest.raises(HTTPException) as exc_info:
        await apis_api.get_api_parameters(api_id=uuid.uuid4(), user=owner, db=db)
    assert exc_info.value.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_api_parameters.py -v`
Expected: FAIL — `AttributeError: module 'app.api.apis' has no attribute 'get_api_parameters'`.

- [ ] **Step 3: Add `ParameterOut` to `backend/app/schemas/api.py`**

Add after the `CustomApiUpdate` class (it needs no imports beyond the existing `BaseModel`):

```python
class ParameterOut(BaseModel):
    name: str
    type: str = "string"
    required: bool = True
    example: str | None = None
    description: str | None = None
    source_step: int | None = None
```

- [ ] **Step 4: Add the endpoint to `backend/app/api/apis.py`**

Add `ParameterOut` to the existing `from app.schemas.api import (...)` block. Then add this route (place it just after `get_api`, near line 74):

```python
@router.get("/{api_id}/parameters", response_model=list[ParameterOut])
async def get_api_parameters(
    api_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ParameterOut]:
    api = await _get_visible_api(api_id, user, db)
    raw = api.workflow_snapshot.get("parameters", [])
    return [ParameterOut(**p) for p in raw]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/test_api_parameters.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Lint**

Run: `cd backend && uv run ruff check app`
Expected: no errors (add the import in alphabetical position within the `app.schemas.api` import block to satisfy import sorting).

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/api.py backend/app/api/apis.py backend/tests/test_api_parameters.py
git commit -m "feat(api): expose API parameters endpoint for the in-app tester"
```

---

## Task 2: Frontend — `TryItPanel` scaffold (params form + key acquisition), mounted in ApiDetail

This task gets the panel on screen with a working parameter form and key handling. The Run button is present but wired in Task 3.

**Files:**
- Create: `frontend/src/components/TryItPanel.tsx`
- Modify: `frontend/src/pages/ApiDetail.tsx` (replace the "Try it" section body; keep the curl block below)
- Modify: `frontend/src/lib/types.ts` (add run/execution envelope types — used in Task 3, added here so the module is complete)

**Interfaces:**
- Consumes: `GET /api/apis/{apiId}/parameters` → `Parameter[]` (existing `Parameter` type in `types.ts`); `POST /api/keys` (body `{ label: string }`) → `{ api_key: string; key_prefix: string; ... }`; `api` client from `../lib/api`.
- Produces: `export default function TryItPanel({ apiId, slug, isOwner }: { apiId: string; slug: string; isOwner: boolean })`.

- [ ] **Step 1: Add response envelope types to `frontend/src/lib/types.ts`**

Append (the `Parameter` interface already exists — do not redefine it):

```typescript
export interface RunSuccess {
  data: unknown
  meta: { cached: boolean; duration_ms?: number; execution_id?: string }
}

export interface RunAccepted {
  execution_id: string
  status_url: string
}

export interface ExecutionPending {
  execution_id: string
  status: 'queued' | 'running'
}
```

- [ ] **Step 2: Create `frontend/src/components/TryItPanel.tsx` (scaffold)**

Renders the key-acquisition UI (role-conditioned) and a form built from the fetched parameters. `runReal` is stubbed with a `// wired in Task 3` comment so the file typechecks and the Run button is visible but inert.

```tsx
import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, CodeBlock, Checkbox, FieldHelp, FieldLabel, Input, cardClasses } from './ui'
import { ApiError, api } from '../lib/api'
import type { Parameter } from '../lib/types'

const OWNER_KEY_SLOT = 'apibuilder.testerKey'
const GRANTEE_KEY_SLOT = 'apibuilder.granteeTesterKey'

interface ApiKeyCreated {
  api_key: string
  key_prefix: string
}

export default function TryItPanel({
  apiId,
  slug,
  isOwner,
}: {
  apiId: string
  slug: string
  isOwner: boolean
}) {
  const [params, setParams] = useState<Parameter[]>([])
  const [values, setValues] = useState<Record<string, string>>({})
  const [ownerKey, setOwnerKey] = useState<string | null>(() => localStorage.getItem(OWNER_KEY_SLOT))
  const [granteeKey, setGranteeKey] = useState<string>(() => sessionStorage.getItem(GRANTEE_KEY_SLOT) ?? '')
  const [error, setError] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)

  useEffect(() => {
    api
      .get<Parameter[]>(`/apis/${apiId}/parameters`)
      .then((ps) => {
        setParams(ps)
        setValues(Object.fromEntries(ps.map((p) => [p.name, p.example ?? ''])))
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load parameters'))
  }, [apiId])

  const activeKey = isOwner ? ownerKey : granteeKey.trim() || null

  async function generateOwnerKey(): Promise<string> {
    setGenerating(true)
    setError(null)
    try {
      const created = await api.post<ApiKeyCreated>('/keys', { label: 'in-app tester' })
      localStorage.setItem(OWNER_KEY_SLOT, created.api_key)
      setOwnerKey(created.api_key)
      return created.api_key
    } finally {
      setGenerating(false)
    }
  }

  function setValue(name: string, value: string) {
    setValues((prev) => ({ ...prev, [name]: value }))
  }

  async function runReal() {
    // wired in Task 3
  }

  const canRun = useMemo(() => activeKey !== null, [activeKey])

  return (
    <section className={`${cardClasses({ variant: 'quiet' })} space-y-4`}>
      <div className="flex items-center justify-between">
        <h2 className="text-h2">Try it</h2>
        <Badge variant="neutral">/v1/run/{slug}</Badge>
      </div>

      {error && <p className="text-sm font-medium text-red-deep">{error}</p>}

      {/* Key acquisition */}
      {isOwner ? (
        <div className="flex flex-wrap items-center gap-2">
          {ownerKey ? (
            <span className="text-sm text-ink/70">
              Test key ready (<code className="font-mono">{ownerKey.slice(0, 8)}…</code>)
            </span>
          ) : (
            <Button size="sm" onClick={() => generateOwnerKey().catch((e) =>
              setError(e instanceof ApiError ? e.message : 'Failed to generate key'))} disabled={generating}>
              {generating ? 'Generating…' : 'Generate test key'}
            </Button>
          )}
        </div>
      ) : (
        <div>
          <FieldLabel htmlFor="tester-key">Your API key</FieldLabel>
          <Input
            id="tester-key"
            type="password"
            placeholder="ab_…"
            value={granteeKey}
            onChange={(e) => {
              setGranteeKey(e.target.value)
              sessionStorage.setItem(GRANTEE_KEY_SLOT, e.target.value)
            }}
            className="max-w-md"
          />
          <FieldHelp>Create one on the Keys page. Remembered for this browser tab only.</FieldHelp>
        </div>
      )}

      {/* Parameter form */}
      {params.length === 0 ? (
        <p className="text-sm text-ink/60">This API takes no parameters.</p>
      ) : (
        <div className="space-y-3">
          {params.map((p) => (
            <div key={p.name}>
              <FieldLabel htmlFor={`param-${p.name}`}>
                {p.name}
                {p.required && <span className="text-red-deep"> *</span>}
                <span className="ml-2 normal-case text-ink/45">{p.type}</span>
              </FieldLabel>
              {p.type === 'boolean' ? (
                <label className="flex items-center gap-2 text-sm">
                  <Checkbox
                    checked={values[p.name] === 'true'}
                    onChange={(e) => setValue(p.name, e.target.checked ? 'true' : 'false')}
                  />
                  {p.description ?? 'true / false'}
                </label>
              ) : (
                <Input
                  id={`param-${p.name}`}
                  type={p.type === 'integer' || p.type === 'number' ? 'number' : 'text'}
                  value={values[p.name] ?? ''}
                  onChange={(e) => setValue(p.name, e.target.value)}
                  className="max-w-md"
                />
              )}
              {p.description && p.type !== 'boolean' && <FieldHelp>{p.description}</FieldHelp>}
            </div>
          ))}
        </div>
      )}

      <Button onClick={runReal} disabled={!canRun}>
        Run
      </Button>
      {!canRun && (
        <FieldHelp>{isOwner ? 'Generate a test key to run.' : 'Paste your API key to run.'}</FieldHelp>
      )}
    </section>
  )
}
```

- [ ] **Step 3: Mount it in `frontend/src/pages/ApiDetail.tsx`**

Add the import near the other component imports:

```tsx
import TryItPanel from '../components/TryItPanel'
```

Replace the existing "Try it" section (currently lines ~287-290):

```tsx
      <section className="mb-8">
        <h2 className="text-h2 mb-2">Try it</h2>
        <CodeBlock lang="bash" code={curlExample} />
      </section>
```

with:

```tsx
      <section className="mb-8 space-y-4">
        <TryItPanel apiId={customApi.id} slug={customApi.slug} isOwner={isOwner} />
        <div>
          <CapsLabel tone="muted" className="mb-2">Or with curl</CapsLabel>
          <CodeBlock lang="bash" code={curlExample} />
        </div>
      </section>
```

(`CapsLabel` and `CodeBlock` are already imported in ApiDetail.tsx.)

- [ ] **Step 4: Typecheck + lint**

Run: `cd frontend && npm run build`
Expected: build succeeds, no TS errors.
Run: `cd frontend && npm run lint`
Expected: no errors.

- [ ] **Step 5: Manual browser verification**

Start the app (`scripts\dev.ps1`) or the Browser-pane dev server, open an API you own at `/apis/{id}`. Verify: the "Try it" panel renders with a field per parameter (prefilled from examples), the "Generate test key" button appears; clicking it flips to "Test key ready (ab_…)". The curl block still shows below under "Or with curl". The Run button is enabled once a key exists (it does nothing yet — expected).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/TryItPanel.tsx frontend/src/pages/ApiDetail.tsx frontend/src/lib/types.ts
git commit -m "feat(ui): TryItPanel scaffold with param form and key acquisition"
```

---

## Task 3: Frontend — wire the run, async poll, result & error handling

**Files:**
- Modify: `frontend/src/components/TryItPanel.tsx`

**Interfaces:**
- Consumes: `RunSuccess`, `RunAccepted`, `ExecutionPending` types (Task 2); public endpoints `GET /v1/run/{slug}` and `GET /v1/executions/{execution_id}`, both taking header `X-API-Key`.
- Produces: a functioning `runReal()` that sets result/error/running state and renders output.

- [ ] **Step 1: Add result state + run/poll logic to `TryItPanel.tsx`**

Add these imports/types at the top (extend the existing `types` import):

```tsx
import type { Parameter, RunAccepted, RunSuccess } from '../lib/types'
```

Add state near the other `useState` hooks:

```tsx
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<RunSuccess | null>(null)
  const [status, setStatus] = useState<string | null>(null)
```

Add these helpers inside the component (above `return`). `buildQuery` omits empty **optional** params; required params are sent even if empty so the server returns its real 422.

```tsx
  function buildQuery(): string {
    const qs = new URLSearchParams()
    for (const p of params) {
      const v = values[p.name] ?? ''
      if (v === '' && !p.required) continue
      qs.set(p.name, v)
    }
    return qs.toString()
  }

  async function callRun(key: string, qs: string): Promise<Response> {
    return fetch(`/v1/run/${slug}${qs ? `?${qs}` : ''}`, { headers: { 'X-API-Key': key } })
  }

  async function pollExecution(key: string, executionId: string): Promise<void> {
    const deadline = Date.now() + 30_000
    while (Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 1200))
      const res = await fetch(`/v1/executions/${executionId}`, { headers: { 'X-API-Key': key } })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError(typeof body.detail === 'string' ? body.detail : 'Execution failed')
        return
      }
      if (body.status === 'queued' || body.status === 'running') {
        setStatus(`Running… (${body.status})`)
        continue
      }
      setResult(body as RunSuccess) // terminal: has data + meta
      setStatus(null)
      return
    }
    setError('Timed out waiting for the execution to finish.')
  }
```

- [ ] **Step 2: Implement `runReal()`**

Replace the stub. Handles 200 (result), 202 (poll), 422 (coercion errors list), 401 (owner regenerate-once, grantee message), and other errors. Uses the existing `generateOwnerKey()` and `activeKey`.

```tsx
  async function runReal(retryOn401 = true) {
    const key = activeKey
    if (!key) return
    setRunning(true)
    setError(null)
    setResult(null)
    setStatus(null)
    const qs = buildQuery()
    try {
      const res = await callRun(key, qs)
      const body = await res.json().catch(() => ({}))

      if (res.ok && 'data' in body) {
        setResult(body as RunSuccess)
      } else if (res.status === 202) {
        const accepted = body as RunAccepted
        setStatus('Running…')
        await pollExecution(key, accepted.execution_id)
      } else if (res.status === 422) {
        const detail = body.detail
        setError(Array.isArray(detail) ? detail.join('; ') : 'Invalid parameters.')
      } else if (res.status === 401) {
        if (isOwner && retryOn401) {
          localStorage.removeItem(OWNER_KEY_SLOT)
          setOwnerKey(null)
          const fresh = await generateOwnerKey()
          setRunning(false)
          // retry once with the fresh key
          const res2 = await callRun(fresh, qs)
          const body2 = await res2.json().catch(() => ({}))
          if (res2.ok && 'data' in body2) setResult(body2 as RunSuccess)
          else if (res2.status === 202) { setStatus('Running…'); await pollExecution(fresh, (body2 as RunAccepted).execution_id) }
          else setError(typeof body2.detail === 'string' ? body2.detail : 'Request failed.')
        } else {
          setError('Invalid or revoked API key.')
        }
      } else {
        const d = body.detail
        setError(typeof d === 'string' ? d : d?.detail ?? `Request failed (${res.status}).`)
      }
    } catch {
      setError('Network error calling the API.')
    } finally {
      setRunning(false)
    }
  }
```

Update the Run button to reflect running state and the default-arg signature:

```tsx
      <Button onClick={() => runReal()} disabled={!canRun || running}>
        {running ? 'Running…' : 'Run'}
      </Button>
```

- [ ] **Step 3: Render result / status below the Run button**

Add before the closing `</section>`:

```tsx
      {status && <p className="text-sm font-medium text-ink/70">{status}</p>}

      {result && (
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="success">200 OK</Badge>
            {result.meta?.cached && <Badge variant="info">cached</Badge>}
            {result.meta?.duration_ms != null && (
              <Badge variant="neutral">{Math.round(result.meta.duration_ms)}ms</Badge>
            )}
          </div>
          <CodeBlock lang="json" code={JSON.stringify(result.data, null, 2)} />
        </div>
      )}
```

- [ ] **Step 4: Typecheck + lint**

Run: `cd frontend && npm run build`
Expected: build succeeds.
Run: `cd frontend && npm run lint`
Expected: no errors.

- [ ] **Step 5: Manual end-to-end browser verification**

With `docker compose up -d`, the worker running (`python -m app.workers.main`), and the dev servers up: open an API you own, generate a test key, fill the parameters, click **Run**. Verify a `200 OK` badge and pretty-printed JSON appear (or a "Running…" state that resolves for slow replays). Then clear a required field and Run again → confirm the 422 error text shows. If a run is slow enough to 202, confirm the poll resolves to a result.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/TryItPanel.tsx
git commit -m "feat(ui): wire TryItPanel run, async poll, result and error handling"
```

---

## Self-Review

**Spec coverage:**
- Real public `/v1/run/{slug}` path, same-origin → Task 3 (`callRun`). ✓
- Role-conditioned key handling (owner one-click localStorage + 401 regenerate; grantee paste sessionStorage) → Task 2 (UI) + Task 3 (401 logic). ✓
- Parameters exposed via dedicated endpoint (not `CustomApiOut`) → Task 1. ✓
- Param form by type with example prefill; empty optional params omitted → Task 2 (form) + Task 3 (`buildQuery`). ✓
- 200 / 202-poll / 422 / 401 / 403 / 429 / 402 handling → Task 3 (`runReal`). ✓
- Result display with cached + duration chips and resolved status → Task 3 Step 3. ✓
- Curl snippet kept as reference below the panel → Task 2 Step 3. ✓
- Backend tests (owner/grantee/no-access/extraction) → Task 1. ✓
- Frontend manual verification → Tasks 2 & 3 Step 5. ✓

**Placeholder scan:** No TBD/TODO in deliverables. The Task 2 `runReal` stub is intentional and explicitly replaced in Task 3 Step 2.

**Type consistency:** `ParameterOut` (backend) mirrors `Parameter` (frontend, existing). `get_api_parameters` name matches between Task 1 endpoint and its test. `RunSuccess`/`RunAccepted`/`ExecutionPending` defined in Task 2, consumed in Task 3. `generateOwnerKey`, `activeKey`, `OWNER_KEY_SLOT`, `buildQuery`, `pollExecution`, `callRun` names are consistent across Tasks 2–3. The public 200 envelope (`{data, meta}`) and 202 envelope (`{execution_id, status_url}`) match `backend/app/api/public.py`.

**Note on 403/429/402:** These are surfaced by the generic `else` branch in `runReal` (Task 3 Step 2), which reads `body.detail` (string) or `body.detail.detail` (structured), covering "no access", "rate limit exceeded", and "insufficient wallet balance" messages without special-casing each.

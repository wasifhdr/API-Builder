import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Checkbox, CodeBlock, FieldHelp, FieldLabel, Input, cardClasses } from './ui'
import { ApiError, api } from '../lib/api'
import { useSession } from '../hooks/useSession'
import type { Parameter, RunAccepted, RunSuccess } from '../lib/types'

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
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<RunSuccess | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const { user, refetch } = useSession()
  const [savingHeadless, setSavingHeadless] = useState(false)
  // Effective default is headless (matches the config default); an unset
  // preference means the browser window stays hidden.
  const headless = user?.settings.replay_headless ?? true

  async function setHeadless(value: boolean) {
    setSavingHeadless(true)
    setError(null)
    try {
      await api.patch('/me/settings', { replay_headless: value })
      await refetch()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update headless setting')
    } finally {
      setSavingHeadless(false)
    }
  }

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
        setStatus(null)
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
    setStatus(null)
    setError('Timed out waiting for the execution to finish.')
  }

  // Maps one /v1/run response to UI state. The 401 branch here fires only
  // when a retry is not applicable (grantee, or owner's second failure) —
  // runReal handles the owner regenerate-and-retry before calling this.
  async function handleResponse(res: Response, key: string): Promise<void> {
    const body = await res.json().catch(() => ({} as Record<string, unknown>))
    if (res.ok && 'data' in body) {
      setResult(body as unknown as RunSuccess)
    } else if (res.status === 202) {
      setStatus('Running…')
      await pollExecution(key, (body as unknown as RunAccepted).execution_id)
    } else if (res.status === 422) {
      const detail = (body as { detail?: unknown }).detail
      setError(Array.isArray(detail) ? detail.join('; ') : 'Invalid parameters.')
    } else if (res.status === 401) {
      setError('Invalid or revoked API key.')
    } else {
      const d = (body as { detail?: unknown }).detail
      const obj = typeof d === 'object' && d !== null ? (d as { detail?: string; message?: string }) : undefined
      const msg = typeof d === 'string' ? d : (obj?.detail ?? obj?.message)
      setError(msg ?? `Request failed (${res.status}).`)
    }
  }

  async function runReal() {
    const key = activeKey
    if (!key) return
    setRunning(true)
    setError(null)
    setResult(null)
    setStatus(null)
    const qs = buildQuery()
    try {
      let useKey = key
      let res = await callRun(useKey, qs)
      if (res.status === 401 && isOwner) {
        // key was likely revoked — regenerate once and retry
        localStorage.removeItem(OWNER_KEY_SLOT)
        setOwnerKey(null)
        useKey = await generateOwnerKey()
        res = await callRun(useKey, qs)
      }
      await handleResponse(res, useKey)
    } catch {
      setStatus(null)
      setError('Network error calling the API.')
    } finally {
      setRunning(false)
    }
  }

  const canRun = useMemo(() => activeKey !== null, [activeKey])

  return (
    <section className={`${cardClasses({ variant: 'quiet' })} space-y-4`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h2 className="text-h2">Try it</h2>
          {isOwner && (
            <label
              className="flex items-center gap-2 text-sm text-ink/70"
              title="On: replay runs hidden. Off: the Chromium window opens on the worker's desktop so you can watch the run — useful for debugging."
            >
              <Checkbox
                checked={headless}
                disabled={savingHeadless}
                onChange={(e) => setHeadless(e.target.checked)}
              />
              Headless
            </label>
          )}
        </div>
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

      <Button onClick={() => runReal()} disabled={!canRun || running}>
        {running ? 'Running…' : 'Run'}
      </Button>
      {!canRun && (
        <FieldHelp>{isOwner ? 'Generate a test key to run.' : 'Paste your API key to run.'}</FieldHelp>
      )}

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
    </section>
  )
}

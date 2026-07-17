import { useEffect, useMemo, useState } from 'react'
import { Badge, Button, Checkbox, FieldHelp, FieldLabel, Input, cardClasses } from './ui'
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

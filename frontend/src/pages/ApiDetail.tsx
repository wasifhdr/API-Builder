import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useSession } from '../hooks/useSession'
import { ApiError, api } from '../lib/api'
import type { ApiExecution, CustomApi, Grant, Invite } from '../lib/types'

export default function ApiDetail() {
  const { apiId } = useParams<{ apiId: string }>()
  const { user } = useSession()
  const [customApi, setCustomApi] = useState<CustomApi | null>(null)
  const [executions, setExecutions] = useState<ApiExecution[]>([])
  const [invites, setInvites] = useState<Invite[]>([])
  const [grants, setGrants] = useState<Grant[]>([])
  const [cacheTtl, setCacheTtl] = useState(0)
  const [priceInput, setPriceInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)

  const isOwner = !!user && !!customApi && customApi.owner_id === user.id

  function load() {
    api
      .get<CustomApi>(`/apis/${apiId}`)
      .then((a) => {
        setCustomApi(a)
        setCacheTtl(a.cache_ttl_seconds)
        setPriceInput(a.price_bdt ?? '')
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load API'))
    api.get<ApiExecution[]>(`/apis/${apiId}/executions`).then(setExecutions).catch(() => undefined)
    api.get<Invite[]>(`/apis/${apiId}/invites`).then(setInvites).catch(() => undefined)
    api.get<Grant[]>(`/apis/${apiId}/grants`).then(setGrants).catch(() => undefined)
  }

  useEffect(load, [apiId])

  async function toggleActive() {
    if (!customApi) return
    try {
      const updated = await api.patch<CustomApi>(`/apis/${apiId}`, { is_active: !customApi.is_active })
      setCustomApi(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update')
    }
  }

  async function saveCacheTtl() {
    setSaveMessage(null)
    try {
      const updated = await api.patch<CustomApi>(`/apis/${apiId}`, { cache_ttl_seconds: cacheTtl })
      setCustomApi(updated)
      setSaveMessage('Saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update')
    }
  }

  async function toggleVisibility() {
    if (!customApi) return
    setError(null)
    const next = customApi.visibility === 'shared' ? 'private' : 'shared'
    try {
      const updated = await api.patch<CustomApi>(`/apis/${apiId}`, { visibility: next })
      setCustomApi(updated)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update visibility')
    }
  }

  async function savePrice() {
    setError(null)
    try {
      const updated = await api.patch<CustomApi>(`/apis/${apiId}`, {
        price_bdt: priceInput.trim() === '' ? null : priceInput.trim(),
      })
      setCustomApi(updated)
      setSaveMessage('Saved.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update price')
    }
  }

  async function createInvite() {
    setError(null)
    try {
      const invite = await api.post<Invite>(`/apis/${apiId}/invites`, {})
      setInvites((prev) => [invite, ...prev])
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to create invite')
    }
  }

  async function revokeInvite(id: string) {
    try {
      await api.delete(`/apis/${apiId}/invites/${id}`)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to revoke invite')
    }
  }

  async function revokeGrant(id: string) {
    try {
      await api.delete(`/apis/${apiId}/grants/${id}`)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to revoke grant')
    }
  }

  async function regenerateSpec() {
    try {
      const updated = await api.post<CustomApi>(`/apis/${apiId}/regenerate-spec`)
      setCustomApi(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to regenerate spec')
    }
  }

  if (!customApi) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white text-gray-500">
        {error ?? 'Loading…'}
      </div>
    )
  }

  const curlExample = `curl -H "X-API-Key: ab_..." "http://localhost:8000/v1/run/${customApi.slug}"`
  const inviteLink = (token: string) => `${window.location.origin}/invite/${token}`

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <Link to="/dashboard" className="text-sm text-gray-600 hover:text-gray-900">
          &larr; Dashboard
        </Link>
        <span className="text-xs uppercase font-semibold text-gray-500">{customApi.spec_status}</span>
      </header>
      <main className="p-6 max-w-2xl space-y-6">
        {error && <p className="text-red-600 text-sm">{error}</p>}
        {!isOwner && <p className="text-xs text-blue-600 uppercase font-semibold">Shared with you</p>}

        <div>
          <h1 className="text-lg font-semibold text-gray-900">{customApi.name}</h1>
          <p className="text-sm text-gray-500 font-mono">/v1/run/{customApi.slug}</p>
        </div>

        <div className="flex items-center gap-4 text-sm">
          <Link to={`/docs/${customApi.slug}`} className="text-blue-600 hover:text-blue-800">
            View docs
          </Link>
          {isOwner && (
            <button type="button" onClick={regenerateSpec} className="text-gray-600 hover:text-gray-900">
              Regenerate docs
            </button>
          )}
        </div>

        <section>
          <h2 className="text-sm font-semibold text-gray-900 mb-2">Try it</h2>
          <pre className="rounded bg-gray-50 p-3 text-xs overflow-auto">{curlExample}</pre>
        </section>

        {isOwner && (
          <>
            <section className="space-y-3">
              <h2 className="text-sm font-semibold text-gray-900">Settings</h2>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" checked={customApi.is_active} onChange={toggleActive} />
                Active (accepting requests)
              </label>
              <div className="flex items-center gap-2 text-sm">
                <label htmlFor="cache-ttl">Cache TTL (seconds)</label>
                <input
                  id="cache-ttl"
                  type="number"
                  min={0}
                  value={cacheTtl}
                  onChange={(e) => setCacheTtl(Number(e.target.value))}
                  className="w-24 rounded border border-gray-300 px-2 py-1"
                />
                <button type="button" onClick={saveCacheTtl} className="text-xs rounded bg-gray-900 text-white px-2 py-1">
                  Save
                </button>
                {saveMessage && <span className="text-green-600 text-xs">{saveMessage}</span>}
              </div>
            </section>

            <section className="space-y-3">
              <h2 className="text-sm font-semibold text-gray-900">Sharing</h2>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={customApi.visibility === 'shared'}
                  onChange={toggleVisibility}
                />
                Shared (allow invites &amp; paid access — requires Pro or Max)
              </label>
              {customApi.visibility === 'shared' && (
                <div className="flex items-center gap-2 text-sm">
                  <label htmlFor="price">Price for grantees (৳, blank = free)</label>
                  <input
                    id="price"
                    type="text"
                    value={priceInput}
                    onChange={(e) => setPriceInput(e.target.value)}
                    placeholder="free"
                    className="w-24 rounded border border-gray-300 px-2 py-1"
                  />
                  <button type="button" onClick={savePrice} className="text-xs rounded bg-gray-900 text-white px-2 py-1">
                    Save
                  </button>
                </div>
              )}
            </section>

            {customApi.visibility === 'shared' && (
              <section className="space-y-3">
                <div className="flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-gray-900">Invites</h2>
                  <button type="button" onClick={createInvite} className="text-xs rounded bg-gray-900 text-white px-2 py-1">
                    New invite
                  </button>
                </div>
                {invites.length === 0 && <p className="text-sm text-gray-400">No invites yet.</p>}
                <ul className="space-y-2">
                  {invites.map((inv) => (
                    <li key={inv.id} className="rounded border border-gray-200 p-2 text-xs flex items-center justify-between">
                      <div className="font-mono truncate mr-2">{inviteLink(inv.token)}</div>
                      <div className="flex items-center gap-2 shrink-0">
                        <span className="text-gray-400">{inv.use_count} used</span>
                        {inv.revoked_at ? (
                          <span className="text-gray-400">revoked</span>
                        ) : (
                          <button type="button" onClick={() => revokeInvite(inv.id)} className="text-red-600">
                            Revoke
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>

                <h2 className="text-sm font-semibold text-gray-900 pt-2">Grants</h2>
                {grants.length === 0 && <p className="text-sm text-gray-400">No one has access yet.</p>}
                <ul className="space-y-2">
                  {grants.map((g) => (
                    <li key={g.id} className="rounded border border-gray-200 p-2 text-xs flex items-center justify-between">
                      <span className="font-mono">{g.user_id}</span>
                      <div className="flex items-center gap-2">
                        <span className="text-gray-400">{g.granted_via}</span>
                        {g.revoked_at ? (
                          <span className="text-gray-400">revoked</span>
                        ) : (
                          <button type="button" onClick={() => revokeGrant(g.id)} className="text-red-600">
                            Revoke
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section>
              <h2 className="text-sm font-semibold text-gray-900 mb-2">Recent executions</h2>
              {executions.length === 0 && <p className="text-sm text-gray-400">No executions yet.</p>}
              {executions.length > 0 && (
                <table className="w-full text-xs border border-gray-200 rounded-md">
                  <thead>
                    <tr className="text-left text-gray-500">
                      <th className="p-2">Status</th>
                      <th className="p-2">Params</th>
                      <th className="p-2">Duration</th>
                      <th className="p-2">Error</th>
                      <th className="p-2">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {executions.map((e) => (
                      <tr key={e.id} className="border-t border-gray-100">
                        <td className="p-2">{e.status}</td>
                        <td className="p-2 font-mono">{JSON.stringify(e.params)}</td>
                        <td className="p-2">{e.duration_ms != null ? `${e.duration_ms}ms` : ''}</td>
                        <td className="p-2 text-red-600">
                          {e.error_message}
                          {e.failure_artifact_path && (
                            <div className="text-gray-400">{e.failure_artifact_path}</div>
                          )}
                        </td>
                        <td className="p-2">{new Date(e.created_at).toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          </>
        )}
      </main>
    </div>
  )
}

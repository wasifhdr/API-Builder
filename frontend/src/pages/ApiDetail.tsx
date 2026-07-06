import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { ApiExecution, CustomApi } from '../lib/types'

export default function ApiDetail() {
  const { apiId } = useParams<{ apiId: string }>()
  const [customApi, setCustomApi] = useState<CustomApi | null>(null)
  const [executions, setExecutions] = useState<ApiExecution[]>([])
  const [cacheTtl, setCacheTtl] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)

  function load() {
    api
      .get<CustomApi>(`/apis/${apiId}`)
      .then((a) => {
        setCustomApi(a)
        setCacheTtl(a.cache_ttl_seconds)
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load API'))
    api
      .get<ApiExecution[]>(`/apis/${apiId}/executions`)
      .then(setExecutions)
      .catch(() => undefined)
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

  if (!customApi) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white text-gray-500">
        {error ?? 'Loading…'}
      </div>
    )
  }

  const curlExample = `curl -H "X-API-Key: ab_..." "http://localhost:8000/v1/run/${customApi.slug}"`

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

        <div>
          <h1 className="text-lg font-semibold text-gray-900">{customApi.name}</h1>
          <p className="text-sm text-gray-500 font-mono">/v1/run/{customApi.slug}</p>
        </div>

        <section>
          <h2 className="text-sm font-semibold text-gray-900 mb-2">Try it</h2>
          <pre className="rounded bg-gray-50 p-3 text-xs overflow-auto">{curlExample}</pre>
        </section>

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
      </main>
    </div>
  )
}

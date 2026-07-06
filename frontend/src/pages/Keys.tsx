import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'

interface ApiKeySummary {
  id: string
  label: string
  key_prefix: string
  last_used_at: string | null
  created_at: string
}

interface ApiKeyCreated extends ApiKeySummary {
  api_key: string
}

export default function Keys() {
  const [keys, setKeys] = useState<ApiKeySummary[]>([])
  const [label, setLabel] = useState('default')
  const [newKey, setNewKey] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  function load() {
    api
      .get<ApiKeySummary[]>('/keys')
      .then(setKeys)
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load keys'))
  }

  useEffect(load, [])

  async function createKey() {
    setError(null)
    try {
      const created = await api.post<ApiKeyCreated>('/keys', { label })
      setNewKey(created.api_key)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create key')
    }
  }

  async function revokeKey(id: string) {
    try {
      await api.delete(`/keys/${id}`)
      load()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to revoke key')
    }
  }

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4">
        <Link to="/dashboard" className="text-sm text-gray-600 hover:text-gray-900">
          &larr; Dashboard
        </Link>
      </header>
      <main className="p-6 max-w-2xl space-y-6">
        <h1 className="text-lg font-semibold text-gray-900">API Keys</h1>
        {error && <p className="text-red-600 text-sm">{error}</p>}

        {newKey && (
          <div className="rounded-md border border-yellow-300 bg-yellow-50 p-3 text-sm">
            <p className="font-medium text-gray-900 mb-1">Copy your key now — it won't be shown again.</p>
            <pre className="overflow-auto rounded bg-white p-2 text-xs">{newKey}</pre>
          </div>
        )}

        <div className="flex items-center gap-2">
          <input
            type="text"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="label"
            className="rounded-md border border-gray-300 px-3 py-2 text-sm"
          />
          <button
            type="button"
            onClick={createKey}
            className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800"
          >
            Create key
          </button>
        </div>

        <table className="w-full text-xs border border-gray-200 rounded-md">
          <thead>
            <tr className="text-left text-gray-500">
              <th className="p-2">Label</th>
              <th className="p-2">Prefix</th>
              <th className="p-2">Last used</th>
              <th className="p-2">Created</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k.id} className="border-t border-gray-100">
                <td className="p-2">{k.label}</td>
                <td className="p-2 font-mono">{k.key_prefix}…</td>
                <td className="p-2">{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : 'never'}</td>
                <td className="p-2">{new Date(k.created_at).toLocaleString()}</td>
                <td className="p-2">
                  <button type="button" onClick={() => revokeKey(k.id)} className="text-red-500 hover:text-red-700">
                    Revoke
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </main>
    </div>
  )
}

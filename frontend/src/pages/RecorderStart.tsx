import { type FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, ApiError } from '../lib/api'

export default function RecorderStart() {
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [startUrl, setStartUrl] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const { workflow_id } = await api.post<{ workflow_id: string }>('/recordings', {
        name,
        start_url: startUrl,
      })
      navigate(`/recorder/${workflow_id}`)
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError('Daily API creation limit reached. Upgrade your plan or try again tomorrow.')
      } else {
        setError(err instanceof Error ? err.message : 'Failed to start recording')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4">
        <Link to="/dashboard" className="text-sm text-gray-600 hover:text-gray-900">
          &larr; Dashboard
        </Link>
      </header>
      <main className="p-6 max-w-md">
        <h1 className="text-lg font-semibold text-gray-900 mb-4">Record a new workflow</h1>
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <p className="text-red-600 text-sm">{error}</p>}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
            <input
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="e.g. Rokomari book search"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Starting URL</label>
            <input
              type="url"
              required
              value={startUrl}
              onChange={(e) => setStartUrl(e.target.value)}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm"
              placeholder="https://example.com"
            />
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
          >
            {submitting ? 'Starting…' : 'Start recording'}
          </button>
        </form>
      </main>
    </div>
  )
}

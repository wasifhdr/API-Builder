import { useEffect, useState } from 'react'

type Health = { status: string; db: boolean; redis: boolean }

function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/health')
      .then((res) => res.json())
      .then(setHealth)
      .catch((err) => setError(String(err)))
  }, [])

  return (
    <div className="min-h-screen flex items-center justify-center bg-white">
      <div className="text-center space-y-2">
        <h1 className="text-2xl font-semibold text-gray-900">API Builder</h1>
        {error && <p className="text-red-600">{error}</p>}
        {!error && !health && <p className="text-gray-500">Checking backend…</p>}
        {health && (
          <p className="text-gray-700">
            status: <span className="font-mono">{health.status}</span> · db:{' '}
            <span className="font-mono">{String(health.db)}</span> · redis:{' '}
            <span className="font-mono">{String(health.redis)}</span>
          </p>
        )}
      </div>
    </div>
  )
}

export default App

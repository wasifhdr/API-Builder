import { Navigate } from 'react-router-dom'
import { useSession } from '../hooks/useSession'

export default function Landing() {
  const { user, loading } = useSession()

  if (loading) {
    return <div className="min-h-screen flex items-center justify-center bg-white text-gray-500">Loading…</div>
  }
  if (user) return <Navigate to="/dashboard" replace />

  return (
    <div className="min-h-screen flex items-center justify-center bg-white">
      <div className="text-center space-y-6 max-w-sm px-4">
        <h1 className="text-3xl font-semibold text-gray-900">API Builder</h1>
        <p className="text-gray-600">
          Turn any website into a JSON API by recording how you use it.
        </p>
        <a
          href="/api/auth/login"
          className="inline-block rounded-md bg-gray-900 px-5 py-2.5 text-white font-medium hover:bg-gray-800"
        >
          Sign in with Google
        </a>
      </div>
    </div>
  )
}

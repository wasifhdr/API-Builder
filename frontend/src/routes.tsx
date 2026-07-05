import type { ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useSession } from './hooks/useSession'
import Dashboard from './pages/Dashboard'
import Landing from './pages/Landing'
import Settings from './pages/Settings'

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useSession()
  if (loading) {
    return <div className="min-h-screen flex items-center justify-center text-gray-500">Loading…</div>
  }
  if (!user) return <Navigate to="/" replace />
  return <>{children}</>
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route
        path="/dashboard"
        element={
          <RequireAuth>
            <Dashboard />
          </RequireAuth>
        }
      />
      <Route
        path="/settings"
        element={
          <RequireAuth>
            <Settings />
          </RequireAuth>
        }
      />
    </Routes>
  )
}

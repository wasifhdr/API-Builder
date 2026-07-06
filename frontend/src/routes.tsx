import type { ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useSession } from './hooks/useSession'
import ApiDetail from './pages/ApiDetail'
import Dashboard from './pages/Dashboard'
import Keys from './pages/Keys'
import Landing from './pages/Landing'
import RecorderSession from './pages/RecorderSession'
import RecorderStart from './pages/RecorderStart'
import Settings from './pages/Settings'
import WorkflowEditor from './pages/WorkflowEditor'

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useSession()
  if (loading) {
    return <div className="min-h-screen flex items-center justify-center bg-white text-gray-500">Loading…</div>
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
      <Route
        path="/recorder"
        element={
          <RequireAuth>
            <RecorderStart />
          </RequireAuth>
        }
      />
      <Route
        path="/recorder/:workflowId"
        element={
          <RequireAuth>
            <RecorderSession />
          </RequireAuth>
        }
      />
      <Route
        path="/workflows/:workflowId/edit"
        element={
          <RequireAuth>
            <WorkflowEditor />
          </RequireAuth>
        }
      />
      <Route
        path="/apis/:apiId"
        element={
          <RequireAuth>
            <ApiDetail />
          </RequireAuth>
        }
      />
      <Route
        path="/keys"
        element={
          <RequireAuth>
            <Keys />
          </RequireAuth>
        }
      />
    </Routes>
  )
}

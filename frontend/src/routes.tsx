import type { ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { useSession } from './hooks/useSession'
import AdminSms from './pages/AdminSms'
import AdminTransactions from './pages/AdminTransactions'
import AdminUsers from './pages/AdminUsers'
import ApiDetail from './pages/ApiDetail'
import ApiDocs from './pages/ApiDocs'
import Billing from './pages/Billing'
import Dashboard from './pages/Dashboard'
import InviteAccept from './pages/InviteAccept'
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

function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useSession()
  if (loading) {
    return <div className="min-h-screen flex items-center justify-center bg-white text-gray-500">Loading…</div>
  }
  if (!user) return <Navigate to="/" replace />
  if (user.role !== 'admin') return <Navigate to="/dashboard" replace />
  return <>{children}</>
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/invite/:token" element={<InviteAccept />} />
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
      <Route
        path="/docs/:slug"
        element={
          <RequireAuth>
            <ApiDocs />
          </RequireAuth>
        }
      />
      <Route
        path="/billing"
        element={
          <RequireAuth>
            <Billing />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/transactions"
        element={
          <RequireAdmin>
            <AdminTransactions />
          </RequireAdmin>
        }
      />
      <Route
        path="/admin/sms"
        element={
          <RequireAdmin>
            <AdminSms />
          </RequireAdmin>
        }
      />
      <Route
        path="/admin/users"
        element={
          <RequireAdmin>
            <AdminUsers />
          </RequireAdmin>
        }
      />
    </Routes>
  )
}

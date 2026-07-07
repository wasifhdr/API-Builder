import type { ReactNode } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { PageLoading } from './components/ui'
import { useSession } from './hooks/useSession'
import AdminApis from './pages/AdminApis'
import AdminAudit from './pages/AdminAudit'
import AdminControls from './pages/AdminControls'
import AdminOverview from './pages/AdminOverview'
import AdminPlans from './pages/AdminPlans'
import AdminSms from './pages/AdminSms'
import AdminTransactions from './pages/AdminTransactions'
import AdminUsers from './pages/AdminUsers'
import ApiDetail from './pages/ApiDetail'
import ApiDocs from './pages/ApiDocs'
import Billing from './pages/Billing'
import ClaimUsername from './pages/ClaimUsername'
import Dashboard from './pages/Dashboard'
import InviteAccept from './pages/InviteAccept'
import Keys from './pages/Keys'
import Landing from './pages/Landing'
import Profile from './pages/Profile'
import RecorderSession from './pages/RecorderSession'
import RecorderStart from './pages/RecorderStart'
import Settings from './pages/Settings'
import WorkflowEditor from './pages/WorkflowEditor'

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useSession()
  if (loading) return <PageLoading />
  if (!user) return <Navigate to="/" replace />
  if (!user.username) return <Navigate to="/claim-username" replace />
  return <>{children}</>
}

function RequireSuperAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useSession()
  if (loading) return <PageLoading />
  if (!user) return <Navigate to="/" replace />
  if (!user.username) return <Navigate to="/claim-username" replace />
  if (user.role !== 'super_admin') return <Navigate to="/dashboard" replace />
  return <>{children}</>
}

function RequireUsernameClaim({ children }: { children: ReactNode }) {
  const { user, loading } = useSession()
  if (loading) return <PageLoading />
  if (!user) return <Navigate to="/" replace />
  if (user.username) return <Navigate to="/dashboard" replace />
  return <>{children}</>
}

export default function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/invite/:token" element={<InviteAccept />} />
      <Route
        path="/claim-username"
        element={
          <RequireUsernameClaim>
            <ClaimUsername />
          </RequireUsernameClaim>
        }
      />
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
        path="/profile"
        element={
          <RequireAuth>
            <Profile />
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
        path="/admin"
        element={<Navigate to="/admin/overview" replace />}
      />
      <Route
        path="/admin/overview"
        element={
          <RequireSuperAdmin>
            <AdminOverview />
          </RequireSuperAdmin>
        }
      />
      <Route
        path="/admin/transactions"
        element={
          <RequireSuperAdmin>
            <AdminTransactions />
          </RequireSuperAdmin>
        }
      />
      <Route
        path="/admin/sms"
        element={
          <RequireSuperAdmin>
            <AdminSms />
          </RequireSuperAdmin>
        }
      />
      <Route
        path="/admin/users"
        element={
          <RequireSuperAdmin>
            <AdminUsers />
          </RequireSuperAdmin>
        }
      />
      <Route
        path="/admin/apis"
        element={
          <RequireSuperAdmin>
            <AdminApis />
          </RequireSuperAdmin>
        }
      />
      <Route
        path="/admin/plans"
        element={
          <RequireSuperAdmin>
            <AdminPlans />
          </RequireSuperAdmin>
        }
      />
      <Route
        path="/admin/controls"
        element={
          <RequireSuperAdmin>
            <AdminControls />
          </RequireSuperAdmin>
        }
      />
      <Route
        path="/admin/audit"
        element={
          <RequireSuperAdmin>
            <AdminAudit />
          </RequireSuperAdmin>
        }
      />
    </Routes>
  )
}

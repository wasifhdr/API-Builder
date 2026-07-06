import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AdminNav from '../components/AdminNav'
import { ApiError, api } from '../lib/api'
import type { AdminUser, PlanTier } from '../lib/types'

const TIERS: PlanTier[] = ['free', 'pro', 'max']

export default function AdminUsers() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [error, setError] = useState<string | null>(null)

  function load() {
    api
      .get<AdminUser[]>('/admin/users')
      .then(setUsers)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load'))
  }

  useEffect(load, [])

  async function setTier(userId: string, tier: PlanTier) {
    setError(null)
    try {
      await api.patch(`/admin/users/${userId}`, { tier })
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update tier')
    }
  }

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4">
        <Link to="/dashboard" className="text-sm text-gray-600 hover:text-gray-900">
          &larr; Dashboard
        </Link>
      </header>
      <AdminNav />
      <main className="p-6 space-y-4">
        <h1 className="text-lg font-semibold text-gray-900">Users</h1>
        {error && <p className="text-red-600 text-sm">{error}</p>}
        <table className="w-full text-xs border border-gray-200 rounded-md">
          <thead>
            <tr className="text-left text-gray-500">
              <th className="p-2">Email</th>
              <th className="p-2">Role</th>
              <th className="p-2">Tier</th>
              <th className="p-2">Override</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id} className="border-t border-gray-100">
                <td className="p-2">{u.email}</td>
                <td className="p-2">{u.role}</td>
                <td className="p-2 uppercase font-medium">{u.effective_tier}</td>
                <td className="p-2">
                  <div className="flex gap-1">
                    {TIERS.map((t) => (
                      <button
                        key={t}
                        type="button"
                        disabled={t === u.effective_tier}
                        onClick={() => setTier(u.id, t)}
                        className={`rounded px-2 py-0.5 ${
                          t === u.effective_tier ? 'bg-gray-100 text-gray-400' : 'bg-gray-900 text-white'
                        }`}
                      >
                        {t}
                      </button>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </main>
    </div>
  )
}

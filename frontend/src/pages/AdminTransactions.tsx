import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AdminNav from '../components/AdminNav'
import { ApiError, api } from '../lib/api'
import type { AdminTransaction } from '../lib/types'

export default function AdminTransactions() {
  const [transactions, setTransactions] = useState<AdminTransaction[]>([])
  const [error, setError] = useState<string | null>(null)
  const [noteInput, setNoteInput] = useState<Record<string, string>>({})

  function load() {
    api
      .get<AdminTransaction[]>('/admin/transactions')
      .then(setTransactions)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load'))
  }

  useEffect(load, [])

  async function verify(id: string) {
    setError(null)
    try {
      await api.post(`/admin/transactions/${id}/verify`)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to verify')
    }
  }

  async function reject(id: string) {
    setError(null)
    try {
      await api.post(`/admin/transactions/${id}/reject`, { note: noteInput[id] ?? '' })
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to reject')
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
        <h1 className="text-lg font-semibold text-gray-900">Transactions</h1>
        {error && <p className="text-red-600 text-sm">{error}</p>}
        {transactions.length === 0 && <p className="text-sm text-gray-400">No transactions yet.</p>}
        {transactions.length > 0 && (
          <table className="w-full text-xs border border-gray-200 rounded-md">
            <thead>
              <tr className="text-left text-gray-500">
                <th className="p-2">Status</th>
                <th className="p-2">Purpose</th>
                <th className="p-2">Amount</th>
                <th className="p-2">TrxID</th>
                <th className="p-2">Method</th>
                <th className="p-2">Note</th>
                <th className="p-2">Created</th>
                <th className="p-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {transactions.map((t) => (
                <tr key={t.id} className="border-t border-gray-100">
                  <td className="p-2 uppercase font-medium">{t.status}</td>
                  <td className="p-2">{t.purpose === 'subscription' ? t.plan_tier : 'api_access'}</td>
                  <td className="p-2">
                    ৳{t.amount_expected_bdt}
                    {t.amount_received_bdt && t.amount_received_bdt !== t.amount_expected_bdt && (
                      <span className="text-gray-400"> (recv ৳{t.amount_received_bdt})</span>
                    )}
                  </td>
                  <td className="p-2 font-mono">{t.bkash_trx_id ?? '—'}</td>
                  <td className="p-2">{t.verification_method ?? '—'}</td>
                  <td className="p-2 text-amber-600">{t.note ?? ''}</td>
                  <td className="p-2">{new Date(t.created_at).toLocaleString()}</td>
                  <td className="p-2">
                    {(t.status === 'pending' || t.status === 'submitted') && (
                      <div className="flex gap-1 items-center">
                        <button
                          type="button"
                          onClick={() => verify(t.id)}
                          className="rounded bg-green-600 text-white px-2 py-0.5"
                        >
                          Verify
                        </button>
                        <input
                          type="text"
                          placeholder="reason"
                          value={noteInput[t.id] ?? ''}
                          onChange={(e) => setNoteInput((prev) => ({ ...prev, [t.id]: e.target.value }))}
                          className="w-20 rounded border border-gray-300 px-1"
                        />
                        <button
                          type="button"
                          onClick={() => reject(t.id)}
                          className="rounded bg-red-600 text-white px-2 py-0.5"
                        >
                          Reject
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </div>
  )
}

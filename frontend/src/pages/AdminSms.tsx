import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AdminNav from '../components/AdminNav'
import { api } from '../lib/api'
import type { AdminSms } from '../lib/types'

export default function AdminSmsPage() {
  const [receipts, setReceipts] = useState<AdminSms[]>([])

  useEffect(() => {
    api.get<AdminSms[]>('/admin/sms').then(setReceipts).catch(() => undefined)
  }, [])

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4">
        <Link to="/dashboard" className="text-sm text-gray-600 hover:text-gray-900">
          &larr; Dashboard
        </Link>
      </header>
      <AdminNav />
      <main className="p-6 space-y-3">
        <h1 className="text-lg font-semibold text-gray-900">SMS feed</h1>
        {receipts.length === 0 && <p className="text-sm text-gray-400">No SMS receipts yet.</p>}
        <ul className="space-y-2">
          {receipts.map((r) => (
            <li key={r.id} className="rounded-md border border-gray-200 p-3 text-xs">
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-gray-500">{r.sms_sender ?? 'unknown'}</span>
                <span className={r.matched_transaction_id ? 'text-green-600' : 'text-gray-400'}>
                  {r.matched_transaction_id ? 'matched' : 'unmatched'}
                </span>
              </div>
              <p className="text-gray-800 mb-1">{r.raw_text}</p>
              <p className="text-gray-400">
                trx={r.parsed_trx_id ?? '—'} amount={r.parsed_amount_bdt ?? '—'} msisdn={r.parsed_sender_msisdn ?? '—'} ·{' '}
                {new Date(r.received_at).toLocaleString()}
              </p>
            </li>
          ))}
        </ul>
      </main>
    </div>
  )
}

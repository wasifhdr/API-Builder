import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AdminNav from '../components/AdminNav'
import AppShell from '../components/AppShell'
import { Badge, buttonClasses, CapsLabel, cardClasses, InlineCode, PageHeader } from '../components/ui'
import { api } from '../lib/api'
import type { AdminSms } from '../lib/types'

export default function AdminSmsPage() {
  const [receipts, setReceipts] = useState<AdminSms[]>([])

  useEffect(() => {
    api.get<AdminSms[]>('/admin/sms').then(setReceipts).catch(() => undefined)
  }, [])

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader eyebrow={<CapsLabel>Admin</CapsLabel>} title="SMS feed" />
      <AdminNav />

      {receipts.length === 0 && <p className="text-sm text-ink/60">No SMS receipts yet.</p>}
      <ul className="space-y-2">
        {receipts.map((r) => (
          <li key={r.id} className={cardClasses({ variant: 'quiet' })}>
            <div className="mb-1 flex items-center justify-between">
              <span className="font-mono text-xs text-ink/60">{r.sms_sender ?? 'unknown'}</span>
              <Badge variant={r.matched_transaction_id ? 'success' : 'neutral'}>
                {r.matched_transaction_id ? 'matched' : 'unmatched'}
              </Badge>
            </div>
            <p className="mb-1 text-sm text-ink/80">{r.raw_text}</p>
            <p className="text-xs text-ink/50">
              trx=<InlineCode>{r.parsed_trx_id ?? '—'}</InlineCode> amount={r.parsed_amount_bdt ?? '—'} msisdn=
              {r.parsed_sender_msisdn ?? '—'} · {new Date(r.received_at).toLocaleString()}
            </p>
          </li>
        ))}
      </ul>
    </AppShell>
  )
}

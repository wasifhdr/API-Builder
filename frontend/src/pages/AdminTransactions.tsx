import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AdminNav from '../components/AdminNav'
import AppShell from '../components/AppShell'
import {
  Badge,
  type BadgeVariant,
  Button,
  buttonClasses,
  CapsLabel,
  EmptyRow,
  Input,
  PageHeader,
  Table,
  TableWrapper,
  Td,
  Th,
  Tr,
} from '../components/ui'
import { ApiError, api } from '../lib/api'
import type { AdminTransaction, PaymentStatus } from '../lib/types'

const STATUS_BADGE: Record<PaymentStatus, BadgeVariant> = {
  pending: 'pending',
  submitted: 'pending',
  verified: 'success',
  rejected: 'failed',
  expired: 'neutral',
}

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
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader eyebrow={<CapsLabel>Admin</CapsLabel>} title="Transactions" />
      <AdminNav />

      {error && <p className="mb-4 text-sm font-medium text-red-deep">{error}</p>}

      <TableWrapper>
        <Table>
          <thead>
            <tr>
              <Th>Status</Th>
              <Th>User</Th>
              <Th>Purpose</Th>
              <Th>Amount</Th>
              <Th>TrxID</Th>
              <Th>Method</Th>
              <Th>Note</Th>
              <Th>Created</Th>
              <Th>Actions</Th>
            </tr>
          </thead>
          <tbody>
            {transactions.length === 0 && <EmptyRow colSpan={9}>No transactions yet.</EmptyRow>}
            {transactions.map((t) => (
              <Tr key={t.id}>
                <Td>
                  <Badge variant={STATUS_BADGE[t.status]}>{t.status}</Badge>
                </Td>
                <Td>
                  {t.user_email}
                  {t.user_username && <span className="text-ink/60"> ({t.user_username})</span>}
                </Td>
                <Td>{t.purpose === 'subscription' ? t.plan_tier : 'api_access'}</Td>
                <Td mono>
                  ৳{t.amount_expected_bdt}
                  {t.amount_received_bdt && t.amount_received_bdt !== t.amount_expected_bdt && (
                    <span className="text-ink/50"> (recv ৳{t.amount_received_bdt})</span>
                  )}
                </Td>
                <Td mono>{t.bkash_trx_id ?? '—'}</Td>
                <Td>{t.verification_method ?? '—'}</Td>
                <Td className="text-gold-deep">{t.note ?? ''}</Td>
                <Td>{new Date(t.created_at).toLocaleString()}</Td>
                <Td>
                  {(t.status === 'pending' || t.status === 'submitted') && (
                    <div className="flex items-center gap-1.5">
                      <Button variant="primary" size="sm" onClick={() => verify(t.id)}>
                        Verify
                      </Button>
                      <Input
                        type="text"
                        placeholder="reason"
                        value={noteInput[t.id] ?? ''}
                        onChange={(e) => setNoteInput((prev) => ({ ...prev, [t.id]: e.target.value }))}
                        className="w-24 py-1"
                      />
                      <Button variant="danger" size="sm" onClick={() => reject(t.id)}>
                        Reject
                      </Button>
                    </div>
                  )}
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </TableWrapper>
    </AppShell>
  )
}

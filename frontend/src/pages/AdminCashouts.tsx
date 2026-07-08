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
import type { AdminCashout, CashoutStatus } from '../lib/types'

const STATUS_BADGE: Record<CashoutStatus, BadgeVariant> = {
  requested: 'pending',
  paid: 'success',
  rejected: 'failed',
}

export default function AdminCashouts() {
  const [cashouts, setCashouts] = useState<AdminCashout[]>([])
  const [error, setError] = useState<string | null>(null)
  const [trxInput, setTrxInput] = useState<Record<string, string>>({})
  const [noteInput, setNoteInput] = useState<Record<string, string>>({})

  function load() {
    api
      .get<AdminCashout[]>('/admin/cashouts')
      .then(setCashouts)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load'))
  }

  useEffect(load, [])

  async function pay(id: string) {
    const bkashTrxId = trxInput[id]?.trim()
    if (!bkashTrxId) return
    setError(null)
    try {
      await api.post(`/admin/cashouts/${id}/pay`, { bkash_trx_id: bkashTrxId })
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to mark paid')
    }
  }

  async function reject(id: string) {
    setError(null)
    try {
      await api.post(`/admin/cashouts/${id}/reject`, { note: noteInput[id] ?? '' })
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
      <PageHeader eyebrow={<CapsLabel>Admin</CapsLabel>} title="Cashouts" />
      <AdminNav />

      {error && <p className="mb-4 text-sm font-medium text-red-deep">{error}</p>}

      <TableWrapper>
        <Table>
          <thead>
            <tr>
              <Th>Status</Th>
              <Th>Creator</Th>
              <Th>Amount</Th>
              <Th>Payout number</Th>
              <Th>Requested</Th>
              <Th>Actions</Th>
            </tr>
          </thead>
          <tbody>
            {cashouts.length === 0 && <EmptyRow colSpan={6}>No cashout requests yet.</EmptyRow>}
            {cashouts.map((c) => (
              <Tr key={c.id}>
                <Td>
                  <Badge variant={STATUS_BADGE[c.status]}>{c.status}</Badge>
                </Td>
                <Td>
                  {c.user_email}
                  {c.user_username && <span className="text-ink/60"> ({c.user_username})</span>}
                </Td>
                <Td mono>৳{c.amount_bdt}</Td>
                <Td mono>{c.payout_msisdn}</Td>
                <Td>{new Date(c.created_at).toLocaleString()}</Td>
                <Td>
                  {c.status === 'requested' ? (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <Input
                        type="text"
                        placeholder="bKash TrxID"
                        value={trxInput[c.id] ?? ''}
                        onChange={(e) => setTrxInput((prev) => ({ ...prev, [c.id]: e.target.value }))}
                        className="w-32 py-1"
                      />
                      <Button variant="primary" size="sm" onClick={() => pay(c.id)}>
                        Mark paid
                      </Button>
                      <Input
                        type="text"
                        placeholder="reason"
                        value={noteInput[c.id] ?? ''}
                        onChange={(e) => setNoteInput((prev) => ({ ...prev, [c.id]: e.target.value }))}
                        className="w-24 py-1"
                      />
                      <Button variant="danger" size="sm" onClick={() => reject(c.id)}>
                        Reject
                      </Button>
                    </div>
                  ) : (
                    <span className="text-xs text-ink/60">
                      {c.status === 'paid' ? `TrxID ${c.bkash_trx_id}` : c.note}
                    </span>
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

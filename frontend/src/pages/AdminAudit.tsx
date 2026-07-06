import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AdminNav from '../components/AdminNav'
import AppShell from '../components/AppShell'
import {
  Button,
  buttonClasses,
  CapsLabel,
  EmptyRow,
  PageHeader,
  Table,
  TableWrapper,
  Td,
  Th,
  Tr,
} from '../components/ui'
import { ApiError, api } from '../lib/api'
import type { AdminAuditLogEntry } from '../lib/types'

const PAGE_SIZE = 50

function formatDetail(detail: Record<string, unknown>): string {
  const keys = Object.keys(detail)
  if (keys.length === 0) return '—'
  return keys.map((k) => `${k}: ${JSON.stringify(detail[k])}`).join(', ')
}

export default function AdminAudit() {
  const [rows, setRows] = useState<AdminAuditLogEntry[]>([])
  const [offset, setOffset] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  function load(nextOffset: number) {
    setLoading(true)
    setError(null)
    api
      .get<AdminAuditLogEntry[]>(`/admin/audit-log?limit=${PAGE_SIZE}&offset=${nextOffset}`)
      .then((data) => {
        setRows(data)
        setOffset(nextOffset)
        setHasMore(data.length === PAGE_SIZE)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load audit log'))
      .finally(() => setLoading(false))
  }

  useEffect(() => load(0), [])

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader eyebrow={<CapsLabel>Admin</CapsLabel>} title="Audit log" />
      <AdminNav />

      {error && <p className="mb-4 text-sm font-medium text-red-deep">{error}</p>}

      <TableWrapper>
        <Table>
          <thead>
            <tr>
              <Th>When</Th>
              <Th>Actor</Th>
              <Th>Action</Th>
              <Th>Target</Th>
              <Th>Detail</Th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && !loading && <EmptyRow colSpan={5}>No audit entries yet.</EmptyRow>}
            {rows.map((r) => (
              <Tr key={r.id}>
                <Td mono>{new Date(r.created_at).toLocaleString()}</Td>
                <Td>
                  {/* actor_user_id is nulled by the same ON DELETE SET NULL that removes
                      actor_email/actor_username, so a missing actor always means the
                      acting account was deleted — there's no separate "system" actor. */}
                  {r.actor_email ?? 'deleted user'}
                  {r.actor_username && <span className="text-ink/60"> ({r.actor_username})</span>}
                </Td>
                <Td mono>{r.action}</Td>
                <Td mono className="max-w-[10rem] truncate">
                  {r.target_type}:{r.target_id}
                </Td>
                <Td className="max-w-md truncate text-xs text-ink/70">{formatDetail(r.detail)}</Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </TableWrapper>

      <div className="mt-4 flex justify-between">
        <Button
          variant="ghost"
          size="sm"
          disabled={offset === 0 || loading}
          onClick={() => load(Math.max(0, offset - PAGE_SIZE))}
        >
          &larr; Newer
        </Button>
        <Button variant="ghost" size="sm" disabled={!hasMore || loading} onClick={() => load(offset + PAGE_SIZE)}>
          Older &rarr;
        </Button>
      </div>
    </AppShell>
  )
}

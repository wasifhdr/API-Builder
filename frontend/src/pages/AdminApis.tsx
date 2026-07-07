import { useEffect, useMemo, useState } from 'react'
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
  FieldError,
  FieldLabel,
  Input,
  Modal,
  PageHeader,
  StatChip,
  Table,
  TableWrapper,
  Td,
  Th,
  Tr,
} from '../components/ui'
import { ApiError, api } from '../lib/api'
import type { AdminApi, SpecStatus } from '../lib/types'

const SPEC_BADGE: Record<SpecStatus, BadgeVariant> = {
  pending: 'neutral',
  generating: 'info',
  ready: 'success',
  failed: 'failed',
}

function errMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

interface RowProps {
  api: AdminApi
  onChanged: () => void
}

function ApiRow({ api: row, onChanged }: RowProps) {
  const [toggleOpen, setToggleOpen] = useState(false)
  const [toggleError, setToggleError] = useState<string | null>(null)
  const [toggleBusy, setToggleBusy] = useState(false)

  const [deleteOpen, setDeleteOpen] = useState(false)
  const [confirmSlug, setConfirmSlug] = useState('')
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)

  async function confirmToggle() {
    setToggleBusy(true)
    setToggleError(null)
    try {
      await api.patch(`/admin/apis/${row.id}`, { is_active: !row.is_active })
      setToggleOpen(false)
      onChanged()
    } catch (err) {
      setToggleError(errMessage(err, 'Failed to update API'))
    } finally {
      setToggleBusy(false)
    }
  }

  async function confirmDelete() {
    setDeleteBusy(true)
    setDeleteError(null)
    try {
      await api.delete(`/admin/apis/${row.id}`)
      setDeleteOpen(false)
      onChanged()
    } catch (err) {
      setDeleteError(errMessage(err, 'Failed to delete API'))
    } finally {
      setDeleteBusy(false)
    }
  }

  return (
    <>
      <Tr>
        <Td>
          <div>{row.owner_username ?? row.owner_email}</div>
          <div className="text-xs text-ink/60">{row.owner_email}</div>
        </Td>
        <Td>
          <div className="font-bold">{row.name}</div>
          <div className="font-mono text-[13px] text-ink/60">{row.slug}</div>
        </Td>
        <Td>
          <Badge variant={row.visibility === 'shared' ? 'info' : 'neutral'}>{row.visibility}</Badge>
        </Td>
        <Td>
          {row.is_active ? <Badge variant="success">Active</Badge> : <Badge variant="failed">Disabled</Badge>}
        </Td>
        <Td>
          <Badge variant={SPEC_BADGE[row.spec_status]}>{row.spec_status}</Badge>
        </Td>
        <Td mono>{row.execution_count}</Td>
        <Td>
          <div className="flex justify-end gap-1">
            <Button size="sm" variant={row.is_active ? 'danger' : 'default'} onClick={() => setToggleOpen(true)}>
              {row.is_active ? 'Deactivate' : 'Activate'}
            </Button>
            <Button size="sm" variant="danger" onClick={() => setDeleteOpen(true)}>
              Delete
            </Button>
          </div>
        </Td>
      </Tr>

      <Modal
        open={toggleOpen}
        onClose={() => {
          setToggleOpen(false)
          setToggleError(null)
        }}
        title={row.is_active ? 'Deactivate this API?' : 'Reactivate this API?'}
        actions={
          <>
            <Button variant="default" onClick={() => setToggleOpen(false)}>
              Cancel
            </Button>
            <Button variant={row.is_active ? 'danger' : 'default'} onClick={confirmToggle} disabled={toggleBusy}>
              {toggleBusy ? 'Working…' : row.is_active ? 'Deactivate' : 'Reactivate'}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink/70">
          {row.is_active
            ? 'Keyed calls to this API will start returning 403 immediately. Data is kept.'
            : 'Keyed calls to this API will work again.'}
        </p>
        {toggleError && <FieldError>{toggleError}</FieldError>}
      </Modal>

      <Modal
        open={deleteOpen}
        onClose={() => {
          setDeleteOpen(false)
          setDeleteError(null)
          setConfirmSlug('')
        }}
        title="Delete this API?"
        actions={
          <>
            <Button variant="default" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={confirmDelete} disabled={deleteBusy || confirmSlug !== row.slug}>
              {deleteBusy ? 'Deleting…' : 'Delete API'}
            </Button>
          </>
        }
      >
        <p className="mb-3 text-sm text-ink/70">
          This permanently removes the API and its executions, grants, and invites. Type its slug{' '}
          <span className="font-mono font-bold">{row.slug}</span> to confirm.
        </p>
        <FieldLabel htmlFor={`confirm-slug-${row.id}`}>Slug</FieldLabel>
        <Input
          id={`confirm-slug-${row.id}`}
          value={confirmSlug}
          onChange={(e) => setConfirmSlug(e.target.value)}
          autoComplete="off"
        />
        {deleteError && <FieldError>{deleteError}</FieldError>}
      </Modal>
    </>
  )
}

export default function AdminApis() {
  const [apis, setApis] = useState<AdminApi[]>([])
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')

  function load() {
    api
      .get<AdminApi[]>('/admin/apis')
      .then(setApis)
      .catch((err) => setError(errMessage(err, 'Failed to load')))
  }

  useEffect(load, [])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return apis
    return apis.filter(
      (a) =>
        a.name.toLowerCase().includes(q) ||
        a.slug.toLowerCase().includes(q) ||
        a.owner_email.toLowerCase().includes(q) ||
        (a.owner_username ?? '').toLowerCase().includes(q),
    )
  }, [apis, search])

  const activeCount = apis.filter((a) => a.is_active).length

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader eyebrow={<CapsLabel>Admin</CapsLabel>} title="APIs" />
      <AdminNav />

      <div className="mb-4 flex flex-wrap items-center gap-4">
        <StatChip value={apis.length} label="total APIs" />
        <StatChip value={activeCount} label="active" />
        <Input
          placeholder="Search by name, slug, or owner…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="max-w-xs"
        />
      </div>

      {error && <p className="mb-4 text-sm font-medium text-red-deep">{error}</p>}

      <TableWrapper>
        <Table>
          <thead>
            <tr>
              <Th>Owner</Th>
              <Th>Name / slug</Th>
              <Th>Visibility</Th>
              <Th>Status</Th>
              <Th>Spec</Th>
              <Th>Executions</Th>
              <Th />
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && <EmptyRow colSpan={7}>No APIs found.</EmptyRow>}
            {filtered.map((a) => (
              <ApiRow key={a.id} api={a} onChanged={load} />
            ))}
          </tbody>
        </Table>
      </TableWrapper>
    </AppShell>
  )
}

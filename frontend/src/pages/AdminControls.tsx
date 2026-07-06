import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import AdminNav from '../components/AdminNav'
import AppShell from '../components/AppShell'
import {
  Badge,
  Button,
  buttonClasses,
  CapsLabel,
  EmptyRow,
  FieldError,
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
import { useSession } from '../hooks/useSession'
import { ApiError, api } from '../lib/api'
import type { AdminUser } from '../lib/types'

function errMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

export default function AdminControls() {
  const { user: me } = useSession()
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  const [search, setSearch] = useState('')

  const [promoteTarget, setPromoteTarget] = useState<AdminUser | null>(null)
  const [promoteError, setPromoteError] = useState<string | null>(null)
  const [promoteBusy, setPromoteBusy] = useState(false)

  const [demoteTarget, setDemoteTarget] = useState<AdminUser | null>(null)
  const [demoteError, setDemoteError] = useState<string | null>(null)
  const [demoteBusy, setDemoteBusy] = useState(false)

  function load() {
    api
      .get<AdminUser[]>('/admin/users')
      .then(setUsers)
      .catch((err) => setLoadError(errMessage(err, 'Failed to load users')))
  }

  useEffect(load, [])

  const superAdmins = useMemo(() => users.filter((u) => u.role === 'super_admin'), [users])

  const searchResults = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return []
    return users
      .filter((u) => u.role !== 'super_admin')
      .filter(
        (u) => u.email.toLowerCase().includes(q) || (u.username ?? '').toLowerCase().includes(q),
      )
      .slice(0, 20)
  }, [users, search])

  async function confirmPromote() {
    if (!promoteTarget) return
    setPromoteBusy(true)
    setPromoteError(null)
    try {
      await api.patch(`/admin/users/${promoteTarget.id}`, { role: 'super_admin' })
      setPromoteTarget(null)
      setSearch('')
      load()
    } catch (err) {
      setPromoteError(errMessage(err, 'Failed to promote user'))
    } finally {
      setPromoteBusy(false)
    }
  }

  async function confirmDemote() {
    if (!demoteTarget) return
    setDemoteBusy(true)
    setDemoteError(null)
    try {
      await api.patch(`/admin/users/${demoteTarget.id}`, { role: 'user' })
      setDemoteTarget(null)
      load()
    } catch (err) {
      setDemoteError(errMessage(err, 'Failed to demote user'))
    } finally {
      setDemoteBusy(false)
    }
  }

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader eyebrow={<CapsLabel>Admin</CapsLabel>} title="Admin Controls" />
      <AdminNav />

      {loadError && <p className="mb-4 text-sm font-medium text-red-deep">{loadError}</p>}

      <div className="mb-6">
        <StatChip value={superAdmins.length} label="super admins" />
      </div>

      <div className="mb-8">
        <h2 className="text-h2 mb-3">Current super admins</h2>
        <TableWrapper>
          <Table>
            <thead>
              <tr>
                <Th>Email</Th>
                <Th>Username</Th>
                <Th />
              </tr>
            </thead>
            <tbody>
              {superAdmins.length === 0 && <EmptyRow colSpan={3}>No super admins.</EmptyRow>}
              {superAdmins.map((u) => (
                <Tr key={u.id}>
                  <Td>
                    {u.email}
                    {me?.id === u.id && (
                      <Badge variant="info" className="ml-2">
                        You
                      </Badge>
                    )}
                  </Td>
                  <Td>{u.username ?? <span className="text-ink/45">unset</span>}</Td>
                  <Td>
                    <div className="flex justify-end">
                      <Button
                        size="sm"
                        variant="danger-ghost"
                        disabled={me?.id === u.id}
                        onClick={() => setDemoteTarget(u)}
                      >
                        Demote
                      </Button>
                    </div>
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </TableWrapper>
      </div>

      <div>
        <h2 className="text-h2 mb-3">Promote a user</h2>
        <Input
          placeholder="Search by email or username…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="mb-3 max-w-sm"
        />
        {search.trim() && (
          <TableWrapper>
            <Table>
              <thead>
                <tr>
                  <Th>Email</Th>
                  <Th>Username</Th>
                  <Th>Role</Th>
                  <Th />
                </tr>
              </thead>
              <tbody>
                {searchResults.length === 0 && <EmptyRow colSpan={4}>No matching users.</EmptyRow>}
                {searchResults.map((u) => (
                  <Tr key={u.id}>
                    <Td>{u.email}</Td>
                    <Td>{u.username ?? <span className="text-ink/45">unset</span>}</Td>
                    <Td>{u.role}</Td>
                    <Td>
                      <div className="flex justify-end">
                        <Button size="sm" onClick={() => setPromoteTarget(u)}>
                          Promote
                        </Button>
                      </div>
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrapper>
        )}
      </div>

      <Modal
        open={promoteTarget !== null}
        onClose={() => {
          setPromoteTarget(null)
          setPromoteError(null)
        }}
        title="Promote to super admin?"
        actions={
          <>
            <Button variant="default" onClick={() => setPromoteTarget(null)}>
              Cancel
            </Button>
            <Button onClick={confirmPromote} disabled={promoteBusy}>
              {promoteBusy ? 'Promoting…' : 'Promote'}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink/70">
          <span className="font-mono font-bold">{promoteTarget?.email}</span> will gain full super
          admin powers: managing users, plans, and payments, above all payment tiers.
        </p>
        {promoteError && <FieldError>{promoteError}</FieldError>}
      </Modal>

      <Modal
        open={demoteTarget !== null}
        onClose={() => {
          setDemoteTarget(null)
          setDemoteError(null)
        }}
        title="Demote this super admin?"
        actions={
          <>
            <Button variant="default" onClick={() => setDemoteTarget(null)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={confirmDemote} disabled={demoteBusy}>
              {demoteBusy ? 'Demoting…' : 'Demote'}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink/70">
          <span className="font-mono font-bold">{demoteTarget?.email}</span> will lose super admin
          powers and become a regular user.
        </p>
        {demoteError && <FieldError>{demoteError}</FieldError>}
      </Modal>
    </AppShell>
  )
}

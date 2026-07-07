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
import { useSession } from '../hooks/useSession'
import { ApiError, api } from '../lib/api'
import type { AdminKey, AdminUser, AdminUserUpdate, AdminWorkflow, PlanTier, UserRole } from '../lib/types'

const WORKFLOW_BADGE: Record<AdminWorkflow['status'], BadgeVariant> = {
  recording: 'info',
  draft: 'neutral',
  ready: 'success',
  archived: 'neutral',
}

const TIERS: PlanTier[] = ['free', 'pro', 'max']
const TIER_BADGE: Record<PlanTier, BadgeVariant> = { free: 'neutral', pro: 'info', max: 'purple' }

function errMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

interface RowProps {
  user: AdminUser
  isSelf: boolean
  expanded: boolean
  onToggle: () => void
  onChanged: () => void
}

function UserRow({ user, isSelf, expanded, onToggle, onChanged }: RowProps) {
  const [name, setName] = useState(user.name ?? '')
  const [phone, setPhone] = useState(user.phone ?? '')
  const [detailError, setDetailError] = useState<string | null>(null)
  const [detailSaving, setDetailSaving] = useState(false)

  const [suspendOpen, setSuspendOpen] = useState(false)
  const [suspendError, setSuspendError] = useState<string | null>(null)
  const [suspendBusy, setSuspendBusy] = useState(false)

  const [deleteOpen, setDeleteOpen] = useState(false)
  const [confirmEmail, setConfirmEmail] = useState('')
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleteBusy, setDeleteBusy] = useState(false)

  const [keys, setKeys] = useState<AdminKey[]>([])
  const [keysError, setKeysError] = useState<string | null>(null)
  const [relabelId, setRelabelId] = useState<string | null>(null)
  const [relabelValue, setRelabelValue] = useState('')

  const [workflows, setWorkflows] = useState<AdminWorkflow[]>([])
  const [workflowsError, setWorkflowsError] = useState<string | null>(null)

  const suspended = user.suspended_at !== null

  useEffect(() => {
    setName(user.name ?? '')
    setPhone(user.phone ?? '')
  }, [user.name, user.phone])

  function loadKeys() {
    api
      .get<AdminKey[]>(`/admin/users/${user.id}/keys`)
      .then(setKeys)
      .catch((err) => setKeysError(errMessage(err, 'Failed to load keys')))
  }

  function loadWorkflows() {
    api
      .get<AdminWorkflow[]>(`/admin/users/${user.id}/workflows`)
      .then(setWorkflows)
      .catch((err) => setWorkflowsError(errMessage(err, 'Failed to load workflows')))
  }

  useEffect(() => {
    if (expanded) {
      loadKeys()
      loadWorkflows()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expanded])

  async function deleteWorkflow(workflowId: string) {
    setWorkflowsError(null)
    try {
      await api.delete(`/admin/workflows/${workflowId}`)
      loadWorkflows()
      onChanged()
    } catch (err) {
      setWorkflowsError(errMessage(err, 'Failed to delete workflow'))
    }
  }

  async function patch(body: AdminUserUpdate) {
    return api.patch<AdminUser>(`/admin/users/${user.id}`, body)
  }

  async function saveDetails() {
    setDetailSaving(true)
    setDetailError(null)
    try {
      await patch({ name: name.trim() || null, phone: phone.trim() || null })
      onChanged()
    } catch (err) {
      setDetailError(errMessage(err, 'Failed to save'))
    } finally {
      setDetailSaving(false)
    }
  }

  async function setTier(tier: PlanTier) {
    setDetailError(null)
    try {
      await patch({ tier })
      onChanged()
    } catch (err) {
      setDetailError(errMessage(err, 'Failed to update tier'))
    }
  }

  async function setRole(role: UserRole) {
    setDetailError(null)
    try {
      await patch({ role })
      onChanged()
    } catch (err) {
      setDetailError(errMessage(err, 'Failed to update role'))
    }
  }

  async function confirmSuspendToggle() {
    setSuspendBusy(true)
    setSuspendError(null)
    try {
      await patch({ suspended: !suspended })
      setSuspendOpen(false)
      onChanged()
    } catch (err) {
      setSuspendError(errMessage(err, 'Failed to update suspension'))
    } finally {
      setSuspendBusy(false)
    }
  }

  async function confirmDelete() {
    setDeleteBusy(true)
    setDeleteError(null)
    try {
      await api.delete(`/admin/users/${user.id}`)
      setDeleteOpen(false)
      onChanged()
    } catch (err) {
      setDeleteError(errMessage(err, 'Failed to delete user'))
    } finally {
      setDeleteBusy(false)
    }
  }

  async function relabelKey(keyId: string) {
    setKeysError(null)
    try {
      await api.patch(`/admin/users/${user.id}/keys/${keyId}`, { label: relabelValue })
      setRelabelId(null)
      loadKeys()
    } catch (err) {
      setKeysError(errMessage(err, 'Failed to relabel key'))
    }
  }

  async function revokeKey(keyId: string) {
    setKeysError(null)
    try {
      await api.delete(`/admin/users/${user.id}/keys/${keyId}`)
      loadKeys()
    } catch (err) {
      setKeysError(errMessage(err, 'Failed to revoke key'))
    }
  }

  return (
    <>
      <Tr className="cursor-pointer" onClick={onToggle}>
        <Td>{user.email}</Td>
        <Td>{user.username ?? <span className="text-ink/45">unset</span>}</Td>
        <Td>
          <Badge variant={user.role === 'super_admin' ? 'purple' : 'neutral'}>{user.role}</Badge>
        </Td>
        <Td>
          <Badge variant={TIER_BADGE[user.effective_tier]}>{user.effective_tier}</Badge>
        </Td>
        <Td>{suspended ? <Badge variant="failed">Suspended</Badge> : <Badge variant="success">Active</Badge>}</Td>
        <Td mono>
          {user.workflow_count} wf / {user.api_count} api / {user.key_count} keys
        </Td>
      </Tr>
      {expanded && (
        <tr>
          <td colSpan={6} className="border-b border-sand bg-cream/40 px-4 py-4">
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              <div>
                <h3 className="text-h3 mb-2">Details</h3>
                <div className="mb-2">
                  <FieldLabel htmlFor={`name-${user.id}`}>Name</FieldLabel>
                  <Input id={`name-${user.id}`} value={name} onChange={(e) => setName(e.target.value)} />
                </div>
                <div className="mb-2">
                  <FieldLabel htmlFor={`phone-${user.id}`}>Phone</FieldLabel>
                  <Input id={`phone-${user.id}`} value={phone} onChange={(e) => setPhone(e.target.value)} />
                </div>
                <Button size="sm" onClick={saveDetails} disabled={detailSaving}>
                  {detailSaving ? 'Saving…' : 'Save details'}
                </Button>

                <div className="mt-4">
                  <FieldLabel>Tier override</FieldLabel>
                  <div className="flex gap-1">
                    {TIERS.map((t) => (
                      <Button
                        key={t}
                        size="sm"
                        variant={t === user.effective_tier ? 'default' : 'ink'}
                        disabled={t === user.effective_tier}
                        onClick={() => setTier(t)}
                      >
                        {t}
                      </Button>
                    ))}
                  </div>
                </div>

                <div className="mt-4">
                  <FieldLabel>Role</FieldLabel>
                  <div className="flex gap-1">
                    <Button
                      size="sm"
                      variant={user.role === 'user' ? 'default' : 'ink'}
                      disabled={user.role === 'user' || isSelf}
                      onClick={() => setRole('user')}
                    >
                      user
                    </Button>
                    <Button
                      size="sm"
                      variant={user.role === 'super_admin' ? 'default' : 'ink'}
                      disabled={user.role === 'super_admin' || isSelf}
                      onClick={() => setRole('super_admin')}
                    >
                      super_admin
                    </Button>
                  </div>
                  {isSelf && <p className="mt-1 text-xs text-ink/60">You cannot change your own role.</p>}
                </div>

                {detailError && <FieldError>{detailError}</FieldError>}

                <div className="mt-4 flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant={suspended ? 'default' : 'danger'}
                    disabled={isSelf}
                    onClick={() => setSuspendOpen(true)}
                  >
                    {suspended ? 'Unsuspend' : 'Suspend'}
                  </Button>
                  <Button size="sm" variant="danger" disabled={isSelf} onClick={() => setDeleteOpen(true)}>
                    Delete user
                  </Button>
                </div>
                {isSelf && <p className="mt-1 text-xs text-ink/60">You cannot suspend or delete yourself here.</p>}
              </div>

              <div>
                <h3 className="text-h3 mb-2">Keys</h3>
                {keysError && <FieldError>{keysError}</FieldError>}
                <TableWrapper>
                  <Table>
                    <thead>
                      <tr>
                        <Th>Label</Th>
                        <Th>Prefix</Th>
                        <Th>Status</Th>
                        <Th />
                      </tr>
                    </thead>
                    <tbody>
                      {keys.length === 0 && <EmptyRow colSpan={4}>No keys.</EmptyRow>}
                      {keys.map((k) => (
                        <Tr key={k.id}>
                          <Td>
                            {relabelId === k.id ? (
                              <div className="flex gap-1">
                                <Input
                                  className="max-w-[8rem] py-1"
                                  value={relabelValue}
                                  onChange={(e) => setRelabelValue(e.target.value)}
                                  autoFocus
                                />
                                <Button size="sm" variant="ink" onClick={() => relabelKey(k.id)}>
                                  Save
                                </Button>
                                <Button size="sm" variant="ghost" onClick={() => setRelabelId(null)}>
                                  Cancel
                                </Button>
                              </div>
                            ) : (
                              k.label
                            )}
                          </Td>
                          <Td mono>{k.key_prefix}</Td>
                          <Td>
                            {k.revoked_at ? (
                              <Badge variant="failed">Revoked</Badge>
                            ) : (
                              <Badge variant="success">Active</Badge>
                            )}
                          </Td>
                          <Td>
                            <div className="flex justify-end gap-1">
                              {relabelId !== k.id && (
                                <Button
                                  size="sm"
                                  variant="ghost"
                                  onClick={() => {
                                    setRelabelId(k.id)
                                    setRelabelValue(k.label)
                                  }}
                                >
                                  Relabel
                                </Button>
                              )}
                              {!k.revoked_at && (
                                <Button size="sm" variant="danger-ghost" onClick={() => revokeKey(k.id)}>
                                  Revoke
                                </Button>
                              )}
                            </div>
                          </Td>
                        </Tr>
                      ))}
                    </tbody>
                  </Table>
                </TableWrapper>
              </div>

              <div>
                <h3 className="text-h3 mb-2">Workflows</h3>
                {workflowsError && <FieldError>{workflowsError}</FieldError>}
                <TableWrapper>
                  <Table>
                    <thead>
                      <tr>
                        <Th>Name</Th>
                        <Th>Status</Th>
                        <Th />
                      </tr>
                    </thead>
                    <tbody>
                      {workflows.length === 0 && <EmptyRow colSpan={3}>No workflows.</EmptyRow>}
                      {workflows.map((w) => (
                        <Tr key={w.id}>
                          <Td>{w.name}</Td>
                          <Td>
                            <Badge variant={WORKFLOW_BADGE[w.status]}>{w.status}</Badge>
                          </Td>
                          <Td>
                            <div className="flex justify-end">
                              <Button size="sm" variant="danger-ghost" onClick={() => deleteWorkflow(w.id)}>
                                Delete
                              </Button>
                            </div>
                          </Td>
                        </Tr>
                      ))}
                    </tbody>
                  </Table>
                </TableWrapper>
              </div>
            </div>
          </td>
        </tr>
      )}

      <Modal
        open={suspendOpen}
        onClose={() => {
          setSuspendOpen(false)
          setSuspendError(null)
        }}
        title={suspended ? 'Unsuspend this user?' : 'Suspend this user?'}
        actions={
          <>
            <Button variant="default" onClick={() => setSuspendOpen(false)}>
              Cancel
            </Button>
            <Button variant={suspended ? 'default' : 'danger'} onClick={confirmSuspendToggle} disabled={suspendBusy}>
              {suspendBusy ? 'Working…' : suspended ? 'Unsuspend' : 'Suspend'}
            </Button>
          </>
        }
      >
        <p className="text-sm text-ink/70">
          {suspended
            ? 'This restores login access for this account.'
            : 'This logs them out everywhere and blocks login until unsuspended.'}
        </p>
        {suspendError && <FieldError>{suspendError}</FieldError>}
      </Modal>

      <Modal
        open={deleteOpen}
        onClose={() => {
          setDeleteOpen(false)
          setDeleteError(null)
          setConfirmEmail('')
        }}
        title="Delete this user?"
        actions={
          <>
            <Button variant="default" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button variant="danger" onClick={confirmDelete} disabled={deleteBusy || confirmEmail !== user.email}>
              {deleteBusy ? 'Deleting…' : 'Delete user'}
            </Button>
          </>
        }
      >
        <p className="mb-3 text-sm text-ink/70">
          This permanently removes their workflows, APIs, and keys. Type their email{' '}
          <span className="font-mono font-bold">{user.email}</span> to confirm.
        </p>
        <FieldLabel htmlFor={`confirm-email-${user.id}`}>Email</FieldLabel>
        <Input
          id={`confirm-email-${user.id}`}
          value={confirmEmail}
          onChange={(e) => setConfirmEmail(e.target.value)}
          autoComplete="off"
        />
        {deleteError && <FieldError>{deleteError}</FieldError>}
      </Modal>
    </>
  )
}

export default function AdminUsers() {
  const { user: me } = useSession()
  const [users, setUsers] = useState<AdminUser[]>([])
  const [error, setError] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)

  function load() {
    api
      .get<AdminUser[]>('/admin/users')
      .then(setUsers)
      .catch((err) => setError(errMessage(err, 'Failed to load')))
  }

  useEffect(load, [])

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q) return users
    return users.filter(
      (u) =>
        u.email.toLowerCase().includes(q) ||
        (u.username ?? '').toLowerCase().includes(q) ||
        (u.name ?? '').toLowerCase().includes(q),
    )
  }, [users, search])

  const suspendedCount = users.filter((u) => u.suspended_at !== null).length

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader eyebrow={<CapsLabel>Admin</CapsLabel>} title="Users" />
      <AdminNav />

      <div className="mb-4 flex flex-wrap items-center gap-4">
        <StatChip value={users.length} label="total users" />
        <StatChip value={suspendedCount} label="suspended" />
        <Input
          placeholder="Search by email, username, or name…"
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
              <Th>Email</Th>
              <Th>Username</Th>
              <Th>Role</Th>
              <Th>Tier</Th>
              <Th>Status</Th>
              <Th>Counts</Th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 && <EmptyRow colSpan={6}>No users found.</EmptyRow>}
            {filtered.map((u) => (
              <UserRow
                key={u.id}
                user={u}
                isSelf={me?.id === u.id}
                expanded={expandedId === u.id}
                onToggle={() => setExpandedId(expandedId === u.id ? null : u.id)}
                onChanged={load}
              />
            ))}
          </tbody>
        </Table>
      </TableWrapper>
    </AppShell>
  )
}

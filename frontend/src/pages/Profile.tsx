import { useEffect, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import AppShell from '../components/AppShell'
import {
  Badge,
  Button,
  buttonClasses,
  CapsLabel,
  cardClasses,
  EmptyRow,
  FieldError,
  FieldHelp,
  FieldLabel,
  Input,
  Modal,
  PageHeader,
  Table,
  TableWrapper,
  Td,
  Th,
  Tr,
} from '../components/ui'
import { useSession } from '../hooks/useSession'
import { ApiError, api } from '../lib/api'
import type { Session, User } from '../lib/types'

export default function Profile() {
  const { user, refetch, logout } = useSession()
  const navigate = useNavigate()

  // --- Details (name / phone) ---
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [detailsSaving, setDetailsSaving] = useState(false)
  const [detailsError, setDetailsError] = useState<string | null>(null)
  const [detailsSaved, setDetailsSaved] = useState(false)

  // --- Sign-in methods (password) ---
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [passwordSaving, setPasswordSaving] = useState(false)
  const [passwordError, setPasswordError] = useState<string | null>(null)
  const [passwordSaved, setPasswordSaved] = useState(false)

  // --- Sessions ---
  const [sessions, setSessions] = useState<Session[]>([])
  const [sessionsError, setSessionsError] = useState<string | null>(null)
  const [revoking, setRevoking] = useState(false)

  // --- Danger zone ---
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [confirmUsername, setConfirmUsername] = useState('')
  const [deletePassword, setDeletePassword] = useState('')
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    if (user) {
      setName(user.name ?? '')
      setPhone(user.phone ?? '')
    }
  }, [user])

  function loadSessions() {
    api
      .get<Session[]>('/me/sessions')
      .then(setSessions)
      .catch((err) => setSessionsError(err instanceof Error ? err.message : 'Failed to load sessions'))
  }

  useEffect(loadSessions, [])

  if (!user) return null

  async function saveDetails(e: FormEvent) {
    e.preventDefault()
    setDetailsSaving(true)
    setDetailsError(null)
    setDetailsSaved(false)
    try {
      await api.patch<User>('/me/profile', { name: name.trim() || null, phone: phone.trim() || null })
      await refetch()
      setDetailsSaved(true)
    } catch (err) {
      setDetailsError(err instanceof ApiError ? err.message : 'Failed to save details')
    } finally {
      setDetailsSaving(false)
    }
  }

  async function savePassword(e: FormEvent) {
    e.preventDefault()
    setPasswordSaving(true)
    setPasswordError(null)
    setPasswordSaved(false)
    try {
      await api.post<User>('/me/password', {
        current_password: user!.has_password ? currentPassword : undefined,
        new_password: newPassword,
      })
      await refetch()
      setCurrentPassword('')
      setNewPassword('')
      setPasswordSaved(true)
      loadSessions()
    } catch (err) {
      setPasswordError(err instanceof ApiError ? err.message : 'Failed to update password')
    } finally {
      setPasswordSaving(false)
    }
  }

  async function revokeOthers() {
    setRevoking(true)
    setSessionsError(null)
    try {
      await api.post('/me/sessions/revoke-others')
      loadSessions()
    } catch (err) {
      setSessionsError(err instanceof Error ? err.message : 'Failed to revoke sessions')
    } finally {
      setRevoking(false)
    }
  }

  async function confirmDelete() {
    setDeleting(true)
    setDeleteError(null)
    try {
      await api.delete('/me', {
        confirm_username: confirmUsername,
        current_password: user!.has_password ? deletePassword : undefined,
      })
      await logout()
      navigate('/')
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : 'Failed to delete account')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader eyebrow={<CapsLabel>Account</CapsLabel>} title="Profile" />

      <div className="max-w-lg space-y-6">
        {/* Identity */}
        <section className={cardClasses({ variant: 'quiet' })}>
          <h2 className="text-h2 mb-3">Identity</h2>
          <div className="space-y-3">
            <div>
              <FieldLabel>Username</FieldLabel>
              <Input value={user.username ?? ''} disabled />
              <FieldHelp>Usernames are permanent and can&apos;t be changed.</FieldHelp>
            </div>
            <div>
              <FieldLabel>Email</FieldLabel>
              <Input value={user.email} disabled />
              <FieldHelp>Email is permanent and can&apos;t be changed.</FieldHelp>
            </div>
          </div>
        </section>

        {/* Details */}
        <section className={cardClasses({ variant: 'quiet' })}>
          <h2 className="text-h2 mb-3">Details</h2>
          <form onSubmit={saveDetails} className="space-y-3">
            <div>
              <FieldLabel htmlFor="profile-name">Name</FieldLabel>
              <Input
                id="profile-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Your name"
              />
            </div>
            <div>
              <FieldLabel htmlFor="profile-phone">Phone</FieldLabel>
              <Input
                id="profile-phone"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="Your phone number"
              />
            </div>
            {detailsError && <FieldError>{detailsError}</FieldError>}
            {detailsSaved && !detailsError && (
              <p className="text-xs font-medium text-green-deep">Saved.</p>
            )}
            <Button type="submit" disabled={detailsSaving}>
              {detailsSaving ? 'Saving…' : 'Save details'}
            </Button>
          </form>
        </section>

        {/* Sign-in methods */}
        <section className={cardClasses({ variant: 'quiet' })}>
          <h2 className="text-h2 mb-3">Sign-in methods</h2>
          <div className="mb-4 flex flex-wrap gap-2">
            <Badge variant={user.has_google ? 'success' : 'neutral'}>
              {user.has_google ? 'Google linked' : 'Google not linked'}
            </Badge>
            <Badge variant={user.has_password ? 'success' : 'neutral'}>
              {user.has_password ? 'Password set' : 'No password set'}
            </Badge>
          </div>
          <form onSubmit={savePassword} className="space-y-3">
            {user.has_password && (
              <div>
                <FieldLabel htmlFor="current-password">Current password</FieldLabel>
                <Input
                  id="current-password"
                  type="password"
                  value={currentPassword}
                  onChange={(e) => setCurrentPassword(e.target.value)}
                  autoComplete="current-password"
                />
              </div>
            )}
            <div>
              <FieldLabel htmlFor="new-password">
                {user.has_password ? 'New password' : 'Set a password'}
              </FieldLabel>
              <Input
                id="new-password"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoComplete="new-password"
                placeholder="At least 8 characters"
              />
            </div>
            {passwordError && <FieldError>{passwordError}</FieldError>}
            {passwordSaved && !passwordError && (
              <p className="text-xs font-medium text-green-deep">
                Password updated. Other sessions have been signed out.
              </p>
            )}
            <Button type="submit" disabled={passwordSaving || newPassword.length < 8}>
              {passwordSaving ? 'Saving…' : user.has_password ? 'Change password' : 'Set password'}
            </Button>
          </form>
        </section>

        {/* Active sessions */}
        <section className={cardClasses({ variant: 'quiet' })}>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-h2">Active sessions</h2>
            <Button variant="ink" size="sm" onClick={revokeOthers} disabled={revoking}>
              {revoking ? 'Revoking…' : 'Log out other sessions'}
            </Button>
          </div>
          {sessionsError && <FieldError>{sessionsError}</FieldError>}
          <TableWrapper>
            <Table>
              <thead>
                <tr>
                  <Th>Session</Th>
                  <Th>Created</Th>
                  <Th>User agent</Th>
                  <Th>IP</Th>
                  <Th />
                </tr>
              </thead>
              <tbody>
                {sessions.length === 0 && <EmptyRow colSpan={5}>No active sessions.</EmptyRow>}
                {sessions.map((s) => (
                  <Tr key={s.sid_prefix}>
                    <Td mono>{s.sid_prefix}…</Td>
                    <Td>{new Date(s.created_at).toLocaleString()}</Td>
                    <Td className="max-w-[12rem] truncate">{s.user_agent ?? 'unknown'}</Td>
                    <Td mono>{s.ip ?? 'unknown'}</Td>
                    <Td>{s.current && <Badge variant="info">Current</Badge>}</Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrapper>
        </section>

        {/* Danger zone */}
        <section className={`${cardClasses({ variant: 'quiet' })} border-red/40`}>
          <h2 className="text-h2 mb-2">Danger zone</h2>
          <p className="mb-4 text-sm text-ink/70">
            Deleting your account removes your workflows, APIs, keys, and subscriptions. This
            can&apos;t be undone.
          </p>
          <Button variant="danger" onClick={() => setDeleteOpen(true)}>
            Delete account
          </Button>
        </section>
      </div>

      <Modal
        open={deleteOpen}
        onClose={() => {
          setDeleteOpen(false)
          setDeleteError(null)
          setConfirmUsername('')
          setDeletePassword('')
        }}
        title="Delete your account?"
        actions={
          <>
            <Button variant="default" onClick={() => setDeleteOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              onClick={confirmDelete}
              disabled={
                deleting || confirmUsername !== user.username || (user.has_password && !deletePassword)
              }
            >
              {deleting ? 'Deleting…' : 'Delete account'}
            </Button>
          </>
        }
      >
        <p className="mb-3 text-sm text-ink/70">
          This is permanent. Type your username{' '}
          <span className="font-mono font-bold">{user.username}</span> to confirm.
        </p>
        <FieldLabel htmlFor="confirm-username">Username</FieldLabel>
        <Input
          id="confirm-username"
          value={confirmUsername}
          onChange={(e) => setConfirmUsername(e.target.value)}
          autoComplete="off"
        />
        {user.has_password && (
          <div className="mt-3">
            <FieldLabel htmlFor="delete-password">Current password</FieldLabel>
            <Input
              id="delete-password"
              type="password"
              value={deletePassword}
              onChange={(e) => setDeletePassword(e.target.value)}
              autoComplete="current-password"
            />
          </div>
        )}
        {deleteError && <FieldError>{deleteError}</FieldError>}
      </Modal>
    </AppShell>
  )
}

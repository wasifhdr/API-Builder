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
  PageHeader,
  Table,
  TableWrapper,
  Td,
  Th,
  Tr,
} from '../components/ui'
import { ApiError, api } from '../lib/api'
import type { AdminUser, PlanTier } from '../lib/types'

const TIERS: PlanTier[] = ['free', 'pro', 'max']

const TIER_BADGE: Record<PlanTier, BadgeVariant> = { free: 'neutral', pro: 'info', max: 'purple' }

export default function AdminUsers() {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [error, setError] = useState<string | null>(null)

  function load() {
    api
      .get<AdminUser[]>('/admin/users')
      .then(setUsers)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load'))
  }

  useEffect(load, [])

  async function setTier(userId: string, tier: PlanTier) {
    setError(null)
    try {
      await api.patch(`/admin/users/${userId}`, { tier })
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update tier')
    }
  }

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader eyebrow={<CapsLabel>Admin</CapsLabel>} title="Users" />
      <AdminNav />

      {error && <p className="mb-4 text-sm font-medium text-red-deep">{error}</p>}

      <TableWrapper>
        <Table>
          <thead>
            <tr>
              <Th>Email</Th>
              <Th>Role</Th>
              <Th>Tier</Th>
              <Th>Override</Th>
            </tr>
          </thead>
          <tbody>
            {users.length === 0 && <EmptyRow colSpan={4}>No users yet.</EmptyRow>}
            {users.map((u) => (
              <Tr key={u.id}>
                <Td>{u.email}</Td>
                <Td>{u.role}</Td>
                <Td>
                  <Badge variant={TIER_BADGE[u.effective_tier]}>{u.effective_tier}</Badge>
                </Td>
                <Td>
                  <div className="flex gap-1">
                    {TIERS.map((t) => (
                      <Button
                        key={t}
                        size="sm"
                        variant={t === u.effective_tier ? 'default' : 'ink'}
                        disabled={t === u.effective_tier}
                        onClick={() => setTier(u.id, t)}
                      >
                        {t}
                      </Button>
                    ))}
                  </div>
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      </TableWrapper>
    </AppShell>
  )
}

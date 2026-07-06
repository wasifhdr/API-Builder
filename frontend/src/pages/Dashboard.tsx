import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AppShell from '../components/AppShell'
import {
  Badge,
  buttonClasses,
  CapsLabel,
  cardClasses,
  EmptyState,
  InlineCode,
  PageHeader,
  QuotaCells,
} from '../components/ui'
import { useSession } from '../hooks/useSession'
import { api } from '../lib/api'
import type { CustomApi } from '../lib/types'

function ApiCard({ item, shared }: { item: CustomApi; shared?: boolean }) {
  return (
    <Link to={`/apis/${item.id}`} className={cardClasses({ variant: 'standard', clickable: true })}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-h2">{item.name}</h3>
          <p className="mt-1">
            <InlineCode>/v1/run/{item.slug}</InlineCode>
          </p>
        </div>
        <Badge variant="info">GET</Badge>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Badge variant={item.is_active ? 'success' : 'neutral'}>{item.is_active ? 'active' : 'disabled'}</Badge>
        {shared && <CapsLabel tone="blue">Shared with you</CapsLabel>}
      </div>
    </Link>
  )
}

export default function Dashboard() {
  const { user } = useSession()
  const [apis, setApis] = useState<CustomApi[]>([])

  useEffect(() => {
    if (!user) return
    api.get<CustomApi[]>('/apis').then(setApis).catch(() => undefined)
  }, [user])

  if (!user) return null

  const ownedApis = apis.filter((a) => a.owner_id === user.id)
  const sharedApis = apis.filter((a) => a.owner_id !== user.id)

  return (
    <AppShell>
      <PageHeader
        eyebrow={<CapsLabel>Dashboard</CapsLabel>}
        title={user.name ?? user.email}
        subline={user.role === 'admin' ? 'Administrator' : undefined}
        actions={
          <Link to="/recorder" className={buttonClasses('primary')}>
            New recording
          </Link>
        }
      />

      <div className={cardClasses({ variant: 'quiet', className: 'mb-8' })}>
        <CapsLabel tone="muted" className="mb-2">
          Daily quota
        </CapsLabel>
        <QuotaCells used={user.quota_used_today} limit={user.quota_limit} />
      </div>

      <section className="mb-8">
        <h2 className="text-h2 mb-3">My APIs</h2>
        {ownedApis.length === 0 ? (
          <EmptyState
            statement={
              <>
                No APIs yet. <span className="text-orange">Record your first one.</span>
              </>
            }
            description="Turn a browser session into a reusable JSON endpoint."
            action={
              <Link to="/recorder" className={buttonClasses('primary')}>
                New recording
              </Link>
            }
          />
        ) : (
          <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
            {ownedApis.map((a) => (
              <ApiCard key={a.id} item={a} />
            ))}
          </div>
        )}
      </section>

      {sharedApis.length > 0 && (
        <section>
          <h2 className="text-h2 mb-3">Shared with me</h2>
          <div className="grid gap-5 md:grid-cols-2 lg:grid-cols-3">
            {sharedApis.map((a) => (
              <ApiCard key={a.id} item={a} shared />
            ))}
          </div>
        </section>
      )}
    </AppShell>
  )
}

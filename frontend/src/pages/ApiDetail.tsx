import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import {
  Badge,
  type BadgeVariant,
  Button,
  buttonClasses,
  CapsLabel,
  cardClasses,
  CodeBlock,
  EmptyRow,
  EmptyState,
  FieldLabel,
  InlineCode,
  Input,
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
import type {
  ApiExecution,
  ApiStats,
  CustomApi,
  ExecutionStatus,
  Grant,
  Invite,
  SpecStatus,
} from '../lib/types'

const SPEC_BADGE: Record<SpecStatus, BadgeVariant> = {
  pending: 'neutral',
  generating: 'pending',
  ready: 'success',
  failed: 'failed',
}

const EXEC_BADGE: Record<ExecutionStatus, BadgeVariant> = {
  queued: 'neutral',
  running: 'info',
  succeeded: 'success',
  failed: 'failed',
  timeout: 'failed',
}

export default function ApiDetail() {
  const { apiId } = useParams<{ apiId: string }>()
  const { user } = useSession()
  const [customApi, setCustomApi] = useState<CustomApi | null>(null)
  const [executions, setExecutions] = useState<ApiExecution[]>([])
  const [invites, setInvites] = useState<Invite[]>([])
  const [grants, setGrants] = useState<Grant[]>([])
  const [cacheTtl, setCacheTtl] = useState(0)
  const [priceInput, setPriceInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const [stats, setStats] = useState<ApiStats | null>(null)
  const [statsError, setStatsError] = useState<string | null>(null)
  const [statsLoading, setStatsLoading] = useState(true)

  const isOwner = !!user && !!customApi && customApi.owner_id === user.id

  function load() {
    api
      .get<CustomApi>(`/apis/${apiId}`)
      .then((a) => {
        setCustomApi(a)
        setCacheTtl(a.cache_ttl_seconds)
        setPriceInput(a.price_bdt ?? '')
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load API'))
    api.get<ApiExecution[]>(`/apis/${apiId}/executions`).then(setExecutions).catch(() => undefined)
    api.get<Invite[]>(`/apis/${apiId}/invites`).then(setInvites).catch(() => undefined)
    api.get<Grant[]>(`/apis/${apiId}/grants`).then(setGrants).catch(() => undefined)
  }

  function loadStats() {
    setStatsLoading(true)
    setStatsError(null)
    api
      .get<ApiStats>(`/apis/${apiId}/stats`)
      .then(setStats)
      .catch((err) => setStatsError(err instanceof ApiError ? err.message : 'Failed to load stats'))
      .finally(() => setStatsLoading(false))
  }

  useEffect(load, [apiId])
  useEffect(loadStats, [apiId])

  async function toggleActive() {
    if (!customApi) return
    try {
      const updated = await api.patch<CustomApi>(`/apis/${apiId}`, { is_active: !customApi.is_active })
      setCustomApi(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update')
    }
  }

  async function saveCacheTtl() {
    setSaveMessage(null)
    try {
      const updated = await api.patch<CustomApi>(`/apis/${apiId}`, { cache_ttl_seconds: cacheTtl })
      setCustomApi(updated)
      setSaveMessage('Saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update')
    }
  }

  async function toggleVisibility() {
    if (!customApi) return
    setError(null)
    const next = customApi.visibility === 'shared' ? 'private' : 'shared'
    try {
      const updated = await api.patch<CustomApi>(`/apis/${apiId}`, { visibility: next })
      setCustomApi(updated)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update visibility')
    }
  }

  async function savePrice() {
    setError(null)
    try {
      const updated = await api.patch<CustomApi>(`/apis/${apiId}`, {
        price_bdt: priceInput.trim() === '' ? null : priceInput.trim(),
      })
      setCustomApi(updated)
      setSaveMessage('Saved.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to update price')
    }
  }

  async function createInvite() {
    setError(null)
    try {
      const invite = await api.post<Invite>(`/apis/${apiId}/invites`, {})
      setInvites((prev) => [invite, ...prev])
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to create invite')
    }
  }

  async function revokeInvite(id: string) {
    try {
      await api.delete(`/apis/${apiId}/invites/${id}`)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to revoke invite')
    }
  }

  async function revokeGrant(id: string) {
    try {
      await api.delete(`/apis/${apiId}/grants/${id}`)
      load()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to revoke grant')
    }
  }

  async function regenerateSpec() {
    try {
      const updated = await api.post<CustomApi>(`/apis/${apiId}/regenerate-spec`)
      setCustomApi(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to regenerate spec')
    }
  }

  if (!customApi) {
    return (
      <AppShell>
        <p className="text-sm text-ink/60">{error ?? 'Loading…'}</p>
      </AppShell>
    )
  }

  const curlExample = `curl -H "X-API-Key: ab_..." "http://localhost:8000/v1/run/${customApi.slug}"`
  const inviteLink = (token: string) => `${window.location.origin}/invite/${token}`

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>

      <PageHeader
        eyebrow={!isOwner ? <CapsLabel tone="blue">Shared with you</CapsLabel> : undefined}
        title={customApi.name}
        subline={<InlineCode>/v1/run/{customApi.slug}</InlineCode>}
        actions={
          <>
            <Badge variant={SPEC_BADGE[customApi.spec_status]}>{customApi.spec_status}</Badge>
            {isOwner &&
              (customApi.is_active ? (
                <Button variant="ink" onClick={toggleActive}>
                  Unpublish
                </Button>
              ) : (
                <Button variant="primary" onClick={toggleActive}>
                  Publish
                </Button>
              ))}
          </>
        }
      />

      {error && <p className="mb-6 text-sm font-medium text-red-deep">{error}</p>}

      <div className="mb-8 flex flex-wrap items-center gap-3">
        <Link to={`/docs/${customApi.slug}`} className={buttonClasses('default', 'sm')}>
          View docs
        </Link>
        {isOwner && (
          <Button variant="ghost" size="sm" onClick={regenerateSpec}>
            Regenerate docs
          </Button>
        )}
      </div>

      <section className="mb-8">
        <h2 className="text-h2 mb-2">Try it</h2>
        <CodeBlock lang="bash" code={curlExample} />
      </section>

      {isOwner && (
        <>
          <section className={`${cardClasses({ variant: 'quiet' })} mb-8 space-y-4`}>
            <h2 className="text-h2">Settings</h2>
            <div className="flex flex-wrap items-end gap-2">
              <div>
                <FieldLabel htmlFor="cache-ttl">Cache TTL (seconds)</FieldLabel>
                <Input
                  id="cache-ttl"
                  type="number"
                  min={0}
                  value={cacheTtl}
                  onChange={(e) => setCacheTtl(Number(e.target.value))}
                  className="w-32"
                />
              </div>
              <Button size="sm" onClick={saveCacheTtl}>
                Save
              </Button>
              {saveMessage && <span className="text-sm font-medium text-green-deep">{saveMessage}</span>}
            </div>
          </section>

          <section className={`${cardClasses({ variant: 'quiet' })} mb-8 space-y-4`}>
            <h2 className="text-h2">Sharing</h2>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 rounded-dot border-2 border-ink accent-orange"
                checked={customApi.visibility === 'shared'}
                onChange={toggleVisibility}
              />
              Shared (allow invites &amp; paid access — requires Pro or Max)
            </label>
            {customApi.visibility === 'shared' && (
              <div className="flex flex-wrap items-end gap-2">
                <div>
                  <FieldLabel htmlFor="price">Price for grantees (৳, blank = free)</FieldLabel>
                  <Input
                    id="price"
                    type="text"
                    value={priceInput}
                    onChange={(e) => setPriceInput(e.target.value)}
                    placeholder="free"
                    className="w-32"
                  />
                </div>
                <Button size="sm" onClick={savePrice}>
                  Save
                </Button>
              </div>
            )}
          </section>

          {customApi.visibility === 'shared' && (
            <section className="mb-8 space-y-4">
              <div className="flex items-center justify-between">
                <h2 className="text-h2">Invites</h2>
                <Button size="sm" onClick={createInvite}>
                  New invite
                </Button>
              </div>
              {invites.length === 0 ? (
                <p className="text-sm text-ink/60">No invites yet.</p>
              ) : (
                <ul className="space-y-2">
                  {invites.map((inv) => (
                    <li
                      key={inv.id}
                      className={`${cardClasses({ variant: 'quiet' })} flex items-center justify-between gap-3 py-3`}
                    >
                      <InlineCode>{inviteLink(inv.token)}</InlineCode>
                      <div className="flex shrink-0 items-center gap-3 text-sm text-ink/60">
                        <span>{inv.use_count} used</span>
                        {inv.revoked_at ? (
                          <Badge variant="neutral">revoked</Badge>
                        ) : (
                          <Button variant="danger-ghost" size="sm" onClick={() => revokeInvite(inv.id)}>
                            Revoke
                          </Button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}

              <h2 className="text-h2 pt-2">Grants</h2>
              {grants.length === 0 ? (
                <p className="text-sm text-ink/60">No one has access yet.</p>
              ) : (
                <ul className="space-y-2">
                  {grants.map((g) => (
                    <li
                      key={g.id}
                      className={`${cardClasses({ variant: 'quiet' })} flex items-center justify-between gap-3 py-3`}
                    >
                      <InlineCode>{g.user_id}</InlineCode>
                      <div className="flex shrink-0 items-center gap-3 text-sm text-ink/60">
                        <span>{g.granted_via}</span>
                        {g.revoked_at ? (
                          <Badge variant="neutral">revoked</Badge>
                        ) : (
                          <Button variant="danger-ghost" size="sm" onClick={() => revokeGrant(g.id)}>
                            Revoke
                          </Button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          <section className="mb-8">
            <h2 className="text-h2 mb-3">Stats</h2>
            {statsLoading && <p className="text-sm text-ink/60">Loading…</p>}
            {statsError && <p className="text-sm font-medium text-red-deep">{statsError}</p>}
            {!statsLoading && !statsError && stats && stats.total_calls === 0 && (
              <EmptyState
                statement={<>No calls <span className="text-orange">yet.</span></>}
                description="Once callers start hitting this API, usage stats will show up here."
              />
            )}
            {!statsLoading && !statsError && stats && stats.total_calls > 0 && (
              <div className="space-y-5">
                <div className="flex flex-wrap gap-3">
                  <StatChip value={stats.total_calls} label="total calls" />
                  <StatChip value={stats.calls_7d} label="calls last 7d" />
                  <StatChip value={`${Math.round(stats.success_rate_7d * 100)}%`} label="success rate (7d)" />
                  <StatChip
                    value={stats.avg_duration_ms_7d != null ? `${Math.round(stats.avg_duration_ms_7d)}ms` : '—'}
                    label="avg duration (7d)"
                  />
                  <StatChip value={`${Math.round(stats.cache_hit_rate_7d * 100)}%`} label="cache hit rate (7d)" />
                </div>

                <div className={cardClasses({ variant: 'quiet' })}>
                  <CapsLabel tone="muted" className="mb-3">
                    Calls per day (last 14 days)
                  </CapsLabel>
                  <div className="flex h-28 items-end gap-1.5">
                    {stats.calls_by_day.map((day) => {
                      const max = Math.max(...stats.calls_by_day.map((d) => d.total), 1)
                      const failed = day.total - day.succeeded
                      const succeededPct = (day.succeeded / max) * 100
                      const failedPct = (failed / max) * 100
                      return (
                        <div
                          key={day.date}
                          className="flex flex-1 flex-col items-center gap-1"
                          title={`${day.date}: ${day.total} calls, ${day.succeeded} succeeded`}
                        >
                          <div className="flex h-24 w-full flex-col-reverse rounded-dot bg-cream">
                            {day.total > 0 && (
                              <>
                                <div className="w-full rounded-t-dot bg-green" style={{ height: `${succeededPct}%` }} />
                                {failed > 0 && <div className="w-full bg-red" style={{ height: `${failedPct}%` }} />}
                              </>
                            )}
                          </div>
                          <span className="text-[10px] text-ink/45">{day.date.slice(5)}</span>
                        </div>
                      )
                    })}
                  </div>
                </div>

                <div>
                  <CapsLabel tone="muted" className="mb-2">
                    Top consumers (30d)
                  </CapsLabel>
                  <TableWrapper>
                    <Table>
                      <thead>
                        <tr>
                          <Th>Consumer</Th>
                          <Th>Calls (30d)</Th>
                        </tr>
                      </thead>
                      <tbody>
                        {stats.top_consumers.length === 0 && <EmptyRow colSpan={2}>No consumers yet.</EmptyRow>}
                        {stats.top_consumers.map((c) => (
                          <Tr key={c.name}>
                            <Td mono>{c.name}</Td>
                            <Td mono>{c.calls_30d}</Td>
                          </Tr>
                        ))}
                      </tbody>
                    </Table>
                  </TableWrapper>
                </div>

                {stats.last_called_at && (
                  <p className="text-xs text-ink/60">
                    Last called {new Date(stats.last_called_at).toLocaleString()}
                  </p>
                )}
              </div>
            )}
          </section>

          <section>
            <h2 className="text-h2 mb-3">Recent executions</h2>
            <TableWrapper>
              <Table>
                <thead>
                  <tr>
                    <Th>Status</Th>
                    <Th>Params</Th>
                    <Th>Duration</Th>
                    <Th>Error</Th>
                    <Th>Created</Th>
                  </tr>
                </thead>
                <tbody>
                  {executions.length === 0 && <EmptyRow colSpan={5}>No executions yet.</EmptyRow>}
                  {executions.map((e) => (
                    <Tr key={e.id}>
                      <Td>
                        <Badge variant={EXEC_BADGE[e.status]}>{e.status}</Badge>
                      </Td>
                      <Td mono>{JSON.stringify(e.params)}</Td>
                      <Td mono>{e.duration_ms != null ? `${e.duration_ms}ms` : ''}</Td>
                      <Td className="text-red-deep">
                        {e.error_message}
                        {e.failure_artifact_path && (
                          <div className="text-xs text-ink/50">{e.failure_artifact_path}</div>
                        )}
                      </Td>
                      <Td>{new Date(e.created_at).toLocaleString()}</Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            </TableWrapper>
          </section>
        </>
      )}
    </AppShell>
  )
}

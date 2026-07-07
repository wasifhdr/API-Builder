import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AdminNav from '../components/AdminNav'
import AppShell from '../components/AppShell'
import { buttonClasses, CapsLabel, PageHeader, StatChip, cardClasses } from '../components/ui'
import { ApiError, api } from '../lib/api'
import type { AdminStats } from '../lib/types'

function errMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback
}

export default function AdminOverview() {
  const [stats, setStats] = useState<AdminStats | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<AdminStats>('/admin/stats')
      .then(setStats)
      .catch((err) => setError(errMessage(err, 'Failed to load stats')))
  }, [])

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader eyebrow={<CapsLabel>Admin</CapsLabel>} title="Overview" />
      <AdminNav />

      {error && <p className="mb-4 text-sm font-medium text-red-deep">{error}</p>}

      {stats && (
        <div className="space-y-6">
          <div className="flex flex-wrap gap-3">
            <StatChip value={stats.total_users} label="total users" />
            <StatChip value={stats.new_users_7d} label="new users (7d)" />
            <StatChip value={stats.suspended_users} label="suspended" />
            <StatChip value={stats.total_apis} label="total APIs" />
            <StatChip value={stats.active_apis} label="active APIs" />
            <StatChip value={`${Math.round(stats.success_rate_7d * 100)}%`} label="success rate (7d)" />
            <StatChip value={`৳${stats.revenue_verified_bdt}`} label="revenue verified" />
            <StatChip value={stats.pending_payments} label="pending payments" />
          </div>

          <div className={cardClasses({ variant: 'quiet' })}>
            <CapsLabel tone="muted" className="mb-3">
              Executions per day (last 14 days)
            </CapsLabel>
            <div className="flex h-28 items-end gap-1.5">
              {stats.executions_by_day.map((day) => {
                const max = Math.max(...stats.executions_by_day.map((d) => d.total), 1)
                const failed = day.total - day.succeeded
                const succeededPct = (day.succeeded / max) * 100
                const failedPct = (failed / max) * 100
                return (
                  <div
                    key={day.date}
                    className="flex flex-1 flex-col items-center gap-1"
                    title={`${day.date}: ${day.total} executions, ${day.succeeded} succeeded`}
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
        </div>
      )}
    </AppShell>
  )
}

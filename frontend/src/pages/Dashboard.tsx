import { Link } from 'react-router-dom'
import { useSession } from '../hooks/useSession'
import type { PlanTier } from '../lib/types'

const TIER_STYLES: Record<PlanTier, string> = {
  free: 'bg-gray-100 text-gray-700',
  pro: 'bg-blue-100 text-blue-700',
  max: 'bg-purple-100 text-purple-700',
}

function TierBadge({ tier }: { tier: PlanTier }) {
  return (
    <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-semibold uppercase ${TIER_STYLES[tier]}`}>
      {tier}
    </span>
  )
}

function QuotaMeter({ used, limit }: { used: number; limit: number | null }) {
  if (limit === null) {
    return <p className="text-sm text-gray-500">Unlimited API creation attempts today</p>
  }

  const pct = Math.min(100, Math.round((used / limit) * 100))
  const atLimit = used >= limit
  return (
    <div>
      <p className="text-sm text-gray-500 mb-1">
        {used} / {limit} API creation attempts used today
        {atLimit && <span className="text-red-600 font-medium"> — limit reached</span>}
      </p>
      <div className="h-1.5 w-full max-w-xs rounded-full bg-gray-100">
        <div
          className={`h-1.5 rounded-full ${atLimit ? 'bg-red-500' : 'bg-gray-900'}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

export default function Dashboard() {
  const { user, logout } = useSession()
  if (!user) return null

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <h1 className="text-lg font-semibold text-gray-900">API Builder</h1>
        <div className="flex items-center gap-4">
          <Link to="/settings" className="text-sm text-gray-600 hover:text-gray-900">
            Settings
          </Link>
          <button
            type="button"
            onClick={() => logout()}
            className="text-sm text-gray-600 hover:text-gray-900"
          >
            Log out
          </button>
        </div>
      </header>
      <main className="p-6">
        <div className="flex items-center gap-3 mb-4">
          {user.picture_url && (
            <img src={user.picture_url} alt="" className="w-10 h-10 rounded-full" />
          )}
          <div>
            <p className="font-medium text-gray-900">{user.name ?? user.email}</p>
            <p className="text-sm text-gray-500 flex items-center gap-2">
              {user.email}
              <TierBadge tier={user.tier} />
              {user.role === 'admin' && (
                <span className="uppercase text-xs font-semibold text-gray-700">admin</span>
              )}
            </p>
          </div>
        </div>
        <div className="mb-6">
          <QuotaMeter used={user.quota_used_today} limit={user.quota_limit} />
        </div>
        <p className="text-gray-500 text-sm">No APIs yet — recording comes in a later phase.</p>
      </main>
    </div>
  )
}

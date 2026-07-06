import type { ReactNode } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { useSession } from '../hooks/useSession'
import type { PlanTier } from '../lib/types'

const NAV_LINKS = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/keys', label: 'API Keys' },
  { to: '/billing', label: 'Billing' },
  { to: '/settings', label: 'Settings' },
]

const TIER_CHROME: Record<PlanTier, string> = {
  free: 'bg-cream/20 text-cream',
  pro: 'bg-gold text-ink',
  max: 'bg-purple text-paper',
}

const LINK_CLASS = 'rounded-pill px-3 py-1.5 text-sm font-bold transition-colors duration-100 focus-visible:outline-[3px] focus-visible:outline-gold focus-visible:outline-offset-2'
const LINK_INACTIVE = 'text-cream/75 hover:bg-paper/10 hover:text-paper'
const LINK_ACTIVE = 'bg-paper/10 text-gold'

export default function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useSession()
  const location = useLocation()
  const isAdminActive = location.pathname.startsWith('/admin')

  return (
    <div className="min-h-screen bg-cream">
      <header className="sticky top-0 z-40 bg-ink text-paper">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-6">
          <div className="flex items-center gap-6">
            <Link to="/dashboard" className="font-display text-lg font-extrabold tracking-tight text-paper">
              API Builder
            </Link>
            <nav className="hidden items-center gap-1 md:flex">
              {NAV_LINKS.map((l) => (
                <NavLink
                  key={l.to}
                  to={l.to}
                  className={({ isActive }) => `${LINK_CLASS} ${isActive ? LINK_ACTIVE : LINK_INACTIVE}`}
                >
                  {l.label}
                </NavLink>
              ))}
              {user?.role === 'super_admin' && (
                <Link
                  to="/admin/transactions"
                  className={`${LINK_CLASS} ${isAdminActive ? LINK_ACTIVE : LINK_INACTIVE}`}
                >
                  Admin
                </Link>
              )}
            </nav>
          </div>
          {user && (
            <div className="flex items-center gap-3">
              <span className="hidden text-xs text-cream/60 sm:inline">{user.email}</span>
              <span className={`inline-flex items-center rounded-pill px-2.5 py-0.5 text-label uppercase ${TIER_CHROME[user.tier]}`}>
                {user.tier}
              </span>
              <button
                type="button"
                onClick={() => logout()}
                className={`${LINK_CLASS} ${LINK_INACTIVE}`}
              >
                Log out
              </button>
            </div>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
    </div>
  )
}

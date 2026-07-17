import type { ComponentType, SVGProps } from 'react'
import { Link, NavLink } from 'react-router-dom'
import type { PlanTier, User } from '../lib/types'
import {
  AdminIcon,
  BillingIcon,
  ChevronLeftIcon,
  CloseIcon,
  DashboardIcon,
  KeyIcon,
  LogoutIcon,
  ProfileIcon,
  SettingsIcon,
} from './nav-icons'

type IconType = ComponentType<SVGProps<SVGSVGElement>>

interface NavItem {
  to: string
  label: string
  Icon: IconType
  /** Match this prefix for active state instead of exact match (e.g. Admin). */
  matchPrefix?: string
}

const NAV_ITEMS: NavItem[] = [
  { to: '/dashboard', label: 'Dashboard', Icon: DashboardIcon },
  { to: '/keys', label: 'API Keys', Icon: KeyIcon },
  { to: '/billing', label: 'Billing', Icon: BillingIcon },
  { to: '/profile', label: 'Profile', Icon: ProfileIcon },
  { to: '/settings', label: 'Settings', Icon: SettingsIcon },
]

const ADMIN_ITEM: NavItem = {
  to: '/admin/transactions',
  label: 'Admin',
  Icon: AdminIcon,
  matchPrefix: '/admin',
}

const TIER_CHROME: Record<PlanTier, string> = {
  free: 'bg-cream/20 text-cream',
  pro: 'bg-gold text-ink',
  max: 'bg-purple text-paper',
}

const ROW_BASE =
  'flex items-center gap-3 rounded-control py-2 text-sm font-bold transition-colors duration-100 focus-visible:outline-[3px] focus-visible:outline-gold focus-visible:outline-offset-2'
const ROW_INACTIVE = 'text-cream/75 hover:bg-paper/10 hover:text-paper'
const ROW_ACTIVE = 'bg-paper/10 text-gold'

interface SidebarProps {
  user: User | null
  collapsed: boolean
  onLogout: () => void
  /** Called when a nav link is activated — used to close the mobile drawer. */
  onNavigate?: () => void
  /** Desktop only: toggle the icon-rail. Omitted in the mobile drawer. */
  onToggleCollapse?: () => void
  /** Mobile drawer only: render an X to dismiss the overlay. */
  onClose?: () => void
}

function Row({
  item,
  collapsed,
  isActive,
  onNavigate,
}: {
  item: NavItem
  collapsed: boolean
  isActive?: (pathnameActive: boolean) => boolean
  onNavigate?: () => void
}) {
  const { to, label, Icon } = item
  return (
    <NavLink
      to={to}
      onClick={onNavigate}
      title={collapsed ? label : undefined}
      className={({ isActive: exact }) => {
        const active = isActive ? isActive(exact) : exact
        return `${ROW_BASE} ${collapsed ? 'justify-center px-2' : 'px-3'} ${active ? ROW_ACTIVE : ROW_INACTIVE}`
      }}
    >
      <Icon className="size-5 shrink-0" />
      {!collapsed && <span className="truncate">{label}</span>}
    </NavLink>
  )
}

export default function Sidebar({
  user,
  collapsed,
  onLogout,
  onNavigate,
  onToggleCollapse,
  onClose,
}: SidebarProps) {
  return (
    <div className="flex h-full flex-col bg-ink text-paper">
      {/* Brand */}
      <div className={`flex h-14 items-center ${collapsed ? 'justify-center px-2' : 'justify-between px-4'}`}>
        <Link
          to="/dashboard"
          onClick={onNavigate}
          className="flex items-center gap-2 font-display font-extrabold tracking-tight text-paper focus-visible:outline-[3px] focus-visible:outline-gold focus-visible:outline-offset-2"
        >
          {collapsed ? (
            <span className="grid size-9 place-items-center rounded-control bg-gold text-base text-ink">AB</span>
          ) : (
            <span className="text-lg">API Builder</span>
          )}
        </Link>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close menu"
            className="rounded-control p-1.5 text-cream/75 hover:bg-paper/10 hover:text-paper focus-visible:outline-[3px] focus-visible:outline-gold focus-visible:outline-offset-2"
          >
            <CloseIcon className="size-5" />
          </button>
        )}
      </div>

      {/* Nav */}
      <nav className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 py-3">
        {NAV_ITEMS.map((item) => (
          <Row key={item.to} item={item} collapsed={collapsed} onNavigate={onNavigate} />
        ))}
        {user?.role === 'super_admin' && (
          <Row
            item={ADMIN_ITEM}
            collapsed={collapsed}
            onNavigate={onNavigate}
            isActive={() => window.location.pathname.startsWith('/admin')}
          />
        )}
      </nav>

      {/* Footer: user + logout + collapse toggle */}
      <div className="border-t border-paper/10 px-2 py-3">
        {user && !collapsed && (
          <div className="mb-2 flex items-center gap-2 px-1">
            <span className="min-w-0 flex-1 truncate text-xs text-cream/60">{user.email}</span>
            <span
              className={`inline-flex shrink-0 items-center rounded-pill px-2.5 py-0.5 text-label uppercase ${TIER_CHROME[user.tier]}`}
            >
              {user.tier}
            </span>
          </div>
        )}
        <button
          type="button"
          onClick={onLogout}
          title={collapsed ? 'Log out' : undefined}
          className={`w-full ${ROW_BASE} ${collapsed ? 'justify-center px-2' : 'px-3'} ${ROW_INACTIVE}`}
        >
          <LogoutIcon className="size-5 shrink-0" />
          {!collapsed && <span>Log out</span>}
        </button>
        {onToggleCollapse && (
          <button
            type="button"
            onClick={onToggleCollapse}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className={`mt-1 w-full ${ROW_BASE} ${collapsed ? 'justify-center px-2' : 'px-3'} ${ROW_INACTIVE}`}
          >
            <ChevronLeftIcon className={`size-5 shrink-0 transition-transform duration-200 ${collapsed ? 'rotate-180' : ''}`} />
            {!collapsed && <span>Collapse</span>}
          </button>
        )}
      </div>
    </div>
  )
}

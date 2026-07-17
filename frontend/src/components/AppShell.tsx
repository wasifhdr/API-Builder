import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { useSession } from '../hooks/useSession'
import Sidebar from './Sidebar'
import { MenuIcon } from './nav-icons'

const COLLAPSE_KEY = 'apibuilder.sidebarCollapsed'

export default function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useSession()
  const [collapsed, setCollapsed] = useState<boolean>(
    () => typeof window !== 'undefined' && localStorage.getItem(COLLAPSE_KEY) === 'true',
  )
  const [mobileOpen, setMobileOpen] = useState(false)

  function toggleCollapse() {
    setCollapsed((c) => {
      const next = !c
      localStorage.setItem(COLLAPSE_KEY, String(next))
      return next
    })
  }

  const closeMobile = () => setMobileOpen(false)

  // Close the mobile drawer if the viewport grows to desktop width.
  useEffect(() => {
    const mq = window.matchMedia('(min-width: 768px)')
    const onChange = (e: MediaQueryListEvent) => e.matches && setMobileOpen(false)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  return (
    <div className="min-h-screen bg-cream">
      {/* Mobile top strip */}
      <div className="sticky top-0 z-40 flex h-14 items-center gap-3 bg-ink px-4 text-paper md:hidden">
        <button
          type="button"
          onClick={() => setMobileOpen(true)}
          aria-label="Open menu"
          className="rounded-control p-1.5 text-cream/75 hover:bg-paper/10 hover:text-paper focus-visible:outline-[3px] focus-visible:outline-gold focus-visible:outline-offset-2"
        >
          <MenuIcon className="size-6" />
        </button>
        <Link to="/dashboard" className="font-display text-lg font-extrabold tracking-tight text-paper">
          API Builder
        </Link>
      </div>

      {/* Desktop sidebar (fixed) */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 hidden transition-[width] duration-200 md:block ${collapsed ? 'w-16' : 'w-60'}`}
      >
        <Sidebar
          user={user}
          collapsed={collapsed}
          onLogout={logout}
          onToggleCollapse={toggleCollapse}
        />
      </aside>

      {/* Mobile drawer overlay */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div className="absolute inset-0 bg-ink/60" onClick={closeMobile} aria-hidden />
          <aside className="absolute inset-y-0 left-0 w-64 shadow-offset">
            <Sidebar
              user={user}
              collapsed={false}
              onLogout={logout}
              onNavigate={closeMobile}
              onClose={closeMobile}
            />
          </aside>
        </div>
      )}

      {/* Content */}
      <div className={`transition-[padding] duration-200 ${collapsed ? 'md:pl-16' : 'md:pl-60'}`}>
        <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
      </div>
    </div>
  )
}

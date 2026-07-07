import { NavLink } from 'react-router-dom'

const TABS = [
  { to: '/admin/overview', label: 'Overview' },
  { to: '/admin/users', label: 'Users' },
  { to: '/admin/controls', label: 'Admin Controls' },
  { to: '/admin/apis', label: 'APIs' },
  { to: '/admin/plans', label: 'Plans' },
  { to: '/admin/transactions', label: 'Transactions' },
  { to: '/admin/sms', label: 'SMS feed' },
  { to: '/admin/audit', label: 'Audit log' },
]

export default function AdminNav() {
  return (
    <nav className="mb-6 flex flex-wrap gap-2">
      {TABS.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          className={({ isActive }) =>
            `rounded-pill px-3 py-1.5 text-sm font-bold transition-colors duration-100 focus-visible:outline-[3px] focus-visible:outline-ink focus-visible:outline-offset-2 ${
              isActive ? 'bg-ink text-paper' : 'text-ink/70 hover:bg-cream'
            }`
          }
        >
          {t.label}
        </NavLink>
      ))}
    </nav>
  )
}

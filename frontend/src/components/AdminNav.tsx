import { NavLink } from 'react-router-dom'

const TABS = [
  { to: '/admin/transactions', label: 'Transactions' },
  { to: '/admin/sms', label: 'SMS feed' },
  { to: '/admin/users', label: 'Users' },
]

export default function AdminNav() {
  return (
    <nav className="flex gap-4 border-b border-gray-200 px-6">
      {TABS.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          className={({ isActive }) =>
            `py-3 text-sm border-b-2 ${
              isActive ? 'border-gray-900 text-gray-900 font-medium' : 'border-transparent text-gray-500 hover:text-gray-700'
            }`
          }
        >
          {t.label}
        </NavLink>
      ))}
    </nav>
  )
}

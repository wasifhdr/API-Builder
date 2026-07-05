import { Link } from 'react-router-dom'
import { useSession } from '../hooks/useSession'

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
        <div className="flex items-center gap-3 mb-6">
          {user.picture_url && (
            <img src={user.picture_url} alt="" className="w-10 h-10 rounded-full" />
          )}
          <div>
            <p className="font-medium text-gray-900">{user.name ?? user.email}</p>
            <p className="text-sm text-gray-500">
              {user.email} · <span className="uppercase text-xs font-semibold">{user.role}</span>
            </p>
          </div>
        </div>
        <p className="text-gray-500 text-sm">No APIs yet — recording comes in a later phase.</p>
      </main>
    </div>
  )
}

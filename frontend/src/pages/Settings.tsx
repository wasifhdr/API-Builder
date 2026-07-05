import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useSession } from '../hooks/useSession'
import { api } from '../lib/api'
import type { User } from '../lib/types'

export default function Settings() {
  const { user, refetch } = useSession()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!user) return null

  async function saveSettings(patch: Partial<User['settings']>) {
    setSaving(true)
    setError(null)
    try {
      await api.patch<User>('/me/settings', patch)
      await refetch()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save settings')
    } finally {
      setSaving(false)
    }
  }

  const useSavedLogins = user.settings.use_saved_logins ?? false
  const recorderChannel = user.settings.recorder_channel ?? 'chromium'

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4 flex items-center gap-4">
        <Link to="/dashboard" className="text-sm text-gray-600 hover:text-gray-900">
          &larr; Dashboard
        </Link>
        <h1 className="text-lg font-semibold text-gray-900">Settings</h1>
      </header>
      <main className="p-6 max-w-lg space-y-8">
        {error && <p className="text-red-600 text-sm">{error}</p>}

        <section>
          <label className="flex items-start gap-3">
            <input
              type="checkbox"
              className="mt-1"
              checked={useSavedLogins}
              disabled={saving}
              onChange={(e) => saveSettings({ use_saved_logins: e.target.checked })}
            />
            <span>
              <span className="block font-medium text-gray-900">
                Use saved logins for recording
              </span>
              <span className="block text-sm text-gray-500 mt-1">
                When on, the recorder reuses a persistent browser profile that stays logged into
                sites you sign into during recording, so you don't have to log in every time. Auth
                is captured per workflow when you save it, and can be refreshed later if it
                expires.
              </span>
            </span>
          </label>
        </section>

        <section>
          <span className="block font-medium text-gray-900 mb-2">Browser for recording</span>
          <div className="flex flex-col gap-2 text-sm text-gray-700">
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="recorder_channel"
                checked={recorderChannel === 'chromium'}
                disabled={saving}
                onChange={() => saveSettings({ recorder_channel: 'chromium' })}
              />
              Bundled Chromium (default)
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="recorder_channel"
                checked={recorderChannel === 'chrome'}
                disabled={saving}
                onChange={() => saveSettings({ recorder_channel: 'chrome' })}
              />
              Your installed Chrome (lets you sign into Chrome Sync for real password autofill)
            </label>
          </div>
        </section>

        <section className="opacity-50">
          <label
            className="flex items-start gap-3"
            title="Chrome 127+ encrypts cookies so only Chrome itself can read them, and Chrome 136+ blocks automation from touching your default profile. There's no way around this from an outside app — use 'saved logins' above instead."
          >
            <input type="checkbox" className="mt-1" disabled />
            <span>
              <span className="block font-medium text-gray-900">
                Import cookies directly from my regular Chrome profile
              </span>
              <span className="block text-sm text-gray-500 mt-1">
                Experimental — usually blocked by Chrome 127+ (encrypted cookies) and Chrome 136+
                (blocks automation on your default profile).
              </span>
            </span>
          </label>
        </section>
      </main>
    </div>
  )
}

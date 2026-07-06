import { useState } from 'react'
import { Link } from 'react-router-dom'
import AppShell from '../components/AppShell'
import { buttonClasses, CapsLabel, cardClasses, Checkbox, PageHeader, Radio } from '../components/ui'
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
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader eyebrow={<CapsLabel>Account</CapsLabel>} title="Settings" />

      {error && <p className="mb-6 text-sm font-medium text-red-deep">{error}</p>}

      <div className="max-w-lg space-y-6">
        <section className={cardClasses({ variant: 'quiet' })}>
          <label className="flex items-start gap-3">
            <Checkbox
              className="mt-1"
              checked={useSavedLogins}
              disabled={saving}
              onChange={(e) => saveSettings({ use_saved_logins: e.target.checked })}
            />
            <span>
              <span className="block font-bold">Use saved logins for recording</span>
              <span className="mt-1 block text-sm text-ink/70">
                When on, the recorder reuses a persistent browser profile that stays logged into
                sites you sign into during recording, so you don&apos;t have to log in every time.
                Auth is captured per workflow when you save it, and can be refreshed later if it
                expires.
              </span>
            </span>
          </label>
        </section>

        <section className={cardClasses({ variant: 'quiet' })}>
          <span className="mb-2 block font-bold">Browser for recording</span>
          <div className="flex flex-col gap-2 text-sm">
            <label className="flex items-center gap-2">
              <Radio
                name="recorder_channel"
                checked={recorderChannel === 'chromium'}
                disabled={saving}
                onChange={() => saveSettings({ recorder_channel: 'chromium' })}
              />
              Bundled Chromium (default)
            </label>
            <label className="flex items-center gap-2">
              <Radio
                name="recorder_channel"
                checked={recorderChannel === 'chrome'}
                disabled={saving}
                onChange={() => saveSettings({ recorder_channel: 'chrome' })}
              />
              Your installed Chrome (lets you sign into Chrome Sync for real password autofill)
            </label>
          </div>
        </section>

        <section className={`${cardClasses({ variant: 'quiet' })} opacity-50`}>
          <label
            className="flex items-start gap-3"
            title="Chrome 127+ encrypts cookies so only Chrome itself can read them, and Chrome 136+ blocks automation from touching your default profile. There's no way around this from an outside app — use 'saved logins' above instead."
          >
            <Checkbox className="mt-1" disabled />
            <span>
              <span className="block font-bold">Import cookies directly from my regular Chrome profile</span>
              <span className="mt-1 block text-sm text-ink/70">
                Experimental — usually blocked by Chrome 127+ (encrypted cookies) and Chrome 136+
                (blocks automation on your default profile).
              </span>
            </span>
          </label>
        </section>
      </div>
    </AppShell>
  )
}

import { useState, type FormEvent } from 'react'
import { Button, Card, FieldError, FieldHelp, FieldLabel, Input } from '../components/ui'
import { useSession } from '../hooks/useSession'
import { ApiError, api } from '../lib/api'

const USERNAME_PATTERN = /^[a-z0-9_]{3,30}$/

type Availability = 'idle' | 'checking' | 'available' | 'taken' | 'invalid'

export default function ClaimUsername() {
  const { user, refetch } = useSession()
  const [username, setUsername] = useState('')
  const [availability, setAvailability] = useState<Availability>('idle')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  if (!user) return null

  async function checkAvailability(candidate: string) {
    setAvailability('checking')
    try {
      const res = await api.get<{ available: boolean }>(
        `/auth/username-available?username=${encodeURIComponent(candidate)}`,
      )
      setAvailability(res.available ? 'available' : 'taken')
    } catch {
      setAvailability('idle')
    }
  }

  function handleBlur() {
    const candidate = username.trim().toLowerCase()
    setUsername(candidate)
    if (!candidate) {
      setAvailability('idle')
      return
    }
    if (!USERNAME_PATTERN.test(candidate)) {
      setAvailability('invalid')
      return
    }
    void checkAvailability(candidate)
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (availability === 'taken' || availability === 'invalid') return
    setError(null)
    setSubmitting(true)
    try {
      await api.post('/auth/claim-username', { username })
      await refetch()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to claim username')
    } finally {
      setSubmitting(false)
    }
  }

  const blocked = availability === 'taken' || availability === 'invalid'

  return (
    <div className="grid min-h-screen place-items-center bg-cream px-4">
      <Card variant="feature" className="w-full max-w-sm">
        <p className="mb-1 text-label uppercase text-orange-deep">One more step</p>
        <h1 className="font-display text-display-sm mb-3">Claim your username</h1>
        <p className="mb-5 text-sm text-ink/70">
          Signed in as <span className="font-bold">{user.email}</span>. Pick a username to finish
          setting up your account — usernames are permanent and can&apos;t be changed later.
        </p>

        <form onSubmit={handleSubmit}>
          <FieldLabel htmlFor="claim-username">Username</FieldLabel>
          <Input
            id="claim-username"
            value={username}
            onChange={(e) => {
              setUsername(e.target.value)
              setAvailability('idle')
            }}
            onBlur={handleBlur}
            error={blocked}
            placeholder="lowercase, numbers, underscores"
            autoComplete="off"
            autoFocus
          />
          {availability === 'checking' && <FieldHelp>Checking availability…</FieldHelp>}
          {availability === 'available' && <FieldHelp>That username is available.</FieldHelp>}
          {availability === 'taken' && <FieldError>That username is already taken.</FieldError>}
          {availability === 'invalid' && (
            <FieldError>3-30 characters: lowercase letters, numbers, underscores.</FieldError>
          )}

          {error && <p className="mt-3 text-sm font-medium text-red-deep">{error}</p>}

          <Button
            type="submit"
            variant="primary"
            disabled={submitting || blocked || !username}
            className="mt-5 w-full justify-center"
          >
            {submitting ? 'Saving…' : 'Claim username'}
          </Button>
        </form>
      </Card>
    </div>
  )
}

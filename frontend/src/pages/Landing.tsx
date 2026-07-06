import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import {
  Button,
  buttonClasses,
  Card,
  FieldError,
  FieldHelp,
  FieldLabel,
  Input,
  PageLoading,
} from '../components/ui'
import type { Accent } from '../components/ui'
import { useSession } from '../hooks/useSession'
import { ApiError, api } from '../lib/api'

const FEATURES: { title: string; description: string; accent: Accent }[] = [
  {
    title: 'Record once',
    description:
      'Browse a site normally in a real browser — every click, fill, and navigation is captured automatically.',
    accent: 'green',
  },
  {
    title: 'Mark what matters',
    description:
      'Pick the data on the page you want back, and turn any typed value into a reusable parameter.',
    accent: 'orange',
  },
  {
    title: 'Publish an API',
    description:
      'Get a parameterized JSON endpoint with an auto-generated OpenAPI spec, ready to call or share.',
    accent: 'blue',
  },
]

const USERNAME_PATTERN = /^[a-z0-9_]{3,30}$/

type AuthMode = 'login' | 'register'
type Availability = 'idle' | 'checking' | 'available' | 'taken' | 'invalid'

function AuthCard() {
  const { login, register } = useSession()
  const [mode, setMode] = useState<AuthMode>('login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [availability, setAvailability] = useState<Availability>('idle')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  function switchMode(next: AuthMode) {
    setMode(next)
    setError(null)
    setAvailability('idle')
  }

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

  function handleUsernameBlur() {
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

  async function handleLogin(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email.trim().toLowerCase(), password)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to sign in')
    } finally {
      setSubmitting(false)
    }
  }

  async function handleRegister(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (availability === 'taken' || availability === 'invalid') return
    if (password !== confirmPassword) {
      setError('Passwords do not match')
      return
    }
    setSubmitting(true)
    try {
      await register({
        name: name.trim(),
        email: email.trim().toLowerCase(),
        username: username.trim().toLowerCase(),
        password,
      })
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to create account')
    } finally {
      setSubmitting(false)
    }
  }

  const usernameBlocked = availability === 'taken' || availability === 'invalid'

  return (
    <Card variant="feature" className="mx-auto mt-10 w-full max-w-sm text-left">
      <h2 className="text-h2 mb-4 text-center">
        {mode === 'login' ? 'Sign in' : 'Create your account'}
      </h2>

      <form onSubmit={mode === 'login' ? handleLogin : handleRegister} className="space-y-4">
        {mode === 'register' && (
          <div>
            <FieldLabel htmlFor="reg-name">Name</FieldLabel>
            <Input
              id="reg-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="name"
              required
            />
          </div>
        )}

        <div>
          <FieldLabel htmlFor="auth-email">Email</FieldLabel>
          <Input
            id="auth-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            required
          />
        </div>

        {mode === 'register' && (
          <div>
            <FieldLabel htmlFor="reg-username">Username</FieldLabel>
            <Input
              id="reg-username"
              value={username}
              onChange={(e) => {
                setUsername(e.target.value)
                setAvailability('idle')
              }}
              onBlur={handleUsernameBlur}
              error={usernameBlocked}
              placeholder="lowercase, numbers, underscores"
              autoComplete="off"
              required
            />
            {availability === 'checking' && <FieldHelp>Checking availability…</FieldHelp>}
            {availability === 'available' && <FieldHelp>That username is available.</FieldHelp>}
            {availability === 'taken' && <FieldError>That username is already taken.</FieldError>}
            {availability === 'invalid' && (
              <FieldError>3-30 characters: lowercase letters, numbers, underscores.</FieldError>
            )}
          </div>
        )}

        <div>
          <FieldLabel htmlFor="auth-password">Password</FieldLabel>
          <Input
            id="auth-password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            required
          />
          {mode === 'register' && <FieldHelp>At least 8 characters.</FieldHelp>}
        </div>

        {mode === 'register' && (
          <div>
            <FieldLabel htmlFor="reg-confirm-password">Confirm password</FieldLabel>
            <Input
              id="reg-confirm-password"
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              autoComplete="new-password"
              required
            />
          </div>
        )}

        {error && <p className="text-sm font-medium text-red-deep">{error}</p>}

        <Button type="submit" variant="primary" disabled={submitting} className="w-full justify-center">
          {submitting ? 'Please wait…' : mode === 'login' ? 'Sign in' : 'Create account'}
        </Button>
      </form>

      <div className="my-5 flex items-center gap-3 text-xs uppercase text-ink/40">
        <span className="h-px flex-1 bg-sand" />
        or
        <span className="h-px flex-1 bg-sand" />
      </div>

      <a href="/api/auth/login" className={buttonClasses('default', 'md', 'w-full justify-center')}>
        Sign in with Google
      </a>

      <button
        type="button"
        onClick={() => switchMode(mode === 'login' ? 'register' : 'login')}
        className={buttonClasses('ghost', 'sm', 'mt-4 w-full justify-center')}
      >
        {mode === 'login' ? 'New here? Create an Account!' : 'Already have an account? Sign in'}
      </button>
    </Card>
  )
}

export default function Landing() {
  const { user, loading } = useSession()

  if (loading) return <PageLoading />
  if (user) return <Navigate to="/dashboard" replace />

  return (
    <div className="min-h-screen bg-cream">
      <section className="bg-dotgrid px-6 py-24">
        <div className="mx-auto max-w-3xl text-center">
          <p className="mb-3 text-label uppercase text-orange-deep">API Builder</p>
          <h1 className="font-display text-display">
            Turn any website <span className="text-orange">into an API.</span>
          </h1>
          <p className="mx-auto mt-6 max-w-xl text-lg text-ink/70">
            Record your browser session, mark the data you want, and republish the workflow as a
            parameterized JSON HTTP API — no scraping code to write.
          </p>
        </div>

        <AuthCard />
      </section>

      <section className="mx-auto max-w-5xl px-6 pb-24">
        <div className="grid gap-5 md:grid-cols-3">
          {FEATURES.map((f) => (
            <Card key={f.title} variant="standard" accent={f.accent}>
              <h2 className="text-h2">{f.title}</h2>
              <p className="mt-2 text-sm text-ink/70">{f.description}</p>
            </Card>
          ))}
        </div>
      </section>

      <footer className="bg-ink px-6 py-8 text-cream">
        <div className="mx-auto flex max-w-5xl items-center justify-between">
          <span className="font-display text-lg font-extrabold">API Builder</span>
          <span className="text-xs text-cream/60">Record, extract, publish.</span>
        </div>
      </footer>
    </div>
  )
}

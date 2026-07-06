import { type FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import AppShell from '../components/AppShell'
import { Button, buttonClasses, CapsLabel, cardClasses, FieldLabel, Input } from '../components/ui'
import { api, ApiError } from '../lib/api'

export default function RecorderStart() {
  const navigate = useNavigate()
  const [name, setName] = useState('')
  const [startUrl, setStartUrl] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  function normalizeUrl(value: string): string {
    const trimmed = value.trim()
    return trimmed && !/^https?:\/\//i.test(trimmed) ? `https://${trimmed}` : trimmed
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    const normalizedUrl = normalizeUrl(startUrl)
    try {
      new URL(normalizedUrl)
    } catch {
      setError('Please enter a valid URL')
      return
    }
    setStartUrl(normalizedUrl)

    setSubmitting(true)
    try {
      const { workflow_id } = await api.post<{ workflow_id: string }>('/recordings', {
        name,
        start_url: normalizedUrl,
      })
      navigate(`/recorder/${workflow_id}`)
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError('Daily API creation limit reached. Upgrade your plan or try again tomorrow.')
      } else {
        setError(err instanceof Error ? err.message : 'Failed to start recording')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <div className="mx-auto max-w-md">
        <div className={cardClasses({ variant: 'feature' })}>
          <CapsLabel className="mb-1">New recording</CapsLabel>
          <h1 className="text-h1 mb-4">Record a workflow</h1>

          <form onSubmit={handleSubmit} className="space-y-4">
            {error && <p className="text-sm font-medium text-red-deep">{error}</p>}
            <div>
              <FieldLabel htmlFor="wf-name">Name</FieldLabel>
              <Input
                id="wf-name"
                type="text"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Rokomari book search"
              />
            </div>
            <div>
              <FieldLabel htmlFor="wf-url">Starting URL</FieldLabel>
              <Input
                id="wf-url"
                type="text"
                required
                value={startUrl}
                onChange={(e) => setStartUrl(e.target.value)}
                onBlur={() => setStartUrl((v) => normalizeUrl(v))}
                placeholder="https://example.com"
              />
            </div>
            <Button type="submit" variant="primary" disabled={submitting} className="w-full justify-center">
              {submitting ? 'Starting…' : 'Start recording'}
            </Button>
          </form>
        </div>

        <div className={`${cardClasses({ variant: 'callout', accent: 'gold' })} mt-6`}>
          <CapsLabel tone="gold" className="mb-1">
            Heads up
          </CapsLabel>
          <p className="text-sm text-ink/80">
            A real Chromium window will open on this machine&apos;s desktop. Browse the site there —
            every click, fill, and navigation is recorded live on the next screen.
          </p>
        </div>
      </div>
    </AppShell>
  )
}

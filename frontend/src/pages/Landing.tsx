import { Navigate } from 'react-router-dom'
import { useSession } from '../hooks/useSession'
import { buttonClasses, Card, PageLoading } from '../components/ui'
import type { Accent } from '../components/ui'

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
          <a href="/api/auth/login" className={buttonClasses('primary', 'md', 'mt-8 inline-flex')}>
            Sign in with Google
          </a>
        </div>
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

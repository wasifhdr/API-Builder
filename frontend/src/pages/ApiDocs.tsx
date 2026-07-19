import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import ApiDocView, { type OpenApiSpec } from '../components/ApiDocView'
import { buttonClasses, EmptyState, Spinner } from '../components/ui'

type SpecStatus = 'pending' | 'generating' | 'ready' | 'failed'

interface DocResponse {
  name: string
  slug: string
  status: SpecStatus
  spec: OpenApiSpec | null
}

const POLL_MS = 2500
const MAX_POLLS = 80 // ~3.3 min ceiling; generation settles well before this

export default function ApiDocs() {
  const { slug } = useParams<{ slug: string }>()
  const [doc, setDoc] = useState<DocResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)

  const fetchDoc = useCallback(async (): Promise<DocResponse> => {
    const res = await fetch(`/v1/apis/${slug}/doc`, { credentials: 'same-origin' })
    if (!res.ok) {
      const message =
        res.status === 403
          ? 'You do not have access to these docs.'
          : res.status === 401
            ? 'Please sign in to view these docs.'
            : 'These docs could not be found.'
      throw new Error(message)
    }
    return (await res.json()) as DocResponse
  }, [slug])

  useEffect(() => {
    mounted.current = true
    let attempts = 0
    const load = async () => {
      try {
        const d = await fetchDoc()
        if (!mounted.current) return
        setDoc(d)
        setError(null)
        if ((d.status === 'pending' || d.status === 'generating') && attempts < MAX_POLLS) {
          attempts++
          window.setTimeout(load, POLL_MS)
        }
      } catch (e) {
        if (!mounted.current) return
        setError(e instanceof Error ? e.message : 'Failed to load docs')
      }
    }
    load()
    return () => {
      mounted.current = false
    }
  }, [fetchDoc])

  const generating = doc && (doc.status === 'pending' || doc.status === 'generating')

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>

      {error && (
        <EmptyState statement={<>Couldn&apos;t load docs.</>} description={error} />
      )}

      {!error && !doc && (
        <div className="flex items-center gap-3 py-16 text-ink/70">
          <Spinner className="size-5" /> Loading…
        </div>
      )}

      {!error && generating && (
        <div className="flex flex-col items-center justify-center gap-4 py-24 text-center">
          <Spinner className="size-8" />
          <div>
            <p className="font-display text-h2">Generating Docs</p>
            <p className="mt-1 text-sm text-ink/60">
              The AI is writing the documentation — this usually takes a few seconds.
            </p>
          </div>
        </div>
      )}

      {!error && doc && !generating && doc.spec && <ApiDocView spec={doc.spec} slug={doc.slug} />}

      {!error && doc && !generating && !doc.spec && (
        <EmptyState
          statement={
            <>
              Docs generation <span className="text-orange">failed.</span>
            </>
          }
          description="Open the API and click Regenerate docs to try again."
        />
      )}
    </AppShell>
  )
}

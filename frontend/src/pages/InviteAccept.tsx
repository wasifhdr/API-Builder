import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Badge, Button, buttonClasses, Card, PageLoading } from '../components/ui'
import { useSession } from '../hooks/useSession'
import { ApiError, api } from '../lib/api'
import type { AcceptInviteResult, InvitePreview } from '../lib/types'

export default function InviteAccept() {
  const { token } = useParams<{ token: string }>()
  const { user, loading } = useSession()
  const navigate = useNavigate()
  const [preview, setPreview] = useState<InvitePreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AcceptInviteResult | null>(null)
  const [accepting, setAccepting] = useState(false)

  useEffect(() => {
    api
      .get<InvitePreview>(`/invites/${token}`)
      .then(setPreview)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load invite'))
  }, [token])

  async function accept() {
    setAccepting(true)
    setError(null)
    try {
      const res = await api.post<AcceptInviteResult>(`/invites/${token}/accept`)
      setResult(res)
      if (res.status === 'payment_required') {
        navigate('/billing')
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to accept invite')
    } finally {
      setAccepting(false)
    }
  }

  if (loading || (!preview && !error)) return <PageLoading />

  return (
    <div className="grid min-h-screen place-items-center bg-cream px-4">
      <Card variant="feature" className="w-full max-w-sm text-center">
        <p className="mb-1 text-label uppercase text-orange-deep">Invite</p>
        <h1 className="font-display text-display-sm mb-4">API access</h1>

        {error && <p className="mb-3 text-sm font-medium text-red-deep">{error}</p>}

        {preview && (
          <>
            <p className="mb-4 text-ink/80">
              You&apos;ve been invited to use <span className="font-bold">{preview.api_name}</span>
              {preview.price_bdt && (
                <>
                  {' '}
                  for <span className="font-mono font-bold">৳{preview.price_bdt}</span>
                </>
              )}
              .
            </p>

            {!preview.valid && <p className="mb-3 text-sm font-medium text-red-deep">{preview.reason}</p>}

            {preview.valid && !result && (
              <>
                {!user ? (
                  <a href="/api/auth/login" className={buttonClasses('primary', 'md', 'w-full justify-center')}>
                    Sign in with Google to accept
                  </a>
                ) : (
                  <Button variant="primary" onClick={accept} disabled={accepting} className="w-full justify-center">
                    {accepting ? 'Accepting…' : 'Accept invite'}
                  </Button>
                )}
              </>
            )}

            {result?.status === 'granted' && (
              <div className="space-y-3">
                <Badge variant="success">Access granted</Badge>
                <Link to="/keys" className="block text-sm font-bold text-orange-deep hover:text-orange">
                  Create an API key &rarr;
                </Link>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  )
}

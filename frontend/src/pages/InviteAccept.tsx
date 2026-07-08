import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Badge, Button, buttonClasses, Card, PageLoading } from '../components/ui'
import { useSession } from '../hooks/useSession'
import { ApiError, api } from '../lib/api'
import type { AcceptInviteResult, InvitePreview, Wallet } from '../lib/types'

export default function InviteAccept() {
  const { token } = useParams<{ token: string }>()
  const { user, loading } = useSession()
  const [preview, setPreview] = useState<InvitePreview | null>(null)
  const [wallet, setWallet] = useState<Wallet | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<AcceptInviteResult | null>(null)
  const [accepting, setAccepting] = useState(false)

  useEffect(() => {
    api
      .get<InvitePreview>(`/invites/${token}`)
      .then(setPreview)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load invite'))
  }, [token])

  useEffect(() => {
    if (!user) return
    api.get<Wallet>('/billing/wallet').then(setWallet).catch(() => undefined)
  }, [user])

  async function accept() {
    setAccepting(true)
    setError(null)
    try {
      const res = await api.post<AcceptInviteResult>(`/invites/${token}/accept`)
      setResult(res)
      if (res.status === 'insufficient_balance') {
        setWallet((prev) => (prev ? { ...prev, balance_bdt: res.balance_bdt ?? prev.balance_bdt } : prev))
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to accept invite')
    } finally {
      setAccepting(false)
    }
  }

  if (loading || (!preview && !error)) return <PageLoading />

  const price = preview?.price_bdt ? Number(preview.price_bdt) : 0
  const balance = wallet ? Number(wallet.balance_bdt) : null
  // Treat "wallet not loaded yet" as optimistically affordable — accept()
  // asks the server for the real answer, which is the actual source of truth.
  const canAfford = price === 0 || balance === null || balance >= price
  const shortfall = balance !== null ? Math.max(price - balance, 0) : null

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
                  {preview.pricing_mode === 'subscription' && '/mo'}
                </>
              )}
              .
              {preview.pricing_mode === 'subscription' && (
                <span className="block text-xs text-ink/60">Renews monthly — pay again anytime to extend.</span>
              )}
            </p>

            {!preview.valid && <p className="mb-3 text-sm font-medium text-red-deep">{preview.reason}</p>}

            {preview.valid && !result && (
              <>
                {!user ? (
                  <div className="space-y-2">
                    <a
                      href={`/api/auth/login?invite_token=${token}`}
                      className={buttonClasses('primary', 'md', 'w-full justify-center')}
                    >
                      Sign in with Google to accept
                    </a>
                    <Link
                      to={`/?invite=${token}`}
                      className="block text-sm font-medium text-orange-deep hover:text-orange"
                    >
                      Sign in or create an account with email
                    </Link>
                  </div>
                ) : (
                  <>
                    {price > 0 && wallet && (
                      <p className="mb-3 text-sm text-ink/70">
                        This API costs <span className="font-mono font-bold">৳{preview.price_bdt}</span>
                        {preview.pricing_mode === 'subscription' && '/mo'} · your balance{' '}
                        <span className="font-mono font-bold">৳{wallet.balance_bdt}</span>
                      </p>
                    )}
                    {canAfford ? (
                      <Button
                        variant="primary"
                        onClick={accept}
                        disabled={accepting}
                        className="w-full justify-center"
                      >
                        {accepting
                          ? 'Accepting…'
                          : preview.pricing_mode === 'subscription'
                            ? 'Subscribe from wallet'
                            : price > 0
                              ? 'Pay from wallet'
                              : 'Accept invite'}
                      </Button>
                    ) : (
                      <Link
                        to="/billing"
                        className={buttonClasses('primary', 'md', 'w-full justify-center')}
                      >
                        Add ৳{shortfall} to continue
                      </Link>
                    )}
                  </>
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

            {result?.status === 'insufficient_balance' && (
              <div className="space-y-3">
                <p className="text-sm text-ink/70">
                  Your balance <span className="font-mono font-bold">৳{result.balance_bdt}</span> isn&apos;t
                  enough — this API costs <span className="font-mono font-bold">৳{result.price_bdt}</span>.
                </p>
                <Link to="/billing" className={buttonClasses('primary', 'md', 'w-full justify-center')}>
                  Top up to continue
                </Link>
              </div>
            )}
          </>
        )}
      </Card>
    </div>
  )
}

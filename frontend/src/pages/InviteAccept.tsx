import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
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

  if (loading || (!preview && !error)) {
    return <div className="min-h-screen flex items-center justify-center bg-white text-gray-500">Loading…</div>
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-white px-4">
      <div className="max-w-sm w-full text-center space-y-4">
        <h1 className="text-xl font-semibold text-gray-900">API invite</h1>

        {error && <p className="text-red-600 text-sm">{error}</p>}

        {preview && (
          <>
            <p className="text-gray-700">
              You've been invited to use <span className="font-semibold">{preview.api_name}</span>
              {preview.price_bdt && <> for <span className="font-semibold">৳{preview.price_bdt}</span></>}.
            </p>

            {!preview.valid && <p className="text-red-600 text-sm">{preview.reason}</p>}

            {preview.valid && !result && (
              <>
                {!user ? (
                  <a
                    href="/api/auth/login"
                    className="inline-block rounded-md bg-gray-900 px-5 py-2.5 text-white font-medium hover:bg-gray-800"
                  >
                    Sign in with Google to accept
                  </a>
                ) : (
                  <button
                    type="button"
                    onClick={accept}
                    disabled={accepting}
                    className="rounded-md bg-gray-900 px-5 py-2.5 text-white font-medium hover:bg-gray-800 disabled:opacity-50"
                  >
                    {accepting ? 'Accepting…' : 'Accept invite'}
                  </button>
                )}
              </>
            )}

            {result?.status === 'granted' && (
              <div className="space-y-2">
                <p className="text-green-600 text-sm">Access granted!</p>
                <Link to="/keys" className="text-sm text-blue-600 hover:text-blue-800">
                  Create an API key &rarr;
                </Link>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

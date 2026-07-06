import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useSession } from '../hooks/useSession'
import { ApiError, api } from '../lib/api'
import type { PaymentIntent, Plan } from '../lib/types'

const ACTIVE_STATUSES = new Set(['pending', 'submitted'])

export default function Billing() {
  const { user, refetch } = useSession()
  const [plans, setPlans] = useState<Plan[]>([])
  const [receiveMsisdn, setReceiveMsisdn] = useState<string>('')
  const [transactions, setTransactions] = useState<PaymentIntent[]>([])
  const [trxInput, setTrxInput] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)

  function loadTransactions() {
    api.get<PaymentIntent[]>('/billing/mine').then(setTransactions).catch(() => undefined)
  }

  useEffect(() => {
    api.get<Plan[]>('/billing/plans').then(setPlans).catch(() => undefined)
    api.get<{ receive_msisdn: string }>('/billing/config').then((c) => setReceiveMsisdn(c.receive_msisdn)).catch(() => undefined)
    loadTransactions()
  }, [])

  useEffect(() => {
    const hasActive = transactions.some((t) => ACTIVE_STATUSES.has(t.status))
    if (!hasActive) return
    const interval = setInterval(() => {
      loadTransactions()
      refetch()
    }, 4000)
    return () => clearInterval(interval)
  }, [transactions, refetch])

  async function upgrade(tier: 'pro' | 'max') {
    setError(null)
    try {
      await api.post<PaymentIntent>('/billing/intents', { purpose: 'subscription', plan_tier: tier })
      loadTransactions()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to start upgrade')
    }
  }

  async function submitTrx(intentId: string) {
    setError(null)
    const trxId = trxInput[intentId]?.trim()
    if (!trxId) return
    try {
      await api.post<PaymentIntent>(`/billing/intents/${intentId}/submit-trx`, { trx_id: trxId })
      setTrxInput((prev) => ({ ...prev, [intentId]: '' }))
      loadTransactions()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to submit TrxID')
    }
  }

  if (!user) return null

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4">
        <Link to="/dashboard" className="text-sm text-gray-600 hover:text-gray-900">
          &larr; Dashboard
        </Link>
      </header>
      <main className="p-6 max-w-2xl space-y-8">
        <div>
          <h1 className="text-lg font-semibold text-gray-900 mb-1">Billing</h1>
          <p className="text-sm text-gray-500">
            Current plan: <span className="font-semibold uppercase">{user.tier}</span>
          </p>
        </div>

        {error && <p className="text-red-600 text-sm">{error}</p>}

        <section className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {plans.map((p) => (
            <div key={p.tier} className="rounded-md border border-gray-200 p-4">
              <h2 className="font-semibold text-gray-900">{p.name}</h2>
              <p className="text-2xl font-bold text-gray-900 my-1">
                {p.price_bdt > 0 ? `৳${p.price_bdt}` : 'Free'}
                {p.price_bdt > 0 && <span className="text-xs font-normal text-gray-500">/mo</span>}
              </p>
              <p className="text-xs text-gray-500 mb-3">
                {p.daily_creation_limit === null ? 'Unlimited' : p.daily_creation_limit} creations/day
                {p.can_share && ' · sharing & invites'}
              </p>
              {p.tier !== 'free' && user.tier !== p.tier && (
                <button
                  type="button"
                  onClick={() => upgrade(p.tier as 'pro' | 'max')}
                  className="w-full text-xs rounded bg-gray-900 text-white px-2 py-1.5"
                >
                  Upgrade to {p.name}
                </button>
              )}
              {user.tier === p.tier && (
                <span className="block text-center text-xs text-green-600">Current plan</span>
              )}
            </div>
          ))}
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-semibold text-gray-900">Your payment history</h2>
          {transactions.length === 0 && <p className="text-sm text-gray-400">No payment intents yet.</p>}
          <ul className="space-y-3">
            {transactions.map((t) => (
              <li key={t.id} className="rounded-md border border-gray-200 p-3 text-sm">
                <div className="flex items-center justify-between mb-1">
                  <span className="font-medium text-gray-900">
                    {t.purpose === 'subscription' ? `Subscription — ${t.plan_tier}` : 'API access'}
                  </span>
                  <span className="text-xs uppercase font-semibold text-gray-500">{t.status}</span>
                </div>
                <p className="text-xs text-gray-500 mb-2">Amount expected: ৳{t.amount_expected_bdt}</p>

                {t.status === 'pending' && (
                  <div className="rounded bg-gray-50 p-2 space-y-2">
                    <p className="text-xs text-gray-600">
                      Send <span className="font-semibold">৳{t.amount_expected_bdt}</span> via bKash "Send Money" to{' '}
                      <span className="font-mono">{receiveMsisdn}</span>, then paste your TrxID below.
                    </p>
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value={trxInput[t.id] ?? ''}
                        onChange={(e) => setTrxInput((prev) => ({ ...prev, [t.id]: e.target.value }))}
                        placeholder="TrxID"
                        className="flex-1 rounded border border-gray-300 px-2 py-1 text-xs"
                      />
                      <button
                        type="button"
                        onClick={() => submitTrx(t.id)}
                        className="text-xs rounded bg-gray-900 text-white px-2 py-1"
                      >
                        Submit
                      </button>
                    </div>
                  </div>
                )}
                {t.status === 'submitted' && (
                  <p className="text-xs text-gray-500">
                    TrxID <span className="font-mono">{t.bkash_trx_id}</span> submitted — waiting for verification.
                  </p>
                )}
                {t.status === 'verified' && (
                  <p className="text-xs text-green-600">
                    Verified {t.verification_method === 'auto_sms' ? 'automatically' : 'by an admin'}.
                  </p>
                )}
                {t.note && <p className="text-xs text-amber-600 mt-1">{t.note}</p>}
              </li>
            ))}
          </ul>
        </section>
      </main>
    </div>
  )
}

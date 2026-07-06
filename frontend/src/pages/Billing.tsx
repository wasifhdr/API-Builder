import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AppShell from '../components/AppShell'
import {
  type Accent,
  Badge,
  type BadgeVariant,
  Button,
  buttonClasses,
  CapsLabel,
  cardClasses,
  EmptyRow,
  InlineCode,
  Input,
  PageHeader,
  Table,
  TableWrapper,
  Td,
  Th,
  Tr,
} from '../components/ui'
import { useSession } from '../hooks/useSession'
import { ApiError, api } from '../lib/api'
import type { PaymentIntent, PaymentStatus, Plan, PlanTier } from '../lib/types'

const ACTIVE_STATUSES = new Set(['pending', 'submitted'])

const STATUS_BADGE: Record<PaymentStatus, BadgeVariant> = {
  pending: 'pending',
  submitted: 'pending',
  verified: 'success',
  rejected: 'failed',
  expired: 'neutral',
}

const PLAN_ACCENT: Partial<Record<PlanTier, Accent>> = { pro: 'blue', max: 'purple' }

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
    api
      .get<{ receive_msisdn: string }>('/billing/config')
      .then((c) => setReceiveMsisdn(c.receive_msisdn))
      .catch(() => undefined)
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
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader
        eyebrow={<CapsLabel>Billing</CapsLabel>}
        title="Plans & payment"
        subline={
          <>
            Current plan: <span className="font-bold uppercase">{user.tier}</span>
          </>
        }
      />

      {error && <p className="mb-6 text-sm font-medium text-red-deep">{error}</p>}

      <section className="mb-10 grid gap-5 sm:grid-cols-3">
        {plans.map((p) => {
          const isCurrent = user.tier === p.tier
          const accent = PLAN_ACCENT[p.tier]
          return (
            <div key={p.tier} className={cardClasses({ variant: accent ? 'feature' : 'quiet', accent })}>
              <h2 className="text-h2">{p.name}</h2>
              <p className="my-2 font-display text-display-sm">
                {p.price_bdt > 0 ? <span className="font-mono">৳{p.price_bdt}</span> : 'Free'}
                {p.price_bdt > 0 && (
                  <span className="ml-1 font-sans text-sm font-normal text-ink/60">/mo</span>
                )}
              </p>
              <p className="mb-4 text-sm text-ink/70">
                {p.daily_creation_limit === null ? 'Unlimited' : p.daily_creation_limit} creations/day
                {p.can_share && ' · sharing & invites'}
              </p>
              {isCurrent ? (
                <Badge variant="pending">Current</Badge>
              ) : (
                p.tier !== 'free' && (
                  <Button className="w-full justify-center" onClick={() => upgrade(p.tier as 'pro' | 'max')}>
                    Upgrade to {p.name}
                  </Button>
                )
              )}
            </div>
          )
        })}
      </section>

      <section>
        <h2 className="text-h2 mb-3">Your payment history</h2>

        {transactions.some((t) => t.status === 'pending') && (
          <div className={cardClasses({ variant: 'callout', accent: 'gold', className: 'mb-4' })}>
            <CapsLabel tone="gold" className="mb-1">
              How to pay
            </CapsLabel>
            <p className="text-sm text-ink/80">
              Send the expected amount via bKash &quot;Send Money&quot; to <InlineCode>{receiveMsisdn}</InlineCode>,
              then paste your TrxID in the row below.
            </p>
          </div>
        )}

        <TableWrapper>
          <Table>
            <thead>
              <tr>
                <Th>Description</Th>
                <Th>Amount</Th>
                <Th>Status</Th>
                <Th>Action</Th>
              </tr>
            </thead>
            <tbody>
              {transactions.length === 0 && <EmptyRow colSpan={4}>No payment intents yet.</EmptyRow>}
              {transactions.map((t) => (
                <Tr key={t.id}>
                  <Td>{t.purpose === 'subscription' ? `Subscription — ${t.plan_tier}` : 'API access'}</Td>
                  <Td mono>৳{t.amount_expected_bdt}</Td>
                  <Td>
                    <Badge variant={STATUS_BADGE[t.status]}>{t.status}</Badge>
                  </Td>
                  <Td>
                    {t.status === 'pending' && (
                      <div className="flex gap-2">
                        <Input
                          type="text"
                          value={trxInput[t.id] ?? ''}
                          onChange={(e) => setTrxInput((prev) => ({ ...prev, [t.id]: e.target.value }))}
                          placeholder="TrxID"
                          className="w-32"
                        />
                        <Button size="sm" onClick={() => submitTrx(t.id)}>
                          Submit
                        </Button>
                      </div>
                    )}
                    {t.status === 'submitted' && (
                      <span className="text-xs text-ink/60">
                        TrxID <InlineCode>{t.bkash_trx_id}</InlineCode> — pending verification
                      </span>
                    )}
                    {t.status === 'verified' && (
                      <span className="text-xs font-medium text-green-deep">
                        Verified {t.verification_method === 'auto_sms' ? 'automatically' : 'by an admin'}
                      </span>
                    )}
                    {t.note && <div className="mt-1 text-xs text-gold-deep">{t.note}</div>}
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </TableWrapper>
      </section>
    </AppShell>
  )
}

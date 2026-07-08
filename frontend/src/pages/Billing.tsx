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
  StatChip,
  Table,
  TableWrapper,
  Td,
  Th,
  Tr,
} from '../components/ui'
import { useSession } from '../hooks/useSession'
import { ApiError, api } from '../lib/api'
import type {
  Cashout,
  CashoutStatus,
  PaymentIntent,
  PaymentStatus,
  Plan,
  PlanTier,
  SubscribeResult,
  SweepResult,
  Wallet,
  WalletLedgerEntry,
} from '../lib/types'

const ACTIVE_STATUSES = new Set(['pending', 'submitted'])

const STATUS_BADGE: Record<PaymentStatus, BadgeVariant> = {
  pending: 'pending',
  submitted: 'pending',
  verified: 'success',
  rejected: 'failed',
  expired: 'neutral',
}

const PLAN_ACCENT: Partial<Record<PlanTier, Accent>> = { pro: 'blue', max: 'purple' }

const CASHOUT_STATUS_BADGE: Record<CashoutStatus, BadgeVariant> = {
  requested: 'pending',
  paid: 'success',
  rejected: 'failed',
}

const RECHARGE_QUICK_AMOUNTS = [100, 500, 1000]

const LEDGER_REASON_LABEL: Record<WalletLedgerEntry['reason'], string> = {
  recharge: 'Recharge',
  subscription: 'Subscription',
  api_access: 'API access',
  call_debit: 'Call charge',
  call_refund: 'Call refund',
  call_earning: 'Call earning',
  platform_cut: 'Platform cut',
  sweep_out: 'Swept to balance',
  sweep_in: 'Swept from earnings',
  cashout: 'Cashout',
  admin_adjust: 'Admin adjustment',
}

export default function Billing() {
  const { user, refetch } = useSession()
  const [plans, setPlans] = useState<Plan[]>([])
  const [receiveMsisdn, setReceiveMsisdn] = useState<string>('')
  const [transactions, setTransactions] = useState<PaymentIntent[]>([])
  const [trxInput, setTrxInput] = useState<Record<string, string>>({})
  const [wallet, setWallet] = useState<Wallet | null>(null)
  const [ledger, setLedger] = useState<WalletLedgerEntry[]>([])
  const [cashouts, setCashouts] = useState<Cashout[]>([])
  const [rechargeAmount, setRechargeAmount] = useState('')
  const [cashoutAmount, setCashoutAmount] = useState('')
  const [cashoutMsisdn, setCashoutMsisdn] = useState('')
  const [error, setError] = useState<string | null>(null)

  function loadTransactions() {
    api.get<PaymentIntent[]>('/billing/mine').then(setTransactions).catch(() => undefined)
  }

  function loadWallet() {
    api.get<Wallet>('/billing/wallet').then(setWallet).catch(() => undefined)
    api.get<WalletLedgerEntry[]>('/billing/wallet/ledger').then(setLedger).catch(() => undefined)
    api.get<Cashout[]>('/billing/wallet/cashouts').then(setCashouts).catch(() => undefined)
  }

  useEffect(() => {
    api.get<Plan[]>('/billing/plans').then(setPlans).catch(() => undefined)
    api
      .get<{ receive_msisdn: string }>('/billing/config')
      .then((c) => setReceiveMsisdn(c.receive_msisdn))
      .catch(() => undefined)
    loadTransactions()
    loadWallet()
  }, [])

  useEffect(() => {
    const hasActive = transactions.some((t) => ACTIVE_STATUSES.has(t.status))
    if (!hasActive) return
    const interval = setInterval(() => {
      loadTransactions()
      loadWallet()
      refetch()
    }, 4000)
    return () => clearInterval(interval)
  }, [transactions, refetch])

  async function upgrade(tier: 'pro' | 'max') {
    setError(null)
    try {
      await api.post<SubscribeResult>('/billing/subscribe', { plan_tier: tier })
      loadWallet()
      refetch()
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        const shortfall = (err.body as { shortfall_bdt?: string } | undefined)?.shortfall_bdt
        setError(shortfall ? `Top up ৳${shortfall} more to afford this plan.` : err.message)
      } else {
        setError(err instanceof ApiError ? err.message : 'Failed to upgrade')
      }
    }
  }

  async function recharge() {
    setError(null)
    const amount = Number(rechargeAmount)
    if (!amount || amount <= 0) return
    try {
      await api.post<PaymentIntent>('/billing/intents', { purpose: 'recharge', amount_bdt: amount })
      setRechargeAmount('')
      loadTransactions()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to start recharge')
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
      loadWallet()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to submit TrxID')
    }
  }

  async function sweepEarnings() {
    setError(null)
    try {
      await api.post<SweepResult>('/billing/wallet/sweep', {})
      loadWallet()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to sweep earnings')
    }
  }

  async function requestCashout() {
    setError(null)
    const amount = Number(cashoutAmount)
    const msisdn = cashoutMsisdn.trim()
    if (!amount || amount <= 0 || !msisdn) return
    try {
      await api.post<Cashout>('/billing/wallet/cashout', { amount_bdt: amount, payout_msisdn: msisdn })
      setCashoutAmount('')
      setCashoutMsisdn('')
      loadWallet()
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to request cashout')
    }
  }

  if (!user) return null

  const isSuperAdmin = user.role === 'super_admin'

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader
        eyebrow={<CapsLabel>Billing</CapsLabel>}
        title="Plans & payment"
        subline={
          isSuperAdmin ? undefined : (
            <>
              Current plan: <span className="font-bold uppercase">{user.tier}</span>
            </>
          )
        }
      />

      {error && <p className="mb-6 text-sm font-medium text-red-deep">{error}</p>}

      <section className={cardClasses({ variant: 'feature', accent: 'blue', className: 'mb-10' })}>
        <CapsLabel tone="blue" className="mb-3">
          Wallet
        </CapsLabel>
        <div className="flex flex-wrap items-end gap-4">
          <StatChip value={`৳${wallet?.balance_bdt ?? '0.00'}`} label="Balance" />
          <StatChip value={`৳${wallet?.earnings_bdt ?? '0.00'}`} label="Earnings" />
        </div>

        {wallet && Number(wallet.earnings_bdt) > 0 && (
          <div className="mt-4 space-y-3 border-t border-sand pt-4">
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="ghost" size="sm" onClick={sweepEarnings}>
                Sweep ৳{wallet.earnings_bdt} to balance
              </Button>
              {!wallet.can_cashout && (
                <span className="text-xs text-ink/60">Upgrade to Max to cash out to bKash instead.</span>
              )}
            </div>

            {wallet.can_cashout && (
              <div className="flex flex-wrap items-end gap-2">
                <div>
                  <Input
                    type="number"
                    min={1}
                    value={cashoutAmount}
                    onChange={(e) => setCashoutAmount(e.target.value)}
                    placeholder="Amount"
                    aria-label="Cashout amount in taka"
                    className="w-28"
                  />
                </div>
                <div>
                  <Input
                    type="text"
                    value={cashoutMsisdn}
                    onChange={(e) => setCashoutMsisdn(e.target.value)}
                    placeholder="bKash number"
                    aria-label="Payout bKash number"
                    className="w-36"
                  />
                </div>
                <Button size="sm" onClick={requestCashout}>
                  Cash out to bKash
                </Button>
              </div>
            )}
          </div>
        )}

        {cashouts.length > 0 && (
          <div className="mt-4 border-t border-sand pt-4">
            <CapsLabel tone="muted" className="mb-2">
              Cashout requests
            </CapsLabel>
            <ul className="space-y-1">
              {cashouts.slice(0, 5).map((c) => (
                <li key={c.id} className="flex items-center justify-between text-sm">
                  <span className="text-ink/70">
                    ৳{c.amount_bdt} &rarr; {c.payout_msisdn}
                  </span>
                  <Badge variant={CASHOUT_STATUS_BADGE[c.status]}>{c.status}</Badge>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="mt-5 flex flex-wrap items-center gap-2">
          <Input
            type="number"
            min={10}
            step="1"
            value={rechargeAmount}
            onChange={(e) => setRechargeAmount(e.target.value)}
            placeholder="Amount in ৳"
            aria-label="Recharge amount in taka"
            className="w-36"
          />
          {RECHARGE_QUICK_AMOUNTS.map((amt) => (
            <Button key={amt} variant="ghost" size="sm" onClick={() => setRechargeAmount(String(amt))}>
              ৳{amt}
            </Button>
          ))}
          <Button size="sm" onClick={recharge}>
            Add funds
          </Button>
        </div>
        <p className="mt-2 text-xs text-ink/60">
          Minimum ৳10. Creates a payment intent below — pay via bKash and submit the TrxID to credit your
          wallet.
        </p>

        {ledger.length > 0 && (
          <div className="mt-5 border-t border-sand pt-4">
            <CapsLabel tone="muted" className="mb-2">
              Recent wallet activity
            </CapsLabel>
            <ul className="space-y-1">
              {ledger.slice(0, 5).map((entry) => (
                <li key={entry.id} className="flex justify-between text-sm">
                  <span className="text-ink/70">{LEDGER_REASON_LABEL[entry.reason]}</span>
                  <span className={`font-mono ${entry.amount_bdt.startsWith('-') ? 'text-red-deep' : 'text-green-deep'}`}>
                    {entry.amount_bdt.startsWith('-') ? '' : '+'}৳{entry.amount_bdt}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </section>

      {isSuperAdmin ? (
        <div className={cardClasses({ variant: 'callout', accent: 'gold', className: 'mb-10' })}>
          <CapsLabel tone="gold" className="mb-1">
            Super admin
          </CapsLabel>
          <p className="text-sm text-ink/80">
            You&apos;re a super admin — plans don&apos;t apply. You have unlimited creations and sharing on
            every API, with no payment required.
          </p>
        </div>
      ) : (
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
                <ul className="mb-4 space-y-1 text-sm text-ink/70">
                  <li>
                    {p.monthly_call_quota === null ? 'Unlimited' : p.monthly_call_quota} calls/mo
                  </li>
                  {p.can_share ? (
                    <>
                      <li>Charge invitees — one-time, per-call &amp; subscription</li>
                      <li>Platform cut of your sales: {p.platform_cut_pct}%</li>
                      <li>
                        Earnings: {p.can_cashout ? 'cash out to bKash' : 'spend as platform credit'}
                      </li>
                      <li>
                        {p.max_invitees_per_api === null ? 'Unlimited' : p.max_invitees_per_api} invitees per API
                      </li>
                    </>
                  ) : (
                    <li>No sharing or pricing</li>
                  )}
                  <li className="text-xs text-ink/50">
                    {p.daily_creation_limit === null ? 'Unlimited' : p.daily_creation_limit} creations/day
                  </li>
                </ul>
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
      )}

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
                  <Td>
                    {t.purpose === 'subscription'
                      ? `Subscription — ${t.plan_tier}`
                      : t.purpose === 'recharge'
                        ? 'Wallet recharge'
                        : 'API access'}
                  </Td>
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

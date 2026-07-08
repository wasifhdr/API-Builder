import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import AdminNav from '../components/AdminNav'
import AppShell from '../components/AppShell'
import {
  Button,
  buttonClasses,
  CapsLabel,
  Checkbox,
  FieldError,
  FieldLabel,
  Input,
  PageHeader,
  cardClasses,
} from '../components/ui'
import { ApiError, api } from '../lib/api'
import type { AdminPlan, AdminPlanUpdate, PlanTier } from '../lib/types'

const TIER_LABEL: Record<PlanTier, string> = { free: 'Free', pro: 'Pro', max: 'Max' }
const TIER_ACCENT: Partial<Record<PlanTier, 'blue' | 'purple'>> = { pro: 'blue', max: 'purple' }

interface EditState {
  price_bdt: string
  daily_creation_limit: string
  can_share: boolean
  monthly_call_quota: string
  platform_cut_pct: string
  can_cashout: boolean
  max_invitees_per_api: string
}

function toEditState(plan: AdminPlan): EditState {
  return {
    price_bdt: String(plan.price_bdt),
    daily_creation_limit: plan.daily_creation_limit === null ? '' : String(plan.daily_creation_limit),
    can_share: plan.can_share,
    monthly_call_quota: plan.monthly_call_quota === null ? '' : String(plan.monthly_call_quota),
    platform_cut_pct: plan.platform_cut_pct,
    can_cashout: plan.can_cashout,
    max_invitees_per_api: plan.max_invitees_per_api === null ? '' : String(plan.max_invitees_per_api),
  }
}

function parseBlankableInt(value: string): number | null | 'invalid' {
  if (value.trim() === '') return null
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0) return 'invalid'
  return Math.trunc(parsed)
}

export default function AdminPlans() {
  const [plans, setPlans] = useState<AdminPlan[]>([])
  const [edits, setEdits] = useState<Record<string, EditState>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  const [saved, setSaved] = useState<Record<string, boolean>>({})
  const [loadError, setLoadError] = useState<string | null>(null)

  function load() {
    api
      .get<AdminPlan[]>('/admin/plans')
      .then((data) => {
        setPlans(data)
        setEdits(Object.fromEntries(data.map((p) => [p.tier, toEditState(p)])))
      })
      .catch((err) => setLoadError(err instanceof ApiError ? err.message : 'Failed to load'))
  }

  useEffect(load, [])

  function setField(tier: string, field: keyof EditState, value: string | boolean) {
    setEdits((prev) => ({ ...prev, [tier]: { ...prev[tier], [field]: value } }))
    setSaved((prev) => ({ ...prev, [tier]: false }))
  }

  async function save(plan: AdminPlan) {
    const edit = edits[plan.tier]
    setErrors((prev) => ({ ...prev, [plan.tier]: '' }))

    const price = Number(edit.price_bdt)
    if (!Number.isFinite(price) || price < 0) {
      setErrors((prev) => ({ ...prev, [plan.tier]: 'Price must be a non-negative number' }))
      return
    }
    if (plan.tier === 'free' && price !== 0) {
      setErrors((prev) => ({ ...prev, [plan.tier]: 'Free tier price is locked at 0' }))
      return
    }

    const dailyLimit = parseBlankableInt(edit.daily_creation_limit)
    if (dailyLimit === 'invalid') {
      setErrors((prev) => ({ ...prev, [plan.tier]: 'Daily limit must be a non-negative number or blank' }))
      return
    }

    const monthlyCallQuota = parseBlankableInt(edit.monthly_call_quota)
    if (monthlyCallQuota === 'invalid') {
      setErrors((prev) => ({ ...prev, [plan.tier]: 'Monthly call quota must be a non-negative number or blank' }))
      return
    }

    const maxInvitees = parseBlankableInt(edit.max_invitees_per_api)
    if (maxInvitees === 'invalid') {
      setErrors((prev) => ({ ...prev, [plan.tier]: 'Max invitees must be a non-negative number or blank' }))
      return
    }

    const cutPct = Number(edit.platform_cut_pct)
    if (!Number.isFinite(cutPct) || cutPct < 0 || cutPct > 100) {
      setErrors((prev) => ({ ...prev, [plan.tier]: 'Platform cut must be between 0 and 100' }))
      return
    }

    if (plan.tier === 'free' && edit.can_cashout) {
      setErrors((prev) => ({ ...prev, [plan.tier]: 'Free tier cannot cash out' }))
      return
    }

    const body: AdminPlanUpdate = {
      price_bdt: Math.trunc(price),
      daily_creation_limit: dailyLimit,
      can_share: edit.can_share,
      monthly_call_quota: monthlyCallQuota,
      platform_cut_pct: String(cutPct),
      can_cashout: edit.can_cashout,
      max_invitees_per_api: maxInvitees,
    }

    setSaving((prev) => ({ ...prev, [plan.tier]: true }))
    try {
      const updated = await api.patch<AdminPlan>(`/admin/plans/${plan.tier}`, body)
      setPlans((prev) => prev.map((p) => (p.tier === plan.tier ? updated : p)))
      setEdits((prev) => ({ ...prev, [plan.tier]: toEditState(updated) }))
      setSaved((prev) => ({ ...prev, [plan.tier]: true }))
    } catch (err) {
      setErrors((prev) => ({
        ...prev,
        [plan.tier]: err instanceof ApiError ? err.message : 'Failed to save',
      }))
    } finally {
      setSaving((prev) => ({ ...prev, [plan.tier]: false }))
    }
  }

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader eyebrow={<CapsLabel>Admin</CapsLabel>} title="Plans" />
      <AdminNav />

      {loadError && <p className="mb-4 text-sm font-medium text-red-deep">{loadError}</p>}

      <div className="grid gap-5 md:grid-cols-3">
        {plans.map((plan) => {
          const edit = edits[plan.tier]
          if (!edit) return null
          const isFree = plan.tier === 'free'
          return (
            <div
              key={plan.tier}
              className={cardClasses({ variant: 'quiet', accent: TIER_ACCENT[plan.tier] })}
            >
              <h2 className="text-h2 mb-3">{TIER_LABEL[plan.tier]}</h2>

              <div className="mb-3">
                <FieldLabel htmlFor={`price-${plan.tier}`}>Price (BDT / mo)</FieldLabel>
                <Input
                  id={`price-${plan.tier}`}
                  type="number"
                  min={0}
                  disabled={isFree}
                  value={edit.price_bdt}
                  onChange={(e) => setField(plan.tier, 'price_bdt', e.target.value)}
                />
                {isFree && <p className="mt-1 text-xs text-ink/60">Free tier price is locked at 0.</p>}
              </div>

              <div className="mb-3">
                <FieldLabel htmlFor={`limit-${plan.tier}`}>Daily creation limit</FieldLabel>
                <Input
                  id={`limit-${plan.tier}`}
                  type="number"
                  min={0}
                  placeholder="unlimited"
                  value={edit.daily_creation_limit}
                  onChange={(e) => setField(plan.tier, 'daily_creation_limit', e.target.value)}
                />
                <p className="mt-1 text-xs text-ink/60">Leave blank for unlimited.</p>
              </div>

              <div className="mb-3">
                <FieldLabel htmlFor={`callquota-${plan.tier}`}>Calls included / month</FieldLabel>
                <Input
                  id={`callquota-${plan.tier}`}
                  type="number"
                  min={0}
                  placeholder="unlimited"
                  value={edit.monthly_call_quota}
                  onChange={(e) => setField(plan.tier, 'monthly_call_quota', e.target.value)}
                />
                <p className="mt-1 text-xs text-ink/60">Leave blank for unlimited.</p>
              </div>

              <div className="mb-3">
                <FieldLabel htmlFor={`cut-${plan.tier}`}>Platform cut of creator sales (%)</FieldLabel>
                <Input
                  id={`cut-${plan.tier}`}
                  type="number"
                  min={0}
                  max={100}
                  value={edit.platform_cut_pct}
                  onChange={(e) => setField(plan.tier, 'platform_cut_pct', e.target.value)}
                />
              </div>

              <div className="mb-3">
                <FieldLabel htmlFor={`invitees-${plan.tier}`}>Invitees per API</FieldLabel>
                <Input
                  id={`invitees-${plan.tier}`}
                  type="number"
                  min={0}
                  placeholder="unlimited"
                  value={edit.max_invitees_per_api}
                  onChange={(e) => setField(plan.tier, 'max_invitees_per_api', e.target.value)}
                />
                <p className="mt-1 text-xs text-ink/60">Leave blank for unlimited.</p>
              </div>

              <div className="mb-3 flex items-center gap-2">
                <Checkbox
                  id={`share-${plan.tier}`}
                  checked={edit.can_share}
                  onChange={(e) => setField(plan.tier, 'can_share', e.target.checked)}
                />
                <FieldLabel htmlFor={`share-${plan.tier}`} className="mb-0">
                  Allow sharing &amp; invites
                </FieldLabel>
              </div>

              <div className="mb-4 flex items-center gap-2">
                <Checkbox
                  id={`cashout-${plan.tier}`}
                  disabled={isFree}
                  checked={edit.can_cashout}
                  onChange={(e) => setField(plan.tier, 'can_cashout', e.target.checked)}
                />
                <FieldLabel htmlFor={`cashout-${plan.tier}`} className="mb-0">
                  Can cash out earnings to bKash
                </FieldLabel>
              </div>

              {errors[plan.tier] && <FieldError>{errors[plan.tier]}</FieldError>}

              <div className="mt-2 flex items-center gap-3">
                <Button size="sm" onClick={() => save(plan)} disabled={saving[plan.tier]}>
                  {saving[plan.tier] ? 'Saving…' : 'Save'}
                </Button>
                {saved[plan.tier] && !errors[plan.tier] && (
                  <span className="text-xs font-medium text-green-deep">Saved</span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </AppShell>
  )
}

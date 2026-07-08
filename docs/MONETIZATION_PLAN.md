# Monetization & Wallet Plan

Instructions for the implementing agent (Sonnet). Work **one phase per session**, in order — each
phase is a vertical slice that leaves the app runnable. This document extends
[BLUEPRINT.md](BLUEPRINT.md), [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md), and
[MULTI_TENANCY_PLAN.md](MULTI_TENANCY_PLAN.md); where this document conflicts with any of them,
**this document wins** for the billing/pricing features described here. Read
[DESIGN.md](DESIGN.md) before building any page — every new screen uses the Warm Editorial system.

**Definition of done for every phase:** acceptance criteria pass · `ruff check` clean ·
`alembic upgrade head` clean from scratch **and** from the current prod-shaped DB (existing users,
subscriptions, and transactions survive) · `scripts/dev.ps1` boots everything without errors ·
existing Google/password login still works end-to-end · commit with message
`Monetization N: <summary>`.

Global rules (unchanged from IMPLEMENTATION_PLAN.md / MULTI_TENANCY_PLAN.md):
- Windows-native Python; never move Playwright into Docker or the FastAPI process.
- Async everywhere (SQLAlchemy async + asyncpg, redis.asyncio).
- Enums `native_enum=False` storing `.value`; JSONB replaced, never mutated; timestamps UTC
  tz-aware; **all money `Numeric(10,2)` BDT**; quotas on the Asia/Dhaka calendar day.
- Secrets only via `app/config.py` / `.env`.
- **Every new page, component, and UI state follows the Warm Editorial design system**
  ([DESIGN.md](DESIGN.md)): build from the existing `frontend/src/components/ui` primitives
  (Button, Card, Table, Modal, Field, Badge, StatChip, PageHeader, EmptyState, …) and existing
  layouts (AppShell, AdminNav). No new colors, fonts, spacing scales, or chart libraries. Forms,
  confirm modals, empty states, and error text reuse the patterns already on Billing, Settings,
  and the admin pages.

---

## Why this exists (the problem being fixed)

Today two things are priced as flat monthly subscriptions and one expensive thing is not priced at
all:

- **Platform tiers gate `daily_creation_limit`** (how many APIs you *build*). But building is a
  one-time act — the recurring charge doesn't map to recurring value.
- **API *calls* are unpriced.** Every call runs a headless Playwright replay on a single RTX 4050
  (6 GB, shared with llama.cpp, concurrency 1). Once an invitee holds a grant they call
  **unlimited times for a one-time fee** — unbounded compute cost on one laptop, borne by the
  owner. Calls are the scarce, expensive, valuable resource.
- **Every money movement needs manual bKash verification.** Both `SUBSCRIPTION` and `API_ACCESS`
  purchases create a `PaymentTransaction` an admin (or the SMS matcher) must verify.

The fix is a **prepaid BDT wallet** that becomes the single rail for all value in the system:

- The **only** thing that touches bKash — and therefore the only thing an admin verifies — is
  **recharging the wallet** (money *in*). The SMS auto-matcher already covers this, so most
  recharges never reach a human.
- **Everything else is an internal ledger operation:** buying a subscription, buying one-time API
  access, and paying per call all become wallet debits with no bKash and no admin.
- **API calls are metered per call** against the wallet, so price finally tracks compute cost and
  the single-machine capacity ceiling.
- When a creator charges their invitees, the **platform takes a cut** and the creator keeps the
  rest as **earnings**. The one new money-*out* action is a **cashout** (earnings → bKash),
  available only on the Max tier and approved manually by an admin.

The money map the system converges on:

| Event | Touches bKash? | Admin involvement |
|---|---|---|
| Recharge wallet | ✓ in | auto-match via SMS, else manual verify |
| Buy subscription / one-time API access | ✗ internal debit | none |
| Per-call usage | ✗ internal debit | none |
| Creator earnings + platform cut | ✗ internal split | none |
| Cashout (Max only) | ✓ out | manual payout approval |

---

## Locked decisions (agreed with the owner — do not re-litigate)

1. **The wallet holds real BDT** (1 unit = ৳1), `Numeric(10,2)`. No abstract "credits" and no
   exchange rate — the platform cut, creator earnings, and cashouts are all arithmetic on the same
   number.
2. **The platform earns a cut of every creator sale.** The cut percentage is per-tier and editable
   at runtime (see `plan_settings.platform_cut_pct`).
3. **Pro vs Max is decided by the cut and by cashout, not by withholding features.** Both paid
   tiers may charge invitees with **all** pricing modes (one-time, per-call, subscription). They
   differ on: platform cut % (Pro higher, Max lower), **cashout** (Pro earnings are spend-only
   platform credit; only Max can withdraw earnings to bKash), invitee cap, monthly call allowance,
   and replay-queue priority.
4. **Per-API access verification is eliminated.** Buying access to a priced API is a wallet debit
   at accept time, not a `PaymentTransaction`. After Phase W2, `API_ACCESS` and `SUBSCRIPTION` no
   longer create payment intents; the sole verifiable purpose is `RECHARGE`.
5. **Super admins stay above tiers.** They never pay to call, never hit call quotas, their consume
   path skips the wallet gate entirely, and they may cash out. They still *earn* on their own
   shared APIs (harmless; keeps stats consistent).

### Default decisions baked in (change here if you disagree — Sonnet builds them as written)

- **Debit timing = charge-at-enqueue, refund-on-failure.** The balance gate and the debit are the
  **same atomic operation** in `public.py` (no oversell). If the replay ends `failed` or `timeout`,
  the worker **refunds** the consumer in full. The consumer only pays for `succeeded` calls.
- **Cache hits are free.** A cache hit returns before any `ApiExecution` row is created and does no
  replay compute, so it does not debit. (Price tracks compute.)
- **Spending debits `balance_bdt` only.** Earnings accrue to a separate `earnings_bdt` bucket and
  never sit in the hot path. Earnings become spendable only via an explicit **sweep**
  (earnings → balance) or, on Max, a **cashout** (earnings → bKash).
- **Recharge is a free-form amount** (min ৳10), with a few suggested quick-pick amounts in the UI.
  bKash is manual regardless, so fixed packs buy nothing.
- **Rounding:** platform cut = `round(price × pct, 2)`; creator earning = `price − cut`. Never
  create fractional-poisha dust — the two legs always sum to the debited price.

---

## Cross-cutting contracts

### Schema (all money `Numeric(10,2)`, all ids UUID, all timestamps tz-aware UTC)

**`wallets`** — one per user, created on demand.

| column | type | notes |
|---|---|---|
| `user_id` | UUID pk, FK users CASCADE | one wallet per user |
| `balance_bdt` | `Numeric(10,2)` default 0 | spendable; from recharges + sweeps. **Not** withdrawable |
| `earnings_bdt` | `Numeric(10,2)` default 0 | creator earnings, net of cut; sweep or (Max) cash out |
| `updated_at` | `DateTime(tz)` | `onupdate=now()` |

**`wallet_ledger`** — append-only audit trail; the wallet columns are the fast cache, this is the
truth. Every credit/debit writes exactly one row **in the same transaction** as the balance change.

| column | type | notes |
|---|---|---|
| `id` | UUID pk | |
| `user_id` | UUID FK users CASCADE, index | whose bucket moved |
| `bucket` | `String(10)` | `balance` \| `earnings` |
| `amount_bdt` | `Numeric(10,2)` | signed: negative = debit, positive = credit |
| `reason` | `String(20)` | see reason enum below |
| `balance_after_bdt` | `Numeric(10,2)` | bucket value after this row (running balance) |
| `execution_id` | UUID FK api_executions SET NULL, nullable | per-call rows |
| `api_id` | UUID FK custom_apis SET NULL, nullable | which API |
| `transaction_id` | UUID FK payment_transactions SET NULL, nullable | recharge rows |
| `counterparty_user_id` | UUID FK users SET NULL, nullable | e.g. the consumer, on an earning row |
| `created_at` | `DateTime(tz)` server_default now, index | |

`reason` values: `recharge`, `subscription`, `api_access`, `call_debit`, `call_refund`,
`call_earning`, `platform_cut`, `sweep_out`, `sweep_in`, `cashout`, `admin_adjust`. `platform_cut`
rows use `user_id = NULL` (the platform is not a user) — they are how admin revenue is queried.

**`plan_settings`** — extend the existing table (single migration, backfill existing rows):

| new column | type | seed: free / pro / max |
|---|---|---|
| `monthly_call_quota` | `Integer` nullable (null = unlimited) | 100 / 5000 / 50000 |
| `platform_cut_pct` | `Numeric(5,2)` | 0 / 25.00 / 10.00 |
| `can_cashout` | `Boolean` | false / false / true |
| `max_invitees_per_api` | `Integer` nullable (null = unlimited) | 1 / 25 / null |

Keep the existing `daily_creation_limit` and `can_share` as-is (creation limit demoted to a
fair-use guard, no longer the headline lever). `PlanConfig` in `services/plans.py` gains the four
new fields; every construction site (`_defaults()`, the DB read, `billing.py`, `admin.py`) updates.

**`custom_apis`** — add pricing mode:

| new column | type | notes |
|---|---|---|
| `pricing_mode` | enum `ApiPricingMode` (`native_enum=False`) | `free` \| `one_time` \| `per_call` \| `subscription`; default `free` |
| `included_call_quota` | `Integer` nullable | only meaningful for `subscription` mode (W6) |

`price_bdt` (already exists) is **reinterpreted by mode**: one-time access price, per-call price, or
monthly price. Migration backfills: rows with `price_bdt > 0` → `one_time`, else `free`.

**`cashout_requests`** (Phase W5):

| column | type | notes |
|---|---|---|
| `id` | UUID pk | |
| `user_id` | UUID FK users CASCADE, index | requester (must be `can_cashout`) |
| `amount_bdt` | `Numeric(10,2)` | ≤ current `earnings_bdt` at request time |
| `payout_msisdn` | `String(20)` | where to send bKash |
| `status` | enum (`requested`\|`paid`\|`rejected`) | |
| `bkash_trx_id` | `String(40)` nullable | admin records the payout TrxID |
| `note` | `Text` nullable | |
| `decided_by_user_id` | UUID FK users SET NULL | admin who paid/rejected |
| `created_at` / `decided_at` | `DateTime(tz)` | |

### `services/wallet.py` (the one service every money path goes through)

```python
class InsufficientBalance(Exception): ...

async def get_or_create(user_id, db) -> Wallet
async def balances(user_id, db) -> tuple[Decimal, Decimal]        # (balance, earnings)

# atomic conditional debit — the concurrency-safe gate. Never read-then-write.
async def debit(user_id, amount, reason, db, *, bucket="balance", **refs) -> Decimal
#   UPDATE wallets SET balance_bdt = balance_bdt - :amt, updated_at = now()
#   WHERE user_id = :uid AND balance_bdt >= :amt RETURNING balance_bdt
#   0 rows affected  -> raise InsufficientBalance
#   else insert a wallet_ledger row (with balance_after) in the SAME tx

async def credit(user_id, amount, reason, db, *, bucket="balance", **refs) -> Decimal
```

Rules: `debit`/`credit` **never commit** — the caller owns the transaction boundary (mirrors
`sms_matcher.apply_verified_effects`). `**refs` carries `execution_id` / `api_id` /
`transaction_id` / `counterparty_user_id` onto the ledger row. All amounts `Decimal`, positive;
`bucket` selects which column. A wallet row is created lazily the first time a user is credited or
debited.

### Super-admin bypass (consistent with the tenancy contract)

Branch on `user.role == SUPER_ADMIN` **before** any wallet check: super admins skip the per-call
gate and the subscription/one-time debit entirely (calls and features are free for them), and
`can_cashout` is treated as true. Reuse the existing `is_super` signal on `UserWithTier`.

### Audit logging

Reuse `services/audit.py::log_admin_action`. New audited actions: `cashout.pay`, `cashout.reject`,
`wallet.adjust` (any admin manual balance/earnings adjustment). Recharge verify/reject already log
via the existing `transaction.verify` / `transaction.reject`.

---

## Phase W1 — Wallet core, ledger, and recharge money-in

Introduce the wallet and make **recharge** a first-class purpose. Nothing spends from the wallet
yet; subscriptions and API access keep their current bKash flow this phase. The app stays fully
runnable and the wallet becomes fundable.

**Touches:** new `models/wallet.py`, `models/plan_settings.py` (+4 columns), migration (wallets +
wallet_ledger + plan_settings columns + backfill + one wallet row per existing user),
`services/wallet.py`, `services/plans.py` (`PlanConfig` +4 fields, all call sites),
`services/payments.py` (+`RECHARGE`), `services/sms_matcher.py` (`apply_verified_effects` RECHARGE
branch), `models/billing.py` (`PaymentPurpose.RECHARGE`), `schemas/billing.py`, `api/billing.py`,
`pages/Billing.tsx`, `lib/types.ts`, `lib/api.ts`.

- [ ] Migration: create `wallets` + `wallet_ledger`; add the four `plan_settings` columns and
      backfill seed values (free/pro/max per the table above); insert a `wallets` row
      (`balance 0, earnings 0`) for every existing user. Idempotent + reversible.
- [ ] `services/wallet.py` per the contract, with `debit` using the atomic conditional UPDATE and
      always writing a ledger row with `balance_after_bdt`.
- [ ] `PaymentPurpose.RECHARGE`. `payments.create_intent(purpose=RECHARGE, amount_bdt=…)` accepts a
      caller-supplied amount (min ৳10; reject ≤ 0 and > a sane cap, e.g. ৳100000). `plan_tier` /
      `api_id` are null for recharges. Add `amount_bdt: Decimal | None` to `CreateIntentRequest`
      (which is `extra="forbid"`, so the field must be declared) and require it when
      `purpose == recharge`.
- [ ] `apply_verified_effects`: add a `RECHARGE` branch → `wallet.credit(user_id,
      amount_received_bdt, "recharge", transaction_id=…)`. (Subscription/API-access branches
      unchanged this phase.)
- [ ] Endpoints: `GET /api/billing/wallet` → `{balance_bdt, earnings_bdt, can_cashout}`;
      `GET /api/billing/wallet/ledger?limit&offset` newest-first. Recharge reuses the existing
      `POST /api/billing/intents` (purpose `recharge`, body carries `amount_bdt`) +
      `submit-trx`.
- [ ] Billing page: a **Wallet** card at the top — big balance, "Add funds" opens the existing
      amount → intent → submit-TrxID flow (reuse the current payment modal, now with a
      user-entered amount + quick-pick chips), and a compact ledger list below payment history.
- [ ] Tests: recharge intent → submit TrxID → SMS match credits `balance_bdt` and writes one
      `recharge` ledger row; `debit` raises `InsufficientBalance` and leaves the balance untouched;
      two concurrent debits totalling more than the balance never drive it negative (one succeeds,
      one raises).

**Accept:** as a normal user, add ৳500 via the wallet card, submit the TrxID, and (after the admin
verifies or the matching SMS lands) see `balance_bdt = 500` and a ledger entry; the balance survives
`alembic downgrade`+`upgrade`.

## Phase W2 — Route subscriptions & one-time API access through the wallet

Flip both existing purchase flows from bKash-per-purchase to internal wallet debits. This is the
phase that **eliminates per-API and per-subscription admin verification** — afterwards `RECHARGE` is
the only verifiable purpose.

**Touches:** `services/payments.py`, `api/billing.py` (subscribe-from-wallet endpoint),
`api/invites.py` (accept → wallet debit), `services/sms_matcher.py` (drop the now-dead
subscription/api_access effect branches or guard them for legacy rows), `schemas/billing.py`,
`schemas/invite.py`, `pages/Billing.tsx`, `pages/InviteAccept.tsx`, `lib/api.ts`.

- [ ] `POST /api/billing/subscribe` — body `{plan_tier}`. Super admins rejected ("don't need
      plans"). Compute price from `plan_for(tier)`; `wallet.debit(user, price, "subscription")`
      inside one transaction, then run the **existing** subscription activation logic (extend vs
      upgrade — lift it out of `apply_verified_effects` into a shared
      `services/subscriptions.py::activate(user, tier, db)` so both the wallet path and legacy code
      call it). `InsufficientBalance` → 402 with `{detail, shortfall_bdt}`.
- [ ] `accept_invite` (priced, `one_time` mode): replace the `payments.create_intent(API_ACCESS)`
      branch with `wallet.debit(user, api.price_bdt, "api_access", api_id=…)` → issue the grant
      immediately in the same tx. `InsufficientBalance` → 402 `{detail, shortfall_bdt, price_bdt}`
      so the UI can send them to recharge. `AcceptInviteResult` gains a `"payment_required"`→
      `"insufficient_balance"` shape (keep `granted` as-is).
- [ ] `create_intent` now rejects `SUBSCRIPTION` and `API_ACCESS` ("no longer a bKash purpose —
      fund your wallet and pay from it"). Keep the enum values for historical rows;
      `apply_verified_effects` only needs its `RECHARGE` branch for **new** transactions (leave the
      legacy branches reachable only for any already-pending old rows, or migrate those to rejected).
- [ ] Billing "Upgrade to Pro/Max" button → calls `/subscribe`; on 402 show "Top up ৳X more" linking
      to the wallet card. Show the price being deducted from balance, not a bKash modal.
- [ ] InviteAccept: priced invite shows "This API costs ৳X · your balance ৳Y". Enough → one-click
      **Pay from wallet**; not enough → **Add ৳(X−Y) to continue**.
- [ ] Tests: subscribe-from-wallet debits balance, activates the tier, writes a `subscription`
      ledger row, and grants effective tier immediately; insufficient balance → 402, no tier change,
      no debit; one-time invite accept debits and grants atomically; `create_intent(SUBSCRIPTION)`
      → 400.

**Accept:** with ৳500 in the wallet, click Upgrade to Pro → balance drops by the Pro price and the
tier flips instantly with no admin step; accept a ৳50 one-time invite → balance −৳50 and access
granted immediately; the admin Transactions page now shows only recharges.

## Phase W3 — API pricing modes + per-call metering + revenue split

The core. Owners choose how a shared API is priced; per-call APIs meter every successful call
against the consumer's balance and split the proceeds into creator earnings + platform cut.

**Touches:** `models/api.py` (+`pricing_mode`, `+included_call_quota`), migration + backfill,
`schemas/api.py`, `api/apis.py` (owner sets mode/price; validation), `api/public.py` (balance gate +
charge-at-enqueue), `workers/handlers.py` (settle on success / refund on failure),
`services/wallet.py` (helpers if needed), `services/plans.py` (read `platform_cut_pct`),
`pages/ApiDetail.tsx` (pricing controls + earnings), `lib/types.ts`.

- [ ] `ApiPricingMode` enum + column + `included_call_quota`; migration backfills
      `price_bdt > 0 → one_time` else `free`. `PATCH /api/apis/{id}` accepts `pricing_mode` +
      `price_bdt`; validate: only `can_share` tiers (or super admin) may set a paid mode; `per_call`
      requires `price_bdt > 0`; free mode forces `price_bdt = null`.
- [ ] **Gate + charge (in `public.py`, before enqueue):** after `has_access`, compute the price for
      this call: `0` unless `pricing_mode == per_call` (then `api.price_bdt`). If price > 0 **and**
      the caller is not the owner and not a super admin, do this **in a single transaction**: insert
      the `ApiExecution(QUEUED)` row and `flush()` to get its id, then `wallet.debit(caller, price,
      "call_debit", api_id, execution_id=<that id>)`, then `commit()`. If the debit raises
      `InsufficientBalance`, **roll back** (so neither the execution row nor a ledger row persists),
      return **402**, and enqueue nothing — this satisfies "logs no execution". Only enqueue the
      `jobs:exec` message after the commit succeeds. Owner-calling-own-API and super admins skip the
      debit (price 0 → the row is created and enqueued as today). Cache hits already return earlier —
      unaffected, free.
- [ ] **Settle (in `handlers.py::execute_api` finalize block):** look up the `call_debit` ledger
      row for this `execution_id`; its `amount` (negated) is the price actually charged — settle
      against **that**, never a fresh read of `api.price_bdt` (the owner may have edited the price
      mid-flight). If there is no `call_debit` row (owner/super-admin/free call), skip settling.
      - `succeeded` → `wallet.credit(api.owner_id, price − cut, "call_earning", bucket="earnings",
        api_id, execution_id, counterparty_user_id=caller)` and a `platform_cut` ledger row
        (`user_id=NULL`, `+cut`). `cut = round(price × plan_for(owner_tier).platform_cut_pct/100,
        2)`.
      - `failed` / `timeout` → `wallet.credit(caller, price, "call_refund", bucket="balance",
        api_id, execution_id)`. Creator/platform get nothing.
      Do this in the same `async with async_session()` block that writes the terminal status, so
      status + money commit together. (If the caller was owner/super-admin, price is 0 → no-op.)
- [ ] ApiDetail owner view: **Pricing** section — radio for mode (Free / One-time / Per-call /
      Subscription[W6, disabled until then]) + price field with mode-aware label ("per call",
      "one-time", "per month") and a live "you keep ~৳X, platform takes ৳Y (Z%)" hint from the
      owner's tier. An **Earnings** stat (from `earnings_bdt`) with a link to the ledger.
- [ ] Consumer 402 handling: keyed `/v1/run` callers get a JSON 402 `{detail:"insufficient wallet
      balance", price_bdt, balance_bdt}`; the in-app ApiDocs "try it" surface shows the same with a
      link to top up.
- [ ] Tests (critical): per-call success debits caller balance once, credits owner earnings =
      `price−cut`, records a `platform_cut` row, and the three legs sum to zero against the caller
      debit; failure/timeout refunds the caller in full and pays no one; insufficient balance → 402
      and **no** `ApiExecution` job enqueued; owner calling own per-call API is free; super-admin
      caller is free; cache hit does not debit.

**Accept:** owner sets an API to per-call ৳2.00; a second account with ৳10 calls it 3 times → their
balance is ৳4, owner earnings rose by `3 × (2 − cut)`, platform ledger shows `3 × cut`; force one
replay to fail → that call is refunded and nobody is paid; drain the caller's balance → the next
call returns 402 and logs no execution.

## Phase W4 — Tier re-base: call allowance, cut, invitee cap, and the pricing UI

Turn the `plan_settings` columns seeded in W1 into enforced, visible product. Re-base the tiers off
**calls consumed** and **APIs kept live**, enforce the monthly call allowance, and ship the admin +
public pricing surfaces.

**Touches:** `services/quota.py` or new `services/call_quota.py`, `api/public.py` (monthly call
allowance check), `api/apis.py` (invitee cap on allowed-emails/invites), `api/admin.py` (Plans tab
+ new fields), `schemas/admin.py`, `schemas/billing.py` (`PlanOut` +new fields),
`pages/AdminPlans` surface, `pages/Billing.tsx` (new tier cards). 

- [ ] **Monthly call allowance:** a per-user Dhaka-month counter (Redis `calls:{user}:{YYYYMM}`
      with a Postgres fallback count over `api_executions.caller_user_id`, mirroring the existing
      creation-quota pattern in `quota.py`). In `public.py`, after the balance gate, increment and
      compare to `plan_for(caller_tier).monthly_call_quota` (null = unlimited; super admins skip).
      Over quota → 429 `{detail, reset_seconds}`. Successful **per-call paid** calls still count
      (usage is usage). Decide + document whether owner-calling-own counts (default: yes, it's
      still a replay).
- [ ] **Invitee cap:** `max_invitees_per_api` enforced when adding an allowed email / creating an
      invite (count distinct allowed emails for the API; null = unlimited; super admin skips).
      Reject over-cap with a clear 403.
- [ ] Admin **Plans** tab: extend the existing three plan cards with editable `monthly_call_quota`,
      `platform_cut_pct`, `can_cashout`, `max_invitees_per_api` (validate: pct 0–100, quotas ≥ 0 or
      blank=unlimited, free tier `can_cashout=false`). Invalidate the plans cache on save (already
      wired).
- [ ] Public **Billing** tier cards rebuilt around the new story: "Calls included / mo", "Live
      APIs", "Charge invitees (all modes)", "Platform cut", "Earnings: credit (Pro) vs cashout
      (Max)", "Invitees per API". Prices/quotas read live from `plan_settings`.
- [ ] Tests: a free user hitting `monthly_call_quota` gets 429; a Pro user does not at the same
      volume; adding a 26th invitee on Pro is rejected, allowed on Max; plan edits reflect in
      `/api/billing/plans` within the cache TTL.

**Accept:** the Billing page reads as the tier table in this plan; a free account is cut off at its
monthly call limit while a Pro account keeps going; editing Pro's cut % in the admin Plans tab
changes the split applied to the next per-call sale.

## Phase W5 — Cashout (earnings → bKash) + sweep

Give earnings an exit. Max creators withdraw earnings to bKash (manual admin payout — the one
money-*out* action); everyone can sweep earnings into spendable balance.

**Touches:** new `models/cashout.py` (or in `wallet.py`), migration, `services/wallet.py` (`sweep`,
`request_cashout`), `api/billing.py` (sweep + cashout request/list), `api/admin.py` (cashout
queue + pay/reject, audited), `schemas/billing.py`, `schemas/admin.py`, `pages/Billing.tsx`
(earnings card actions), new `pages/AdminCashouts.tsx`, `AdminNav.tsx`, `routes.tsx`, `lib/*`.

- [ ] **Sweep:** `POST /api/billing/wallet/sweep {amount_bdt?}` (default: all) → in one tx,
      `debit(earnings)` + `credit(balance)` with reasons `sweep_out` / `sweep_in`. Available to any
      user with earnings.
- [ ] **Cashout request:** `POST /api/billing/wallet/cashout {amount_bdt, payout_msisdn}` — only if
      `plan_for(tier).can_cashout` (or super admin); `amount ≤ earnings_bdt`. Atomically move the
      amount out of `earnings_bdt` into a **held** state (debit earnings with reason `cashout`,
      referencing the new `cashout_requests` row) so it can't be double-spent while pending.
      `GET /api/billing/wallet/cashouts` lists the user's requests.
- [ ] **Admin queue:** `GET /api/admin/cashouts` (requested first) · `POST
      /api/admin/cashouts/{id}/pay {bkash_trx_id}` (mark paid, audit `cashout.pay`) · `POST
      /api/admin/cashouts/{id}/reject {note}` → **credit the held amount back** to the user's
      `earnings_bdt` and audit `cashout.reject`.
- [ ] Billing earnings card: balance of earnings + **Sweep to balance** and (Max only) **Cash out**
      buttons; a small list of past cashouts with status. Non-cashout tiers see an explainer +
      Sweep only.
- [ ] Admin **Cashouts** tab in AdminNav with the payout queue (amount, user, msisdn, request time,
      Pay/Reject actions with confirm modals).
- [ ] Tests: cashout on Pro rejected (403); on Max moves earnings into a pending request and out of
      the spendable earnings figure; admin reject returns the funds to earnings; admin pay records
      the TrxID and audits; sweep moves earnings→balance and is spendable on the next call.

**Accept:** a Max creator with ৳300 earnings requests a ৳200 cashout → earnings shows ৳100 pending
out; admin pays it with a TrxID and it appears in the audit log; a Pro creator can't cash out but
can sweep the ৳300 into balance and spend it on their own calls.

## Phase W6 — Creator subscription pricing mode (optional)

Recurring per-API pricing: an invitee pays a monthly price for up to `included_call_quota` calls to
that API. Lower priority than W1–W5; build only if wanted.

**Touches:** `api/invites.py` / a new `services/api_subscriptions.py`, `models/api.py`
(`included_call_quota` already added in W3), `api/public.py` (per-API monthly counter + expiry),
`workers/periodic.py` (expiry sweep), `pages/ApiDetail.tsx`, `pages/InviteAccept.tsx`.

- [ ] Accept on a `subscription`-mode API → `wallet.debit(price, "api_access", api_id)` → grant with
      `expires_at = now + 30d`; renewals extend. Per-call debits do **not** apply while a live
      grant has remaining `included_call_quota` for the Dhaka month; calls beyond the included quota
      either 429 or fall back to per-call (document the choice; default: 429 "monthly included
      calls used").
- [ ] Split the monthly price into earnings/cut at accept/renew time (same helper as W3).
- [ ] Expiry: `periodic.py` marks lapsed grants; `has_access` already rejects expired grants.
- [ ] Tests: subscribe to an API from wallet → grant with expiry + included quota; calls within
      quota are free-at-call (already paid); over quota blocked; renewal extends and re-splits.

**Accept:** an invitee subscribes to a ৳100/mo API from their wallet, calls it within the included
quota with no per-call charge, is blocked past the quota, and access lapses after 30 days unless
renewed.

---

## Explicitly out of scope (do not build)

- Automated card/bKash charging, payment gateways, or auto-recurring billing — every money-*in* is
  a manual/SMS-verified recharge; every money-*out* is a manually-approved cashout.
- Refunds of recharges to bKash, or converting recharged `balance_bdt` back to cash (only
  *earnings* leave the system, and only via cashout). This keeps the wallet from being a money-
  transfer instrument.
- Negative balances / credit / postpaid overdraft — the atomic gate forbids going below zero.
- Multi-currency, FX, or non-BDT wallets.
- Discount codes, coupons, referral credits, free-trial credits (can layer on later as
  `admin_adjust` ledger entries if ever needed).
- Per-consumer custom pricing or private price negotiation — a per-API price is the same for all
  invitees.

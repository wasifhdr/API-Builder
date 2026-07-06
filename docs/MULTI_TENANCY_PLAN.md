# Multi-Tenancy & Accounts Restructure Plan

Instructions for the implementing agent (Sonnet). Work **one phase per session**, in order — each
phase is a vertical slice that leaves the app runnable. This document extends
[BLUEPRINT.md](BLUEPRINT.md) and [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md); where this
document conflicts with either, **this document wins** for the features described here. Read
[DESIGN.md](DESIGN.md) before building any page — every new screen uses the Warm Editorial system.

**Definition of done for every phase:** acceptance criteria pass · `ruff check` clean ·
`alembic upgrade head` clean from scratch · `scripts/dev.ps1` boots everything without errors ·
existing Google-OAuth login still works end-to-end · commit with message `Tenancy N: <summary>`.

Global rules (unchanged from IMPLEMENTATION_PLAN.md):
- Windows-native Python; never move Playwright into Docker or the FastAPI process.
- Async everywhere (SQLAlchemy async + asyncpg, redis.asyncio).
- Enums `native_enum=False` storing `.value`; JSONB replaced, never mutated; timestamps UTC
  tz-aware; money `Numeric(10,2)` BDT; quotas on the Asia/Dhaka calendar day.
- Secrets only via `app/config.py` / `.env`.
- **Every new page, component, and UI state follows the Warm Editorial design system**
  ([DESIGN.md](DESIGN.md)) so it is visually indistinguishable from the rest of the app: build
  from the existing `frontend/src/components/ui` primitives (Button, Card, Table, Modal, Field,
  Badge, StatChip, PageHeader, EmptyState, …) and existing page layouts (AppShell, AdminNav)
  rather than inventing new styles; no new colors, fonts, spacing scales, or chart libraries.
  Forms, confirm modals, empty states, and error text reuse the patterns already on Settings,
  Billing, and the admin pages.

---

## Locked decisions (verified with the owner — do not re-litigate)

1. **Two roles only:** `user` and `super_admin`. No organizations/teams — "multi-tenancy" here
   means role-based access control over a shared deployment. The existing `admin` role is renamed
   to `super_admin` via data migration.
2. **whaider2002@gmail.com is the seed super admin.** The `admin_emails` env allowlist stays (same
   env var name, to avoid `.env` churn) as a *bootstrap* mechanism: any Google login matching it is
   promoted to super admin. All other promotion/demotion happens in the Admin Controls UI.
3. **Super admin powers (all approved):** manage super admins; view/edit/delete any user profile;
   view/relabel/revoke any user's API keys; suspend/unsuspend accounts; audit log of all admin
   actions; view/deactivate/delete any user's APIs and workflows; platform stats dashboard; edit
   tier prices/limits/sharing flags at runtime; **above all payment tiers** (no quota, no payment
   prompts, sharing always allowed); can create and share APIs like any user.
4. **User features (all approved):** email/password auth alongside Google; Profile page (edit name,
   phone, password — **username and email immutable**); linked sign-in methods (Google-only users
   can set a password; password users are auto-linked when they sign in with Google using the same
   email); active-sessions view with "log out everywhere else"; delete own account; per-API stats
   for APIs they own. Sharing via invite links already exists and is unchanged.
5. **Usernames: prompt at first login.** `username` is nullable in the DB. Any authenticated user
   with `username IS NULL` (all existing users, and new Google signups) is forced through a
   one-time "claim your username" screen before reaching any app page. Email/password registration
   collects the username up front. Once set, a username can never be changed — by anyone,
   including super admins.
6. **No email verification.** Registration is immediate. Google emails are trusted (verified by
   Google).

---

## Cross-cutting contracts

### User model (final shape)

`users` gains/changes (single migration in Phase T1):

| column | type | notes |
|---|---|---|
| `google_sub` | `String(64)` **nullable**, unique | was NOT NULL — password-only users have none |
| `username` | `String(30)` nullable, unique (lowercase) | regex `^[a-z0-9_]{3,30}$`; stored lowercase; immutable once set |
| `password_hash` | `Text` nullable | bcrypt; null for Google-only users |
| `phone` | `String(20)` nullable | free-form, no verification |
| `suspended_at` | `DateTime(timezone=True)` nullable | null = active; set = suspended |
| `role` | existing | data migration: `UPDATE users SET role='super_admin' WHERE role='admin'` |

`UserRole` enum becomes `USER = "user"`, `SUPER_ADMIN = "super_admin"`. Rename
`deps.require_admin` → `deps.require_super_admin` and update all imports. Frontend: every
`role !== 'admin'` check becomes `role !== 'super_admin'`; update `lib/types.ts`.

### Auth invariants

- Passwords: bcrypt via the `bcrypt` package (add `bcrypt>=4` to pyproject), min 8 chars, no
  other composition rules. Never log or return hashes.
- Sessions: unchanged Redis mechanism (`sess:{sid}` hash + `ab_session` cookie). Both login paths
  (Google, password) create identical sessions. New: on session creation, also
  `SADD user_sessions:{user_id} {sid}` and store `created_at`, `user_agent`, `ip` in the session
  hash — this powers the sessions view and lets admin actions revoke all of a user's sessions.
  When enumerating, prune sids whose `sess:{sid}` no longer exists.
- Account linking, one direction only: Google callback finding no `google_sub` match but an email
  match on an existing account **links** (sets `google_sub`, updates `picture_url`; does not
  overwrite `name` if already set). Registering with an email that already exists (either kind)
  is **rejected** with "an account with this email already exists — sign in instead".
- Login rate limit: Redis `INCR`+`EX` counter per email, max 10 failed password attempts per
  15 minutes → 429.
- Suspension: suspended users cannot log in (either path, 403 "account suspended"),
  `current_user` returns 403 for live sessions, and public API execution (`public.py`) rejects
  calls when the **key owner** or the **API owner** is suspended.

### Super-admin tier bypass

Do **not** fake a tier. Add `is_super` to the `UserWithTier` dataclass (`user.role ==
SUPER_ADMIN`). Everywhere a plan gate exists — daily creation quota (`recordings.py`/`quota.py`),
sharing gate (`can_share` in `apis.py` invite creation), payment intent creation for
subscriptions — branch on `is_super` first: super admins skip the check entirely. `/me` returns
`tier: "max"`-equivalent display data plus `role`, and the frontend hides quota chips and the
Billing upsell for super admins (Billing page shows a "you're a super admin — plans don't apply"
note).

### Audit logging

`admin_audit_log` table (Phase T5): `id` UUID pk · `actor_user_id` FK users SET NULL ·
`action` `String(60)` (e.g. `user.update`, `user.delete`, `user.suspend`, `role.promote`,
`key.revoke`, `plan.update`, `api.deactivate`, `transaction.verify`) · `target_type` `String(30)` ·
`target_id` `String(64)` · `detail` JSONB (old/new values, never secrets) · `created_at`.
Helper `services/audit.py::log_admin_action(db, actor, action, target_type, target_id, detail)` —
call it inside the same transaction as the mutation. Once it exists, retrofit it onto the
already-shipped admin mutations (tier override, transaction verify/reject, plan updates from T4).

---

## Phase T1 — Accounts schema & email/password auth (backend only)

**Touches:** `models/user.py`, new migration, `api/auth.py`, `core/deps.py`, `schemas/user.py`,
`config.py` (no new settings needed), `pyproject.toml` (+`bcrypt`).

- [ ] User model changes + role rename per the table above; one migration (schema + role data
      update). `alembic upgrade head` from an existing DB must preserve all users.
- [ ] `services/passwords.py`: `hash_password`, `verify_password` (bcrypt, cost default).
- [ ] `POST /api/auth/register` — body `{name, email, username, password}`; validates username
      regex + case-insensitive uniqueness, email uniqueness (reject if exists in any form),
      password ≥ 8; creates user (`role=user`), logs them in (same session flow as OAuth).
- [ ] `POST /api/auth/login-password` — body `{email, password}`; constant-shape errors ("invalid
      email or password") whether the email is unknown, is Google-only (no hash), or the password
      is wrong; failed-attempt rate limit; suspension check.
- [ ] `GET /api/auth/username-available?username=x` — `{available: bool}` (public; also validates
      format).
- [ ] `POST /api/auth/claim-username` — authenticated; sets username **only if currently null**;
      409 otherwise.
- [ ] Google callback: nullable-`google_sub` lookup order (by sub, then by email → link); no longer
      overwrites `name` on every login if the user has set one; still applies the
      `admin_emails` bootstrap promotion (now to `super_admin`); suspension check before creating
      the session.
- [ ] Session creation helper (shared by both login paths) that writes the session hash with
      metadata + the `user_sessions` set entry; logout removes the sid from the set.
- [ ] `UserOut`/`MeOut` gain `username`, `phone`, `has_password: bool`, `has_google: bool`;
      never expose `password_hash` or `google_sub`.
- [ ] Tests: register→login round trip; duplicate email/username rejection; username immutability;
      Google-link-by-email; rate limit; suspended login rejection.

**Accept:** existing Google login still works and your account is `super_admin` with all data
intact; `curl` register + password login sets a working session cookie; `/api/me` shows
`username: null` for pre-existing users.

## Phase T2 — Auth UI: login/register + username claim gate

**Touches:** `pages/Landing.tsx`, new `pages/ClaimUsername.tsx`, `routes.tsx`,
`hooks/useSession.tsx`, `lib/types.ts`, `lib/api.ts`.

- [ ] Landing auth card: email + password fields with a **Sign in** button; divider; **Sign in
      with Google** (existing `/api/auth/login` link); below both, ghost button **"New here?
      Create an Account!"** toggling the card into register mode (name, email, unique username
      with live availability check on blur, password + confirm). Inline field errors from the API.
- [ ] Username gate: `RequireAuth` (and `RequireSuperAdmin`) redirect to `/claim-username` whenever
      `user.username === null`; that page explains the one-time choice, validates live, warns
      "usernames are permanent", and calls `claim-username` then `refetch()`.
- [ ] `useSession` exposes the richer `User` (username, phone, role `'user' | 'super_admin'`,
      has_password, has_google).

**Accept:** in the browser — register a brand-new account and land on the dashboard; log out, log
back in with the password; sign in with your Google account and get forced through the claim
screen exactly once; refresh after claiming goes straight to the dashboard.

## Phase T3 — Profile page (self-service account management)

**Touches:** new `api/profile.py` (or extend `me.py`), `schemas/user.py`, new
`pages/Profile.tsx`, `routes.tsx`, `components/AppShell.tsx` (nav link), shared
`services/accounts.py`.

- [ ] `PATCH /api/me/profile` — `{name?, phone?}` only (`extra="forbid"`; username/email
      rejected by schema).
- [ ] `POST /api/me/password` — `{current_password?, new_password}`; if `password_hash` exists,
      `current_password` is required and verified; Google-only users set one without it. On
      change, revoke **all other sessions** (keep current sid).
- [ ] `GET /api/me/sessions` — list `{sid_prefix, created_at, user_agent, ip, current: bool}`
      (never return full sids); `POST /api/me/sessions/revoke-others`.
- [ ] `DELETE /api/me` — body requires `{confirm_username}` matching, plus `current_password` if
      one is set; implemented in `services/accounts.py::delete_user(db, user)` (also used by
      admin delete in T5): deletes the user row (FK cascades take workflows, APIs, keys, grants,
      subscriptions, transactions), then deletes every Redis session in `user_sessions:{id}`.
      Response clears the cookie.
- [ ] `pages/Profile.tsx` sections: **Identity** (username + email, shown read-only with a lock
      hint) · **Details** (name, phone — editable) · **Sign-in methods** (Google linked? password
      set? set/change password form) · **Active sessions** (list + revoke-others) · **Danger
      zone** (delete account with type-your-username confirm modal). Nav: "Profile" link in
      AppShell next to Settings; Settings page stays recorder-prefs only.

**Accept:** edit name/phone and see it persist; set a password on your Google account then log in
with it; revoke-others kills a second browser's session; deleting a scratch account removes its
APIs and returns its slugs/username to availability.

## Phase T4 — DB-backed plan config & super-admin tier bypass

**Touches:** new `models/plan_settings.py` + migration, `services/plans.py` (async rewrite),
callers (`me.py`, `recordings.py`, `payments.py`, `billing.py`), `core/deps.py`, `api/admin.py`,
new admin Plans tab, quota/share/billing gates, `pages/Billing.tsx`.

- [ ] `plan_settings` table: `tier` `String(10)` pk (`free|pro|max`) · `price_bdt Integer` ·
      `daily_creation_limit Integer` nullable (null = unlimited) · `can_share Boolean` ·
      `updated_at`. Migration seeds from current values (free: 0/5/false, pro:
      `plan_price_pro_bdt`/50/true, max: `plan_price_max_bdt`/null/true). The env price vars
      remain **only** as seed defaults; runtime reads come from the DB.
- [ ] `services/plans.py`: `async get_plans(db)` / `async plan_for(tier, db)` reading the table
      with a small in-process TTL cache (~30 s; invalidate on admin update). Update every caller.
- [ ] Super-admin bypass exactly per the cross-cutting contract: quota skip, share-gate skip,
      subscription-intent rejection ("super admins don't need plans"), `/me` signalling, frontend
      hiding of quota chip + Billing upsell.
- [ ] `GET /api/admin/plans`, `PATCH /api/admin/plans/{tier}` — `{price_bdt?,
      daily_creation_limit?, can_share?}`; price ≥ 0; free tier price locked at 0.
- [ ] Frontend admin **Plans** tab (AdminNav): three editable plan cards with save-per-tier;
      public Billing page reflects DB prices immediately.

**Accept:** change Pro's price in the UI → `/api/billing/plans` and the Billing page show it; a
new payment intent uses the new amount; as super admin you can create workflows past the free
daily limit and create invites without a subscription.

## Phase T5 — Admin Controls: users, super admins, suspension, keys, audit log

**Touches:** `api/admin.py`, `schemas/admin.py`, new `models/audit.py` + migration,
`services/audit.py`, `services/accounts.py`, `pages/AdminUsers.tsx` (upgrade), new
`pages/AdminControls.tsx`, new `pages/AdminAudit.tsx`, `AdminNav.tsx`, `routes.tsx`.

- [ ] `admin_audit_log` table + `log_admin_action` helper per the contract; retrofit onto tier
      override, transaction verify/reject, and T4 plan updates.
- [ ] Users API: `GET /api/admin/users` (add username, phone, suspended_at, counts of
      apis/workflows/keys) · `GET /api/admin/users/{id}` (full detail incl. subscription) ·
      `PATCH /api/admin/users/{id}` (`name?, phone?, role?, suspended?` — booleans map to
      `suspended_at`) · `DELETE /api/admin/users/{id}` (uses `services/accounts.delete_user`).
- [ ] Guards (server-side, tested): an admin cannot suspend, demote, or delete **themself**; the
      **last remaining super admin** can never be demoted, suspended, or deleted; role changes
      only between `user` ↔ `super_admin`.
- [ ] Suspension enforcement everywhere per the auth contract (login, live sessions via
      `current_user`, public execution for key owner and API owner) + revoke all sessions on
      suspend.
- [ ] Keys admin: `GET /api/admin/users/{id}/keys` (incl. revoked, with `key_prefix` only) ·
      `PATCH .../keys/{key_id}` (relabel) · `DELETE .../keys/{key_id}` (revoke). All audited.
- [ ] `GET /api/admin/audit-log?limit&offset` newest-first with actor email/username.
- [ ] Frontend: **Admin Controls** page (list of super admins; promote by email/username lookup
      with confirm modal; demote with confirm) · upgraded **Users** page (search box, row →
      detail panel with editable fields, suspend toggle, keys table with relabel/revoke, tier
      override moved here, delete with type-email confirm) · **Audit log** page (filterable
      table). AdminNav tabs: Users · Admin Controls · Plans · Transactions · SMS feed · Audit log.

**Accept:** promote a scratch account to super admin and back; the self/last-admin guards return
403 and the UI explains them; suspend a user → their session dies, password login blocked, their
published API returns an error to callers; every one of those actions appears in the audit log
with actor and detail.

## Phase T6 — Moderation & platform stats dashboard

**Touches:** `api/admin.py`, `schemas/admin.py`, new `pages/AdminOverview.tsx`, new admin APIs
tab, `AdminNav.tsx`, `routes.tsx`.

- [ ] `GET /api/admin/apis` — all APIs with owner (email/username), visibility, `is_active`,
      spec_status, lifetime execution count, created_at; search by name/slug/owner.
- [ ] `PATCH /api/admin/apis/{id}` (`is_active` toggle — deactivation makes public calls return
      403 while keeping data) · `DELETE /api/admin/apis/{id}` (hard delete, cascades executions/
      grants/invites). Both audited.
- [ ] `GET /api/admin/users/{id}/workflows` + `DELETE /api/admin/workflows/{id}` (audited) —
      surfaced inside the T5 user-detail panel.
- [ ] `GET /api/admin/stats` — `{total_users, new_users_7d, suspended_users, total_apis,
      active_apis, executions_by_day: [{date, total, succeeded}] (last 14 Dhaka days),
      success_rate_7d, revenue_verified_bdt (sum of verified transactions), pending_payments}`.
      Aggregate with SQL (`func.count`/`date_trunc` on the Dhaka timezone), not Python loops.
- [ ] Frontend: **Overview** tab (first tab in AdminNav) with StatChips for the headline numbers
      and a simple executions-per-day bar strip (plain divs, per DESIGN.md — no chart library);
      **APIs** tab with the moderation table. Final AdminNav: Overview · Users · Admin Controls ·
      APIs · Plans · Transactions · SMS feed · Audit log.

**Accept:** Overview numbers match reality on your dev DB; deactivating an API makes a keyed call
fail with 403 and reactivating restores it; deleting a scratch API removes it from its owner's
dashboard; all moderation lands in the audit log.

## Phase T7 — Owner-facing API stats

**Touches:** `api/apis.py`, `schemas/api.py`, `pages/ApiDetail.tsx`.

- [ ] `GET /api/apis/{api_id}/stats` — owner or super admin only:
      `{total_calls, calls_7d, success_rate_7d, avg_duration_ms_7d, cache_hit_rate_7d,
      calls_by_day: [{date, total, succeeded}] (last 14 Dhaka days),
      top_consumers: [{username|email-fallback, calls_30d}] (≤5, from caller_user_id),
      last_called_at}`. SQL aggregation over `api_executions` (the `ix_exec_api_created` index
      exists for this); no N+1.
- [ ] ApiDetail gains a **Stats** section above the executions list: StatChips + the same
      bar-strip pattern as the admin overview + top-consumers mini-table. Empty state for
      never-called APIs.

**Accept:** call a published API a few times (mix of cache hits and a failure), reload ApiDetail:
counts, success rate, and consumer list are correct; a non-owner without super admin gets 404/403
on the stats endpoint.

---

## Explicitly out of scope (do not build)

- Organizations, teams, or per-tenant data isolation beyond ownership checks.
- Email verification, password reset via email, 2FA (no SMTP in this deployment).
- Username or email changes after creation — for anyone.
- Impersonation ("log in as user").
- Per-tier API *execution* rate limiting (only the existing daily creation quota is tier-gated;
  editable in T4).

export type UserRole = 'user' | 'super_admin'
export type PlanTier = 'free' | 'pro' | 'max'

export type StepValue = { literal: string } | { param: string }

export interface Step {
  i: number
  type: 'goto' | 'click' | 'fill' | 'press' | 'select_option' | 'scroll_page' | 'extract'
  selectors?: string[]
  url?: string
  value?: StepValue
  key?: string
}

export type RecorderStatus = 'connecting' | 'launching' | 'ready' | 'closed' | 'died'

export interface PickCandidate {
  selectors: string[]
  preview: string | null
  count: number
  generalized: string | null
}

export interface Parameter {
  name: string
  type: string
  required: boolean
  example: string | null
  description: string | null
  source_step: number | null
}

export interface ExtractionField {
  name: string
  selector: string
  take: string
  transform?: string
}

export interface ExtractionConfig {
  mode: 'single' | 'list'
  root?: string
  fields: ExtractionField[]
}

/** AI-suggested parameter for a recorded fill/select_option step — advisory
 * only; accepting one sends the existing mark_param command. */
export interface ParameterSuggestion {
  step_i: number
  name: string
  type: string
  example: string | null
  description: string | null
  confidence: number | null
}

/** AI-suggested name/take/transform for an extraction field, matched back to
 * its selector — advisory only; accepting one updates the extraction config
 * via the existing set_extraction command. */
export interface ExtractionFieldSuggestion {
  selector: string
  name: string
  take: string
  transform: string
}

export type WorkflowStatus = 'recording' | 'draft' | 'ready' | 'archived'

export interface WorkflowSummary {
  id: string
  name: string
  start_url: string
  status: WorkflowStatus
  created_at: string
  updated_at: string
}

export type SpecStatus = 'pending' | 'generating' | 'ready' | 'failed'
export type ExecutionStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'timeout'
export type ApiPricingMode = 'free' | 'one_time' | 'per_call' | 'subscription'

export interface CustomApi {
  id: string
  workflow_id: string
  owner_id: string
  slug: string
  name: string
  description: string | null
  visibility: 'private' | 'shared'
  price_bdt: string | null
  pricing_mode: ApiPricingMode
  included_call_quota: number | null
  spec_status: SpecStatus
  openapi_spec: Record<string, unknown> | null
  cache_ttl_seconds: number
  is_active: boolean
  created_at: string
}

export type GrantSource = 'invite' | 'purchase' | 'admin'

export interface Invite {
  id: string
  api_id: string
  token: string
  max_uses: number | null
  use_count: number
  expires_at: string | null
  revoked_at: string | null
  created_at: string
}

export interface Grant {
  id: string
  api_id: string
  user_id: string
  granted_via: GrantSource
  expires_at: string | null
  revoked_at: string | null
  created_at: string
}

export interface AllowedEmail {
  id: string
  api_id: string
  email: string
  created_at: string
}

export interface InvitePreview {
  api_name: string
  api_slug: string
  price_bdt: string | null
  pricing_mode: ApiPricingMode
  valid: boolean
  reason: string | null
}

export interface AcceptInviteResult {
  status: 'granted' | 'insufficient_balance'
  price_bdt: string | null
  balance_bdt: string | null
}

export interface SubscribeResult {
  tier: PlanTier
  expires_at: string
  balance_bdt: string
}

export interface ApiExecution {
  id: string
  status: ExecutionStatus
  params: Record<string, unknown>
  result: unknown
  error_message: string | null
  failure_artifact_path: string | null
  cache_hit: boolean
  created_at: string
  duration_ms: number | null
}

export type PaymentPurpose = 'subscription' | 'api_access' | 'recharge'
export type PaymentStatus = 'pending' | 'submitted' | 'verified' | 'rejected' | 'expired'
export type VerificationMethod = 'auto_sms' | 'manual_admin'

export interface Plan {
  tier: PlanTier
  name: string
  price_bdt: number
  daily_creation_limit: number | null
  can_share: boolean
  monthly_call_quota: number | null
  platform_cut_pct: string
  can_cashout: boolean
  max_invitees_per_api: number | null
}

export interface PaymentIntent {
  id: string
  purpose: PaymentPurpose
  plan_tier: PlanTier | null
  api_id: string | null
  amount_expected_bdt: string
  amount_received_bdt: string | null
  bkash_trx_id: string | null
  status: PaymentStatus
  verification_method: VerificationMethod | null
  verified_at: string | null
  note: string | null
  created_at: string
}

export interface Wallet {
  balance_bdt: string
  earnings_bdt: string
  can_cashout: boolean
  platform_cut_pct: string
}

export type CashoutStatus = 'requested' | 'paid' | 'rejected'

export interface Cashout {
  id: string
  amount_bdt: string
  payout_msisdn: string
  status: CashoutStatus
  bkash_trx_id: string | null
  note: string | null
  created_at: string
  decided_at: string | null
}

export interface SweepResult {
  swept_bdt: string
  balance_bdt: string
  earnings_bdt: string
}

export interface AdminCashout extends Cashout {
  user_id: string
  user_email: string
  user_username: string | null
}

export type WalletLedgerReason =
  | 'recharge'
  | 'subscription'
  | 'api_access'
  | 'call_debit'
  | 'call_refund'
  | 'call_earning'
  | 'platform_cut'
  | 'sweep_out'
  | 'sweep_in'
  | 'cashout'
  | 'admin_adjust'

export interface WalletLedgerEntry {
  id: string
  bucket: 'balance' | 'earnings'
  amount_bdt: string
  reason: WalletLedgerReason
  balance_after_bdt: string
  execution_id: string | null
  api_id: string | null
  transaction_id: string | null
  counterparty_user_id: string | null
  created_at: string
}

export interface AdminTransaction extends PaymentIntent {
  user_id: string
  user_email: string
  user_username: string | null
}

export interface AdminSms {
  id: string
  received_at: string
  raw_text: string
  sms_sender: string | null
  parsed_trx_id: string | null
  parsed_amount_bdt: string | null
  parsed_sender_msisdn: string | null
  matched_transaction_id: string | null
}

export interface AdminUser {
  id: string
  email: string
  name: string | null
  role: UserRole
  effective_tier: PlanTier
  username: string | null
  phone: string | null
  suspended_at: string | null
  workflow_count: number
  api_count: number
  key_count: number
}

export interface AdminSubscription {
  tier: PlanTier
  status: 'active' | 'expired' | 'cancelled'
  expires_at: string
}

export interface AdminUserDetail extends AdminUser {
  created_at: string
  has_password: boolean
  has_google: boolean
  subscription: AdminSubscription | null
}

export interface AdminUserUpdate {
  tier?: PlanTier
  name?: string | null
  phone?: string | null
  role?: UserRole
  suspended?: boolean
}

export interface AdminKey {
  id: string
  label: string
  key_prefix: string
  last_used_at: string | null
  revoked_at: string | null
  created_at: string
}

export interface AdminAuditLogEntry {
  id: string
  actor_user_id: string | null
  actor_email: string | null
  actor_username: string | null
  action: string
  target_type: string
  target_id: string
  detail: Record<string, unknown>
  created_at: string
}

export interface AdminPlan {
  tier: PlanTier
  price_bdt: number
  daily_creation_limit: number | null
  can_share: boolean
  monthly_call_quota: number | null
  platform_cut_pct: string
  can_cashout: boolean
  max_invitees_per_api: number | null
  updated_at: string
}

export interface AdminPlanUpdate {
  price_bdt?: number
  daily_creation_limit?: number | null
  can_share?: boolean
  monthly_call_quota?: number | null
  platform_cut_pct?: string
  can_cashout?: boolean
  max_invitees_per_api?: number | null
}

export interface UserSettings {
  use_saved_logins?: boolean
  recorder_channel?: 'chromium' | 'chrome'
}

export interface User {
  id: string
  email: string
  username: string | null
  name: string | null
  phone: string | null
  picture_url: string | null
  role: UserRole
  has_password: boolean
  has_google: boolean
  settings: UserSettings
  created_at: string
  tier: PlanTier
  quota_used_today: number
  quota_limit: number | null
}

export interface RegisterPayload {
  name: string
  email: string
  username: string
  password: string
}

export interface ApiStatsDay {
  date: string
  total: number
  succeeded: number
}

export interface ApiStatsConsumer {
  name: string
  calls_30d: number
}

export interface ApiStats {
  total_calls: number
  calls_7d: number
  success_rate_7d: number
  avg_duration_ms_7d: number | null
  cache_hit_rate_7d: number
  calls_by_day: ApiStatsDay[]
  top_consumers: ApiStatsConsumer[]
  last_called_at: string | null
}

export interface Session {
  sid_prefix: string
  created_at: string
  user_agent: string | null
  ip: string | null
  current: boolean
}

export interface AdminApi {
  id: string
  workflow_id: string
  owner_id: string
  owner_email: string
  owner_username: string | null
  slug: string
  name: string
  visibility: 'private' | 'shared'
  is_active: boolean
  spec_status: SpecStatus
  execution_count: number
  created_at: string
}

export interface AdminApiUpdate {
  is_active: boolean
}

export interface AdminWorkflow {
  id: string
  name: string
  status: WorkflowStatus
  created_at: string
}

export interface AdminWorkflowDetail {
  id: string
  name: string
  status: string
  steps: Step[]
  parameters: Parameter[]
  extraction: { main?: ExtractionConfig }
}

export interface AdminStatsDay {
  date: string
  total: number
  succeeded: number
}

export interface AdminStats {
  total_users: number
  new_users_7d: number
  suspended_users: number
  total_apis: number
  active_apis: number
  executions_by_day: AdminStatsDay[]
  success_rate_7d: number
  revenue_verified_bdt: string
  pending_payments: number
}

export interface RunSuccess {
  data: unknown
  meta: { cached: boolean; duration_ms?: number; execution_id?: string }
}

export interface RunAccepted {
  execution_id: string
  status_url: string
}

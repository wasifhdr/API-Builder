export type UserRole = 'user' | 'admin'
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

export type SpecStatus = 'pending' | 'generating' | 'ready' | 'failed'
export type ExecutionStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'timeout'

export interface CustomApi {
  id: string
  workflow_id: string
  slug: string
  name: string
  description: string | null
  visibility: 'private' | 'shared'
  price_bdt: string | null
  spec_status: SpecStatus
  openapi_spec: Record<string, unknown> | null
  cache_ttl_seconds: number
  is_active: boolean
  created_at: string
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

export type PaymentPurpose = 'subscription' | 'api_access'
export type PaymentStatus = 'pending' | 'submitted' | 'verified' | 'rejected' | 'expired'
export type VerificationMethod = 'auto_sms' | 'manual_admin'

export interface Plan {
  tier: PlanTier
  name: string
  price_bdt: number
  daily_creation_limit: number | null
  can_share: boolean
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

export interface AdminTransaction extends PaymentIntent {
  user_id: string
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
}

export interface UserSettings {
  use_saved_logins?: boolean
  recorder_channel?: 'chromium' | 'chrome'
}

export interface User {
  id: string
  email: string
  name: string | null
  picture_url: string | null
  role: UserRole
  settings: UserSettings
  created_at: string
  tier: PlanTier
  quota_used_today: number
  quota_limit: number | null
}

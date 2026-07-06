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

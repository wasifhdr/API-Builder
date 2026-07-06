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

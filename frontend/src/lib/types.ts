export type UserRole = 'user' | 'admin'
export type PlanTier = 'free' | 'pro' | 'max'

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

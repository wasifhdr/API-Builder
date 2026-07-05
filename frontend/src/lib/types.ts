export type UserRole = 'user' | 'admin'

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
}

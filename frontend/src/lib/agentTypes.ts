export type AgentRunStatus =
  | 'planning'
  | 'awaiting_confirm'
  | 'driving'
  | 'distilling'
  | 'verifying'
  | 'repairing'
  | 'succeeded'
  | 'failed'
  | 'cancelled'

export interface AgentPlanParameter {
  name: string
  type: string
  required: boolean
  drive_value: string
  verify_value: string
  description: string | null
}

export interface AgentPlanField {
  name: string
  type?: string
}

export interface AgentPlan {
  url?: string
  summary?: string
  result_shape?: 'list' | 'detail'
  parameters?: AgentPlanParameter[]
  fields?: AgentPlanField[]
}

export interface AgentRun {
  id: string
  status: AgentRunStatus
  prompt: string
  resolved_url: string | null
  plan: AgentPlan
  attempt: number
  failure_reason: string | null
  workflow_id: string | null
  created_at: string
}

export interface AgentActivityEntry {
  tool: string
  detail: string
}

export interface AgentVerifyCheck {
  name: string
  passed: boolean
  detail: string
}

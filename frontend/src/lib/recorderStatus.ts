import type { BadgeVariant } from '../components/ui'
import type { RecorderStatus } from './types'

export const STATUS_LABEL: Record<RecorderStatus, string> = {
  connecting: 'Connecting…',
  launching: 'Launching browser…',
  ready: 'Recording',
  closed: 'Closed',
  died: 'Recorder crashed',
}

export const STATUS_BADGE: Record<RecorderStatus, BadgeVariant> = {
  connecting: 'neutral',
  launching: 'pending',
  ready: 'success',
  closed: 'neutral',
  died: 'failed',
}

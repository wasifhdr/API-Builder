import type { ComponentProps } from 'react'
import { STATUS_BADGE, STATUS_LABEL } from '../lib/recorderStatus'
import type { RecorderStatus, Step } from '../lib/types'
import ExtractionWizard from './ExtractionWizard'
import RecorderStepList from './RecorderStepList'
import { Badge, Button } from './ui'

interface RecorderPipCardProps {
  status: RecorderStatus
  steps: Step[]
  interactive: boolean
  wizard: ComponentProps<typeof ExtractionWizard>
  onRecord: () => void
  onUndo: (i: number) => void
  onMarkParam: (stepI: number, name: string) => void
  onSave: () => void
  onCancel: () => void
}

/**
 * Compact recorder controls rendered into the always-on-top Document
 * Picture-in-Picture window, so they stay visible over the maximized
 * recording browser. The full editor (extraction, test output) stays in the
 * main tab.
 */
export default function RecorderPipCard({
  status,
  steps,
  interactive,
  wizard,
  onRecord,
  onUndo,
  onMarkParam,
  onSave,
  onCancel,
}: RecorderPipCardProps) {
  return (
    <div className="flex h-dvh flex-col gap-3 bg-cream p-3">
      <div className="flex items-center justify-between gap-2">
        <Badge variant={STATUS_BADGE[status]} pulse={status === 'ready'}>
          {STATUS_LABEL[status]}
        </Badge>
        <span className="font-mono text-sm tabular-nums text-ink/70">
          {steps.length} step{steps.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="flex items-center gap-2">
        <Button variant="default" size="sm" onClick={onRecord} disabled={!interactive}>
          Record
        </Button>
        <ExtractionWizard {...wizard} disabled={!interactive} />
      </div>

      <RecorderStepList
        steps={steps}
        interactive={interactive}
        onUndo={onUndo}
        onMarkParam={onMarkParam}
        className="min-h-0 flex-1"
      />

      <div className="flex items-center gap-2">
        <Button variant="primary" size="sm" onClick={onSave} disabled={!interactive || steps.length === 0}>
          Save
        </Button>
        <Button variant="danger-ghost" size="sm" onClick={onCancel} disabled={!interactive}>
          Cancel
        </Button>
      </div>
    </div>
  )
}

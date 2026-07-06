import { STATUS_BADGE, STATUS_LABEL } from '../lib/recorderStatus'
import type { PickCandidate, RecorderStatus, Step } from '../lib/types'
import RecorderStepList from './RecorderStepList'
import { Badge, Button, cardClasses } from './ui'

interface RecorderPipCardProps {
  status: RecorderStatus
  steps: Step[]
  mode: 'record' | 'pick'
  interactive: boolean
  pickResult: PickCandidate | null
  onSetMode: (mode: 'record' | 'pick') => void
  onUndo: (i: number) => void
  onMarkParam: (stepI: number, name: string) => void
  onUseAsListRoot: () => void
  onAddField: () => void
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
  mode,
  interactive,
  pickResult,
  onSetMode,
  onUndo,
  onMarkParam,
  onUseAsListRoot,
  onAddField,
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
        <Button
          variant={mode === 'record' ? 'ink' : 'default'}
          size="sm"
          onClick={() => onSetMode('record')}
          disabled={!interactive}
        >
          Record
        </Button>
        <Button
          variant={mode === 'pick' ? 'ink' : 'default'}
          size="sm"
          onClick={() => onSetMode('pick')}
          disabled={!interactive}
        >
          Pick element
        </Button>
      </div>

      {mode === 'pick' && (
        <div className={`${cardClasses({ variant: 'callout', accent: 'blue' })} space-y-2`}>
          {!pickResult && <p className="text-xs text-ink/70">Click an element in the browser to pick it.</p>}
          {pickResult && (
            <>
              <p className="truncate font-mono text-xs text-ink/80">{pickResult.selectors[0]}</p>
              <p className="text-xs text-ink/60">
                {pickResult.count} similar element(s)
                {pickResult.preview && <> — &ldquo;{pickResult.preview.slice(0, 40)}&rdquo;</>}
              </p>
              <div className="flex gap-2">
                <Button variant="ink" size="sm" onClick={onUseAsListRoot}>
                  Use as list root
                </Button>
                <Button size="sm" onClick={onAddField}>
                  Add as field
                </Button>
              </div>
            </>
          )}
        </div>
      )}

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

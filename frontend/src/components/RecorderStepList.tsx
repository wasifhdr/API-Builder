import { useEffect, useRef, useState } from 'react'
import { describeStep } from '../lib/steps'
import type { Step } from '../lib/types'

const STEP_TONE: Record<Step['type'], string> = {
  goto: 'text-blue-soft',
  click: 'text-gold',
  fill: 'text-gold',
  select_option: 'text-gold',
  press: 'text-cream/60',
  scroll_page: 'text-cream/60',
  extract: 'text-green-soft',
}

interface RecorderStepListProps {
  steps: Step[]
  interactive: boolean
  onUndo: (i: number) => void
  onMarkParam: (stepI: number, name: string) => void
  /** Sizing — the page uses a fixed height, the pop-out card fills its column. */
  className?: string
}

export default function RecorderStepList({
  steps,
  interactive,
  onUndo,
  onMarkParam,
  className = 'h-72',
}: RecorderStepListProps) {
  const [paramFormFor, setParamFormFor] = useState<number | null>(null)
  const [paramName, setParamName] = useState('')
  const listRef = useRef<HTMLDivElement>(null)
  const prevCount = useRef(steps.length)

  // Keep the newest step in view — matters most in the small pop-out card.
  useEffect(() => {
    if (steps.length > prevCount.current && listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight
    }
    prevCount.current = steps.length
  }, [steps.length])

  function submitParamForm(stepI: number) {
    if (!paramName.trim()) return
    onMarkParam(stepI, paramName.trim())
    setParamFormFor(null)
    setParamName('')
  }

  return (
    <div
      ref={listRef}
      className={`overflow-y-auto rounded-card border-2 border-ink bg-ink p-3 font-mono text-xs leading-relaxed text-cream/90 ${className}`}
    >
      {steps.length === 0 && <p className="text-cream/50">No steps recorded yet.</p>}
      {steps.map((step) => {
        const isValueStep = step.type === 'fill' || step.type === 'select_option'
        const isParam = step.value && 'param' in step.value
        return (
          <div key={step.i} className="border-b border-cream/10 py-1.5 last:border-0">
            <div className="flex items-center justify-between gap-2">
              <span>
                <span className={STEP_TONE[step.type]}>{step.type}</span>{' '}
                <span className="text-cream/90">{describeStep(step)}</span>
              </span>
              <div className="flex shrink-0 items-center gap-2">
                {isValueStep && !isParam && paramFormFor !== step.i && (
                  <button
                    type="button"
                    onClick={() => setParamFormFor(step.i)}
                    disabled={!interactive}
                    className="rounded-pill border border-cream/30 px-2 py-0.5 text-[11px] font-bold text-cream hover:bg-cream/10 disabled:opacity-40"
                  >
                    Make parameter
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => onUndo(step.i)}
                  disabled={!interactive}
                  className="text-[11px] font-bold text-red-soft hover:text-red-soft/70 disabled:opacity-40"
                >
                  Undo
                </button>
              </div>
            </div>
            {paramFormFor === step.i && (
              <div className="mt-2 flex items-center gap-2">
                <input
                  type="text"
                  autoFocus
                  value={paramName}
                  onChange={(e) => setParamName(e.target.value)}
                  placeholder="parameter name, e.g. query"
                  className="flex-1 rounded-control border border-cream/30 bg-ink px-2 py-1 text-xs text-cream placeholder:text-cream/40 focus-visible:outline-2 focus-visible:outline-gold"
                />
                <button
                  type="button"
                  onClick={() => submitParamForm(step.i)}
                  className="rounded-pill bg-gold px-2 py-1 text-[11px] font-bold text-ink"
                >
                  Confirm
                </button>
                <button type="button" onClick={() => setParamFormFor(null)} className="text-[11px] text-cream/60">
                  Cancel
                </button>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { describeStep } from '../lib/steps'
import type { ParameterSuggestion, Step } from '../lib/types'

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
  /** AI-suggested parameters, keyed by step_i — optional so callers that don't
   * offer suggestions (e.g. the pop-out PiP card) can omit these entirely. */
  suggestions?: ParameterSuggestion[]
  onAcceptSuggestion?: (suggestion: ParameterSuggestion) => void
  onDismissSuggestion?: (stepI: number) => void
  /** Sizing — the page uses a fixed height, the pop-out card fills its column. */
  className?: string
}

export default function RecorderStepList({
  steps,
  interactive,
  onUndo,
  onMarkParam,
  suggestions = [],
  onAcceptSuggestion,
  onDismissSuggestion,
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
        const suggestion = isValueStep && !isParam ? suggestions.find((sg) => sg.step_i === step.i) : undefined
        return (
          <div key={step.i} className="border-b border-cream/10 py-1.5 last:border-0">
            <div className="flex items-center justify-between gap-2">
              <span>
                <span className={STEP_TONE[step.type]}>{step.type}</span>{' '}
                <span className="text-cream/90">{describeStep(step)}</span>
              </span>
              <div className="flex shrink-0 items-center gap-2">
                {isValueStep && !isParam && !suggestion && paramFormFor !== step.i && (
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
            {suggestion && (
              <div className="mt-2 flex flex-wrap items-center gap-2 rounded-control border border-gold/40 bg-gold/10 px-2 py-1.5">
                <span className="text-[11px] text-gold">✨ Suggested:</span>
                <span className="font-bold text-cream">
                  {suggestion.name} <span className="font-normal text-cream/60">({suggestion.type})</span>
                </span>
                {suggestion.description && <span className="text-cream/60">— {suggestion.description}</span>}
                <div className="ml-auto flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onAcceptSuggestion?.(suggestion)}
                    disabled={!interactive}
                    className="rounded-pill bg-gold px-2 py-0.5 text-[11px] font-bold text-ink disabled:opacity-40"
                  >
                    Accept
                  </button>
                  <button
                    type="button"
                    onClick={() => onDismissSuggestion?.(step.i)}
                    className="text-[11px] text-cream/60 hover:text-cream"
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            )}
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

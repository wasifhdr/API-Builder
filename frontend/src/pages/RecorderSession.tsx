import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import ExtractionEditor from '../components/ExtractionEditor'
import { Badge, type BadgeVariant, Button, buttonClasses, cardClasses, StatChip } from '../components/ui'
import { useRecorder } from '../hooks/useRecorder'
import { api, ApiError } from '../lib/api'
import { describeStep } from '../lib/steps'
import type { ExtractionConfig, RecorderStatus, Step } from '../lib/types'

const STATUS_LABEL: Record<RecorderStatus, string> = {
  connecting: 'Connecting…',
  launching: 'Launching browser…',
  ready: 'Recording',
  closed: 'Closed',
  died: 'Recorder crashed',
}

const STATUS_BADGE: Record<RecorderStatus, BadgeVariant> = {
  connecting: 'neutral',
  launching: 'pending',
  ready: 'success',
  closed: 'neutral',
  died: 'failed',
}

const STEP_TONE: Record<Step['type'], string> = {
  goto: 'text-blue-soft',
  click: 'text-gold',
  fill: 'text-gold',
  select_option: 'text-gold',
  press: 'text-cream/60',
  scroll_page: 'text-cream/60',
  extract: 'text-green-soft',
}

const EMPTY_EXTRACTION: ExtractionConfig = { mode: 'list', root: '', fields: [] }

export default function RecorderSession() {
  const { workflowId } = useParams<{ workflowId: string }>()
  const navigate = useNavigate()
  const {
    status,
    steps,
    error,
    saved,
    mode,
    pickResult,
    extractionResult,
    warnings,
    undoStep,
    bringToFront,
    setMode,
    markParam,
    setExtraction,
    testExtraction,
    save,
    cancel,
  } = useRecorder(workflowId!)
  const interactive = status === 'ready'

  const [extraction, setExtractionState] = useState<ExtractionConfig>(EMPTY_EXTRACTION)
  const [paramFormFor, setParamFormFor] = useState<number | null>(null)
  const [paramName, setParamName] = useState('')
  const [deleting, setDeleting] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  async function deleteWorkflow() {
    setDeleting(true)
    setDeleteError(null)
    try {
      await api.delete(`/workflows/${workflowId}`)
      navigate('/dashboard')
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : 'Failed to delete')
      setConfirmingDelete(false)
    } finally {
      setDeleting(false)
    }
  }

  function updateExtraction(next: ExtractionConfig) {
    setExtractionState(next)
    setExtraction(next)
  }

  function addFieldFromPick() {
    if (!pickResult) return
    updateExtraction({
      ...extraction,
      fields: [
        ...extraction.fields,
        { name: `field${extraction.fields.length + 1}`, selector: pickResult.selectors[0], take: 'text', transform: 'none' },
      ],
    })
  }

  function useAsListRoot() {
    if (!pickResult?.generalized) return
    updateExtraction({ ...extraction, mode: 'list', root: pickResult.generalized })
  }

  function submitParamForm(stepI: number) {
    if (!paramName.trim()) return
    markParam(stepI, paramName.trim())
    setParamFormFor(null)
    setParamName('')
  }

  if (saved) {
    return (
      <AppShell>
        <div className="mx-auto max-w-md text-center">
          <p className="font-display text-display-sm">Workflow saved.</p>
          <div className="mt-6 flex items-center justify-center gap-3">
            <Button variant="primary" onClick={() => navigate(`/workflows/${workflowId}/edit`)}>
              Edit workflow
            </Button>
            <Button variant="ghost" onClick={() => navigate('/dashboard')}>
              Back to dashboard
            </Button>
          </div>
        </div>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <Link to="/dashboard" className={buttonClasses('ghost', 'sm')}>
          &larr; Dashboard
        </Link>
        <div className="flex items-center gap-3">
          <StatChip value={steps.length} label="steps recorded" />
          <Badge variant={STATUS_BADGE[status]} pulse={status === 'ready'}>
            {STATUS_LABEL[status]}
          </Badge>
        </div>
      </div>

      <p className="mb-4 text-sm text-ink/70">
        A Chromium window opened on your desktop — browse there and steps will appear here live.
      </p>
      {error && <p className="mb-4 text-sm font-medium text-red-deep">{error}</p>}
      {warnings.map((w, i) => (
        <div key={i} className={`${cardClasses({ variant: 'callout', accent: 'gold' })} mb-3`}>
          <p className="text-sm text-ink/80">{w}</p>
        </div>
      ))}

      {!interactive && (
        <div className={`${cardClasses({ variant: 'callout', accent: 'gold' })} mb-4 flex flex-wrap items-center justify-between gap-3`}>
          <p className="text-sm text-ink/80">
            {status === 'died'
              ? "This session isn't responding. You can delete it and start over."
              : 'Stuck waiting? You can delete this workflow instead of waiting.'}
          </p>
          {deleteError && <p className="w-full text-sm font-medium text-red-deep">{deleteError}</p>}
          {confirmingDelete ? (
            <div className="flex items-center gap-2">
              <span className="text-sm text-ink/70">Delete this workflow?</span>
              <Button variant="danger-ghost" size="sm" onClick={deleteWorkflow} disabled={deleting}>
                {deleting ? 'Deleting…' : 'Confirm delete'}
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setConfirmingDelete(false)} disabled={deleting}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button variant="danger-ghost" size="sm" onClick={() => setConfirmingDelete(true)}>
              Delete workflow
            </Button>
          )}
        </div>
      )}

      <div className="mb-4 flex items-center gap-2">
        <Button
          variant={mode === 'record' ? 'ink' : 'default'}
          size="sm"
          onClick={() => setMode('record')}
          disabled={!interactive}
        >
          Record
        </Button>
        <Button
          variant={mode === 'pick' ? 'ink' : 'default'}
          size="sm"
          onClick={() => setMode('pick')}
          disabled={!interactive}
        >
          Pick element
        </Button>
        <Button variant="ghost" size="sm" onClick={bringToFront} disabled={!interactive} className="ml-auto">
          Bring window to front
        </Button>
      </div>

      {mode === 'pick' && (
        <div className={`${cardClasses({ variant: 'callout', accent: 'blue' })} mb-4 space-y-2`}>
          {!pickResult && <p className="text-sm text-ink/70">Click an element in the browser window to pick it.</p>}
          {pickResult && (
            <>
              <p className="text-sm text-ink/80">
                Picked: <span className="font-mono">{pickResult.selectors[0]}</span>
                {pickResult.preview && <> — &ldquo;{pickResult.preview.slice(0, 60)}&rdquo;</>}
              </p>
              <p className="text-sm text-ink/60">{pickResult.count} similar element(s) found on the page.</p>
              <div className="flex gap-2">
                <Button variant="ink" size="sm" onClick={useAsListRoot}>
                  Use as list root
                </Button>
                <Button size="sm" onClick={addFieldFromPick}>
                  Add as field
                </Button>
              </div>
            </>
          )}
        </div>
      )}

      <section className="mb-8">
        <h2 className="text-h2 mb-2">Steps</h2>
        <div className="h-72 overflow-y-auto rounded-card border-2 border-ink bg-ink p-3 font-mono text-xs leading-relaxed text-cream/90">
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
                      onClick={() => undoStep(step.i)}
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
      </section>

      <section className="mb-8">
        <h2 className="text-h2 mb-2">Extraction</h2>
        <ExtractionEditor extraction={extraction} onChange={updateExtraction} />
        <div className="mt-3 flex items-center gap-3">
          <Button
            variant="ink"
            size="sm"
            onClick={testExtraction}
            disabled={!interactive || extraction.fields.length === 0}
          >
            Test extraction
          </Button>
        </div>
        {extractionResult && (
          <pre className="mt-3 max-h-48 overflow-auto rounded-card border border-sand bg-cream p-3 text-xs">
            {JSON.stringify(extractionResult.sample, null, 2)}
          </pre>
        )}
      </section>

      <div className="flex items-center gap-3">
        <Button variant="primary" onClick={save} disabled={!interactive || steps.length === 0}>
          Save
        </Button>
        <Button variant="danger-ghost" onClick={cancel} disabled={!interactive}>
          Cancel
        </Button>
      </div>
    </AppShell>
  )
}

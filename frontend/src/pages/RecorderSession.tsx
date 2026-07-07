import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useNavigate, useParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import ExtractionEditor from '../components/ExtractionEditor'
import RecorderPipCard from '../components/RecorderPipCard'
import RecorderStepList from '../components/RecorderStepList'
import { Badge, Button, buttonClasses, cardClasses, Spinner, StatChip } from '../components/ui'
import { usePipWindow } from '../hooks/usePipWindow'
import { api, ApiError } from '../lib/api'
import { STATUS_BADGE, STATUS_LABEL } from '../lib/recorderStatus'
import { useRecorder } from '../hooks/useRecorder'
import type { ExtractionConfig, ParameterSuggestion } from '../lib/types'

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
    authoringPending,
    parameterSuggestions,
    extractionFieldSuggestions,
    undoStep,
    bringToFront,
    setMode,
    markParam,
    setExtraction,
    testExtraction,
    suggestAuthoring,
    dismissParameterSuggestion,
    dismissExtractionFieldSuggestion,
    save,
    cancel,
  } = useRecorder(workflowId!)
  const interactive = status === 'ready'

  const { supported: pipSupported, pipWindow, open: openPip, close: closePip } = usePipWindow()

  const [extraction, setExtractionState] = useState<ExtractionConfig>(EMPTY_EXTRACTION)
  const [deleting, setDeleting] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  // Once the session ends (saved, cancelled, or crashed) the card has nothing
  // to control — close it so the user lands back on this tab.
  useEffect(() => {
    if (saved || status === 'closed' || status === 'died') closePip()
  }, [saved, status, closePip])

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

  function acceptParameterSuggestion(suggestion: ParameterSuggestion) {
    markParam(suggestion.step_i, suggestion.name, suggestion.type, suggestion.description)
  }

  function acceptExtractionFieldSuggestion(selector: string, name: string, take: string, transform: string) {
    updateExtraction({
      ...extraction,
      fields: extraction.fields.map((f) => (f.selector === selector ? { ...f, name, take, transform } : f)),
    })
    dismissExtractionFieldSuggestion(selector)
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
        {pipSupported && ' Pop out the controls to keep them floating above that window.'}
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
        <div className="ml-auto flex items-center gap-2">
          {pipSupported && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => (pipWindow ? closePip() : openPip(380, 560))}
              disabled={!interactive}
            >
              {pipWindow ? 'Close floating controls' : 'Pop out controls'}
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={bringToFront} disabled={!interactive}>
            Bring window to front
          </Button>
        </div>
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
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-h2">Steps</h2>
          <Button
            variant="ghost"
            size="sm"
            onClick={suggestAuthoring}
            disabled={!interactive || steps.length === 0 || authoringPending}
          >
            {authoringPending ? (
              <>
                <Spinner className="size-4" /> Thinking…
              </>
            ) : (
              '✨ Suggest parameters'
            )}
          </Button>
        </div>
        <RecorderStepList
          steps={steps}
          interactive={interactive}
          onUndo={undoStep}
          onMarkParam={markParam}
          suggestions={parameterSuggestions}
          onAcceptSuggestion={acceptParameterSuggestion}
          onDismissSuggestion={dismissParameterSuggestion}
        />
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
        {extractionFieldSuggestions.length > 0 && (
          <div className={`${cardClasses({ variant: 'callout', accent: 'gold' })} mt-3 space-y-2`}>
            <p className="text-[11px] font-bold uppercase tracking-wide text-ink/60">✨ Suggested field names</p>
            {extractionFieldSuggestions.map((suggestion) => (
              <div key={suggestion.selector} className="flex flex-wrap items-center gap-2 text-sm">
                <span className="font-mono text-xs text-ink/60">{suggestion.selector}</span>
                <span>
                  &rarr; <span className="font-bold">{suggestion.name}</span>{' '}
                  <span className="text-ink/60">
                    ({suggestion.take}
                    {suggestion.transform !== 'none' && `, ${suggestion.transform}`})
                  </span>
                </span>
                <div className="ml-auto flex items-center gap-2">
                  <Button
                    variant="ink"
                    size="sm"
                    onClick={() =>
                      acceptExtractionFieldSuggestion(
                        suggestion.selector,
                        suggestion.name,
                        suggestion.take,
                        suggestion.transform,
                      )
                    }
                  >
                    Accept
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => dismissExtractionFieldSuggestion(suggestion.selector)}>
                    Dismiss
                  </Button>
                </div>
              </div>
            ))}
          </div>
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

      {pipWindow &&
        createPortal(
          <RecorderPipCard
            status={status}
            steps={steps}
            mode={mode}
            interactive={interactive}
            pickResult={pickResult}
            onSetMode={setMode}
            onUndo={undoStep}
            onMarkParam={markParam}
            onUseAsListRoot={useAsListRoot}
            onAddField={addFieldFromPick}
            onSave={save}
            onCancel={cancel}
          />,
          pipWindow.document.body,
        )}
    </AppShell>
  )
}

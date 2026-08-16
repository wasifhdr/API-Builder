import { type FormEvent, useCallback, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import {
  Badge,
  type BadgeVariant,
  Button,
  buttonClasses,
  CapsLabel,
  cardClasses,
  FieldError,
  FieldHelp,
  FieldLabel,
  Input,
  Spinner,
  Textarea,
} from '../components/ui'
import { useAgentRun } from '../hooks/useAgentRun'
import { useDictation } from '../hooks/useDictation'
import { api, ApiError } from '../lib/api'
import { TERMINAL_RUN_STATUSES } from '../lib/agentTypes'
import type { AgentRunStatus } from '../lib/agentTypes'

const STATUS_LABEL: Record<AgentRunStatus, string> = {
  planning: 'Planning',
  awaiting_confirm: 'Waiting for confirmation',
  driving: 'Driving the browser',
  distilling: 'Processing the recording',
  verifying: 'Verifying',
  repairing: 'Retrying with a new strategy',
  succeeded: 'Succeeded',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

// Not an AgentRunStatus: the row keeps whatever phase it was in, and the run
// resumes there once the quota window reopens. It overrides the label only so
// a minute of silence does not read as a hung run.
const RATE_LIMIT_LABEL = 'Waiting for limit reset'

const STATUS_BADGE: Record<AgentRunStatus, BadgeVariant> = {
  planning: 'pending',
  awaiting_confirm: 'pending',
  driving: 'info',
  distilling: 'info',
  verifying: 'info',
  repairing: 'pending',
  succeeded: 'success',
  failed: 'failed',
  cancelled: 'pending',
}

function MicIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className="size-4"
      aria-hidden="true"
    >
      <rect x="9" y="2" width="6" height="11" rx="3" />
      <path d="M5 10a7 7 0 0 0 14 0" />
      <path d="M12 17v5" />
    </svg>
  )
}

/** Entry screen: describe the API in a sentence. Separate from the recorder
 * route, which stays untouched and is offered as the fallback below. */
function PromptForm() {
  const navigate = useNavigate()
  const [prompt, setPrompt] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const appendSpoken = useCallback((text: string) => {
    setPrompt((current) => (current ? `${current.replace(/\s+$/, '')} ${text}` : text))
  }, [])
  const dictation = useDictation({ onFinal: appendSpoken })

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    dictation.stop()
    setError(null)
    setSubmitting(true)
    try {
      const run = await api.post<{ id: string }>('/agent/runs', { prompt })
      navigate(`/build/${run.id}`)
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setError('Not enough wallet balance for an agent run. Add funds and try again.')
      } else if (err instanceof ApiError && err.status === 403) {
        setError('Autonomous authoring requires a Pro or Max plan.')
      } else {
        setError(err instanceof Error ? err.message : 'Failed to start the agent')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="mx-auto max-w-lg">
      <div className={cardClasses({ variant: 'feature' })}>
        <CapsLabel className="mb-1">Build with AI</CapsLabel>
        <h1 className="text-h1 mb-4">Describe the API you want</h1>

        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <p className="text-sm font-medium text-red-deep">{error}</p>}
          <div>
            <FieldLabel htmlFor="agent-prompt">What should it do?</FieldLabel>
            <div className="relative">
              <Textarea
                id="agent-prompt"
                required
                rows={3}
                // Interim words are shown appended so dictation reads live, but
                // only the settled `prompt` is ever submitted.
                value={dictation.interim ? `${prompt} ${dictation.interim}`.trim() : prompt}
                onChange={(e) => setPrompt(e.target.value)}
                className={dictation.supported ? 'pr-12' : ''}
                placeholder="e.g. make me an API to search for products on the Walton website"
              />
              {dictation.supported && (
                <button
                  type="button"
                  onClick={dictation.toggle}
                  aria-pressed={dictation.listening}
                  aria-label={dictation.listening ? 'Stop dictation' : 'Dictate your prompt'}
                  title={dictation.listening ? 'Stop dictation' : 'Dictate your prompt'}
                  className={`absolute bottom-2 right-2 grid size-9 place-items-center rounded-control bg-transparent transition focus-visible:outline-[3px] focus-visible:outline-ink focus-visible:outline-offset-2 ${
                    dictation.listening
                      ? 'animate-pulse text-orange'
                      : 'text-ink/55 hover:text-ink'
                  }`}
                >
                  <MicIcon />
                </button>
              )}
            </div>
            {dictation.listening && (
              <FieldHelp>Listening… speak your request, then press the mic again.</FieldHelp>
            )}
            {dictation.error && <FieldError>{dictation.error}</FieldError>}
          </div>
          <Button type="submit" variant="primary" disabled={submitting} className="w-full justify-center">
            {submitting ? 'Starting…' : 'Build it'}
          </Button>
        </form>
      </div>

      <div className={`${cardClasses({ variant: 'callout', accent: 'gold' })} mt-6`}>
        <CapsLabel tone="gold" className="mb-1">
          Heads up
        </CapsLabel>
        <p className="text-sm text-ink/80">
          This costs one agent-run charge and works on public sites only — the agent stops and
          refunds you if the target needs a login. You&apos;ll confirm the resolved site before
          anything runs.
        </p>
      </div>
    </div>
  )
}

/** Progress screen: connects to an existing run and renders it through to a
 * terminal state. */
function RunProgress({ runId }: { runId: string }) {
  const { run, activity, checks, connectionError, rateLimited, confirmUrl, cancelRun } =
    useAgentRun(runId)
  const [confirming, setConfirming] = useState(false)
  const [urlDraft, setUrlDraft] = useState<string | null>(null)
  const [urlError, setUrlError] = useState<string | null>(null)
  const [stopping, setStopping] = useState(false)
  const [stopError, setStopError] = useState<string | null>(null)

  if (!run) {
    return (
      <div className="flex items-center gap-2 text-ink/60">
        <Spinner /> {connectionError ?? 'Loading…'}
      </div>
    )
  }

  async function handleConfirm(ok: boolean) {
    setConfirming(true)
    setUrlError(null)
    try {
      await confirmUrl(ok, ok ? (urlDraft ?? run?.resolved_url ?? undefined) : undefined)
    } catch (err) {
      setUrlError(err instanceof Error ? err.message : 'Could not confirm that address')
    } finally {
      setConfirming(false)
    }
  }

  async function handleStop() {
    setStopping(true)
    setStopError(null)
    try {
      await cancelRun()
    } catch (err) {
      setStopError(err instanceof Error ? err.message : 'Could not stop this run')
    } finally {
      setStopping(false)
    }
  }

  const running = !TERMINAL_RUN_STATUSES.has(run.status)
  // Gated on `running` so a "waiting" event that arrives just as the run ends
  // (or is left over after a reconnect) cannot label a finished run.
  const waitingForLimit = rateLimited && running
  // The confirmation card carries its own "Cancel run" button, so the header
  // does not offer a second, differently-worded way to do the same thing.
  const canStop = running && run.status !== 'awaiting_confirm'

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className={cardClasses({ variant: 'feature' })}>
        <div className="mb-3 flex items-center justify-between">
          <CapsLabel>Agent run</CapsLabel>
          <Badge
            variant={waitingForLimit ? 'pending' : STATUS_BADGE[run.status]}
            pulse={running}
          >
            {waitingForLimit ? RATE_LIMIT_LABEL : STATUS_LABEL[run.status]}
          </Badge>
        </div>
        <p className="text-ink/80">&ldquo;{run.prompt}&rdquo;</p>
        {run.attempt > 1 && (
          <p className="mt-1 text-sm text-ink/50">Attempt {run.attempt}</p>
        )}
        {canStop && (
          <div className="mt-4 border-t border-ink/10 pt-4">
            <Button
              variant="danger-ghost"
              size="sm"
              className="-ml-3"
              disabled={stopping}
              onClick={handleStop}
            >
              {stopping ? 'Stopping…' : 'Stop building'}
            </Button>
            <p className="mt-2 text-sm text-ink/50">
              Ends the run and refunds the charge. The browser closes at the agent&apos;s next
              step, so this can take a moment.
            </p>
            {stopError && <FieldError>{stopError}</FieldError>}
          </div>
        )}
      </div>

      {run.status === 'awaiting_confirm' && run.resolved_url && (
        <div className={cardClasses({ variant: 'callout', accent: 'blue' })}>
          <CapsLabel tone="blue" className="mb-2">
            Confirm the target site
          </CapsLabel>
          <p className="mb-3 text-sm text-ink/70">
            The agent picked this from the description. Edit it if it&apos;s wrong.
          </p>
          <Input
            aria-label="Target site URL"
            value={urlDraft ?? run.resolved_url}
            error={!!urlError}
            disabled={confirming}
            onChange={(e) => setUrlDraft(e.target.value)}
            className="mb-1"
          />
          {urlError && <FieldError>{urlError}</FieldError>}
          {run.plan.result_shape && (
            <p className="mb-4 mt-2 text-sm text-ink/60">
              Returns {run.plan.result_shape === 'list' ? 'a list of results' : 'one record'}.
            </p>
          )}
          <div className="mt-4 flex gap-3">
            <Button variant="primary" disabled={confirming} onClick={() => handleConfirm(true)}>
              Confirm
            </Button>
            <Button variant="ghost" disabled={confirming} onClick={() => handleConfirm(false)}>
              Cancel run
            </Button>
          </div>
        </div>
      )}

      {(run.plan.parameters?.length || run.plan.fields?.length) && (
        <div className={cardClasses({ variant: 'standard' })}>
          <CapsLabel className="mb-2">Plan</CapsLabel>
          {run.plan.parameters && run.plan.parameters.length > 0 && (
            <p className="text-sm text-ink/70">
              Parameters: {run.plan.parameters.map((p) => p.name).join(', ')}
            </p>
          )}
          {run.plan.fields && run.plan.fields.length > 0 && (
            <p className="text-sm text-ink/70">
              Fields: {run.plan.fields.map((f) => f.name).join(', ')}
            </p>
          )}
        </div>
      )}

      {activity.length > 0 && (
        <div className={cardClasses({ variant: 'standard' })}>
          <CapsLabel className="mb-2">Activity</CapsLabel>
          <div className="max-h-64 space-y-1 overflow-y-auto font-mono text-sm text-ink/70">
            {activity.map((entry, i) => (
              <div key={i}>
                <span className="text-gold-deep">{entry.tool}</span> — {entry.detail}
              </div>
            ))}
          </div>
        </div>
      )}

      {checks.length > 0 && (
        <div className={cardClasses({ variant: 'standard' })}>
          <CapsLabel className="mb-2">Verification</CapsLabel>
          <div className="space-y-1">
            {checks.map((check) => (
              <div key={check.name} className="flex items-start gap-2 text-sm">
                <Badge variant={check.passed ? 'success' : 'failed'}>{check.name}</Badge>
                <span className="text-ink/70">{check.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {run.status === 'succeeded' && run.workflow_id && (
        <div className={cardClasses({ variant: 'callout', accent: 'green' })}>
          <CapsLabel tone="green" className="mb-2">
            Done
          </CapsLabel>
          <p className="mb-4 text-ink/80">The API is ready to publish.</p>
          <Link to={`/workflows/${run.workflow_id}/edit`} className={buttonClasses('primary')}>
            Review and publish
          </Link>
        </div>
      )}

      {run.status === 'cancelled' && (
        <div className={cardClasses({ variant: 'callout', accent: 'gold' })}>
          <CapsLabel tone="gold" className="mb-2">
            Cancelled
          </CapsLabel>
          <p className="mb-4 text-ink/80">
            You stopped this run. You haven&apos;t been charged.
          </p>
          <div className="flex flex-wrap gap-3">
            <Link to="/build" className={buttonClasses('primary')}>
              Start over
            </Link>
            {/* Only for a run stopped mid-drive (workflow_id is assigned right
                after the URL is confirmed). A run stopped at the gate never
                attempted anything, so offering a recovery would frame a
                deliberate cancel as a failure. */}
            {run.workflow_id && run.resolved_url && (
              <Link
                to={`/recorder?start_url=${encodeURIComponent(run.resolved_url)}`}
                className={buttonClasses('default')}
              >
                Record it manually
              </Link>
            )}
          </div>
        </div>
      )}

      {run.status === 'failed' && (
        <div className={cardClasses({ variant: 'callout', accent: 'red' })}>
          <CapsLabel tone="red" className="mb-2">
            Couldn&apos;t finish this one
          </CapsLabel>
          <p className="mb-4 text-ink/80">{run.failure_reason ?? 'The agent could not complete this API.'}</p>
          <Link
            to={run.resolved_url ? `/recorder?start_url=${encodeURIComponent(run.resolved_url)}` : '/recorder'}
            className={buttonClasses('primary')}
          >
            Record it manually instead
          </Link>
        </div>
      )}
    </div>
  )
}

export default function AgentBuilder() {
  const { runId } = useParams<{ runId: string }>()

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      {runId ? <RunProgress runId={runId} /> : <PromptForm />}
    </AppShell>
  )
}

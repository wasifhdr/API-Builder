import { type FormEvent, useState } from 'react'
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
  FieldLabel,
  Input,
  Spinner,
  Textarea,
} from '../components/ui'
import { useAgentRun } from '../hooks/useAgentRun'
import { api, ApiError } from '../lib/api'
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

/** Entry screen: describe the API in a sentence. Separate from the recorder
 * route, which stays untouched and is offered as the fallback below. */
function PromptForm() {
  const navigate = useNavigate()
  const [prompt, setPrompt] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
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
            <Textarea
              id="agent-prompt"
              required
              rows={3}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="e.g. make me an API to search for products on the Walton website"
            />
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
  const { run, activity, checks, connectionError, confirmUrl } = useAgentRun(runId)
  const [confirming, setConfirming] = useState(false)
  const [urlDraft, setUrlDraft] = useState<string | null>(null)
  const [urlError, setUrlError] = useState<string | null>(null)

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

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <div className={cardClasses({ variant: 'feature' })}>
        <div className="mb-3 flex items-center justify-between">
          <CapsLabel>Agent run</CapsLabel>
          <Badge variant={STATUS_BADGE[run.status]} pulse={run.status !== 'succeeded' && run.status !== 'failed'}>
            {STATUS_LABEL[run.status]}
          </Badge>
        </div>
        <p className="text-ink/80">&ldquo;{run.prompt}&rdquo;</p>
        {run.attempt > 1 && (
          <p className="mt-1 text-sm text-ink/50">Attempt {run.attempt}</p>
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
            You stopped this run before it started. You haven&apos;t been charged.
          </p>
          <Link to="/build" className={buttonClasses('primary')}>
            Start over
          </Link>
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

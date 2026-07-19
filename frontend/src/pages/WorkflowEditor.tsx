import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import ExtractionEditor from '../components/ExtractionEditor'
import {
  Badge,
  Button,
  buttonClasses,
  cardClasses,
  CapsLabel,
  Checkbox,
  FieldLabel,
  InlineCode,
  Input,
  PageHeader,
  Select,
  Table,
  TableWrapper,
  Td,
  Th,
  Tr,
} from '../components/ui'
import { api, ApiError } from '../lib/api'
import { describeStep } from '../lib/steps'
import type { ExtractionConfig, Parameter, Step } from '../lib/types'

interface WorkflowDetail {
  id: string
  name: string
  start_url: string
  status: string
  steps: Step[]
  parameters: Parameter[]
  extraction: { main?: ExtractionConfig }
  published_api_id: string | null
  published_api_slug: string | null
}

const EMPTY_EXTRACTION: ExtractionConfig = { mode: 'list', root: '', fields: [] }

export default function WorkflowEditor() {
  const { workflowId } = useParams<{ workflowId: string }>()
  const navigate = useNavigate()
  const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null)
  const [name, setName] = useState('')
  const [parameters, setParameters] = useState<Parameter[]>([])
  const [extraction, setExtraction] = useState<ExtractionConfig>(EMPTY_EXTRACTION)
  const [saving, setSaving] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [rerecording, setRerecording] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)
  const isPublished = !!workflow?.published_api_id

  useEffect(() => {
    api
      .get<WorkflowDetail>(`/workflows/${workflowId}`)
      .then((wf) => {
        setWorkflow(wf)
        setName(wf.name)
        setParameters(wf.parameters)
        setExtraction(wf.extraction.main ?? EMPTY_EXTRACTION)
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load workflow'))
  }, [workflowId])

  function updateParam(index: number, patch: Partial<Parameter>) {
    setParameters((prev) => prev.map((p, i) => (i === index ? { ...p, ...patch } : p)))
  }

  async function handleSave() {
    setSaving(true)
    setError(null)
    setSaveMessage(null)
    try {
      await api.patch(`/workflows/${workflowId}`, {
        name,
        parameters,
        extraction: { main: extraction },
      })
      setSaveMessage('Saved.')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  async function handleDelete() {
    setDeleting(true)
    setError(null)
    try {
      await api.delete(`/workflows/${workflowId}`)
      navigate('/dashboard')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to delete')
      setConfirmingDelete(false)
    } finally {
      setDeleting(false)
    }
  }

  async function handlePublish() {
    setPublishing(true)
    setError(null)
    try {
      const published = await api.post<{ id: string }>(`/workflows/${workflowId}/publish`)
      navigate(`/apis/${published.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to publish')
    } finally {
      setPublishing(false)
    }
  }

  async function handleSync() {
    if (!workflow?.published_api_id) return
    setSyncing(true)
    setError(null)
    setSaveMessage(null)
    try {
      await api.post(`/apis/${workflow.published_api_id}/sync`)
      setSaveMessage('Live API updated.')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to sync')
    } finally {
      setSyncing(false)
    }
  }

  async function handleRerecord() {
    setRerecording(true)
    setError(null)
    try {
      await api.post(`/workflows/${workflowId}/rerecord`)
      navigate(`/recorder/${workflowId}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to start re-record')
      setRerecording(false)
    }
  }

  if (!workflow) {
    return (
      <AppShell>
        <p className="text-sm text-ink/60">{error ?? 'Loading…'}</p>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <Link to="/dashboard" className={buttonClasses('ghost', 'sm', 'mb-4')}>
        &larr; Dashboard
      </Link>
      <PageHeader
        eyebrow={<CapsLabel>Workflow</CapsLabel>}
        title={workflow.name}
        actions={<Badge variant="neutral">{workflow.status}</Badge>}
      />

      {isPublished && (
        <div className={`${cardClasses({ variant: 'callout', accent: 'blue' })} mb-6 flex items-center justify-between gap-3`}>
          <span className="text-sm text-ink/80">
            Live as <InlineCode>/v1/run/{workflow.published_api_slug}</InlineCode>. Edits here don&apos;t affect the
            live API until you sync.
          </span>
          <Link to={`/apis/${workflow.published_api_id}`} className={buttonClasses('ghost', 'sm')}>
            Open API &rarr;
          </Link>
        </div>
      )}

      {error && <p className="mb-4 text-sm font-medium text-red-deep">{error}</p>}
      {saveMessage && <p className="mb-4 text-sm font-medium text-green-deep">{saveMessage}</p>}

      <div className="mb-8">
        <FieldLabel htmlFor="wf-name">Name</FieldLabel>
        <Input id="wf-name" type="text" value={name} onChange={(e) => setName(e.target.value)} className="max-w-md" />
      </div>

      <section className="mb-8">
        <h2 className="text-h2 mb-2">Steps</h2>
        <TableWrapper>
          <Table>
            <thead>
              <tr>
                <Th className="w-14">#</Th>
                <Th>Step</Th>
              </tr>
            </thead>
            <tbody>
              {workflow.steps.map((step) => (
                <Tr key={step.i}>
                  <Td mono>{step.i}</Td>
                  <Td>{describeStep(step)}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </TableWrapper>
      </section>

      <section className="mb-8">
        <h2 className="text-h2 mb-2">Parameters</h2>
        {parameters.length === 0 ? (
          <p className="text-sm text-ink/60">No parameters marked yet.</p>
        ) : (
          <TableWrapper>
            <Table>
              <thead>
                <tr>
                  <Th>Name</Th>
                  <Th>Type</Th>
                  <Th>Required</Th>
                  <Th>Example</Th>
                  <Th>Description</Th>
                </tr>
              </thead>
              <tbody>
                {parameters.map((p, i) => (
                  <Tr key={p.name}>
                    <Td>
                      <Badge variant="neutral">{p.name}</Badge>
                    </Td>
                    <Td>
                      <Select
                        value={p.type}
                        onChange={(e) => updateParam(i, { type: e.target.value })}
                        className="w-auto py-1"
                      >
                        <option value="string">string</option>
                        <option value="integer">integer</option>
                        <option value="number">number</option>
                        <option value="boolean">boolean</option>
                      </Select>
                    </Td>
                    <Td>
                      <Checkbox checked={p.required} onChange={(e) => updateParam(i, { required: e.target.checked })} />
                    </Td>
                    <Td mono>{p.example}</Td>
                    <Td>
                      <Input
                        type="text"
                        value={p.description ?? ''}
                        onChange={(e) => updateParam(i, { description: e.target.value })}
                        className="py-1"
                      />
                    </Td>
                  </Tr>
                ))}
              </tbody>
            </Table>
          </TableWrapper>
        )}
      </section>

      <section className="mb-8">
        <h2 className="text-h2 mb-2">Extraction</h2>
        <ExtractionEditor extraction={extraction} onChange={setExtraction} />
      </section>

      <div className="flex items-center gap-3">
        <Button variant="primary" onClick={handleSave} disabled={saving}>
          {saving ? 'Saving…' : 'Save changes'}
        </Button>

        {isPublished ? (
          <>
            <Button variant="default" onClick={handleRerecord} disabled={rerecording}>
              {rerecording ? 'Starting…' : 'Re-record'}
            </Button>
            <Button variant="ink" onClick={handleSync} disabled={syncing}>
              {syncing ? 'Syncing…' : 'Sync changes to live API'}
            </Button>
          </>
        ) : (
          <>
            <Button variant="default" onClick={handleRerecord} disabled={rerecording}>
              {rerecording ? 'Starting…' : 'Re-record'}
            </Button>
            {workflow.status === 'ready' && (
              <Button variant="ink" onClick={handlePublish} disabled={publishing}>
                {publishing ? 'Publishing…' : 'Publish as API'}
              </Button>
            )}
            {confirmingDelete ? (
              <div className="ml-auto flex items-center gap-2">
                <span className="text-sm text-ink/70">Delete this workflow? This can&apos;t be undone.</span>
                <Button variant="danger-ghost" onClick={handleDelete} disabled={deleting}>
                  {deleting ? 'Deleting…' : 'Confirm delete'}
                </Button>
                <Button variant="ghost" onClick={() => setConfirmingDelete(false)} disabled={deleting}>
                  Cancel
                </Button>
              </div>
            ) : (
              <Button variant="danger-ghost" className="ml-auto" onClick={() => setConfirmingDelete(true)}>
                Delete workflow
              </Button>
            )}
          </>
        )}
      </div>
    </AppShell>
  )
}

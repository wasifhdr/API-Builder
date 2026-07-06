import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import AppShell from '../components/AppShell'
import ExtractionEditor from '../components/ExtractionEditor'
import {
  Badge,
  Button,
  buttonClasses,
  CapsLabel,
  Checkbox,
  FieldLabel,
  Input,
  PageHeader,
  Select,
  Table,
  TableWrapper,
  Td,
  Th,
  Tr,
} from '../components/ui'
import { api } from '../lib/api'
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
  const [error, setError] = useState<string | null>(null)
  const [saveMessage, setSaveMessage] = useState<string | null>(null)

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
        {workflow.status === 'ready' && (
          <Button variant="ink" onClick={handlePublish} disabled={publishing}>
            {publishing ? 'Publishing…' : 'Publish as API'}
          </Button>
        )}
      </div>
    </AppShell>
  )
}

import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import ExtractionEditor from '../components/ExtractionEditor'
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
  const [workflow, setWorkflow] = useState<WorkflowDetail | null>(null)
  const [name, setName] = useState('')
  const [parameters, setParameters] = useState<Parameter[]>([])
  const [extraction, setExtraction] = useState<ExtractionConfig>(EMPTY_EXTRACTION)
  const [saving, setSaving] = useState(false)
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

  if (!workflow) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white text-gray-500">
        {error ?? 'Loading…'}
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <Link to="/dashboard" className="text-sm text-gray-600 hover:text-gray-900">
          &larr; Dashboard
        </Link>
        <span className="text-xs uppercase font-semibold text-gray-500">{workflow.status}</span>
      </header>
      <main className="p-6 max-w-2xl space-y-6">
        {error && <p className="text-red-600 text-sm">{error}</p>}
        {saveMessage && <p className="text-green-600 text-sm">{saveMessage}</p>}

        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full max-w-md rounded-md border border-gray-300 px-3 py-2 text-sm"
          />
        </div>

        <section>
          <h2 className="text-sm font-semibold text-gray-900 mb-2">Steps</h2>
          <ul className="divide-y divide-gray-100 border border-gray-200 rounded-md">
            {workflow.steps.map((step) => (
              <li key={step.i} className="px-3 py-2 text-sm text-gray-700">
                {describeStep(step)}
              </li>
            ))}
          </ul>
        </section>

        <section>
          <h2 className="text-sm font-semibold text-gray-900 mb-2">Parameters</h2>
          {parameters.length === 0 && <p className="text-sm text-gray-400">No parameters marked yet.</p>}
          {parameters.length > 0 && (
            <table className="w-full text-xs border border-gray-200 rounded-md">
              <thead>
                <tr className="text-left text-gray-500">
                  <th className="p-2">Name</th>
                  <th className="p-2">Type</th>
                  <th className="p-2">Required</th>
                  <th className="p-2">Example</th>
                  <th className="p-2">Description</th>
                </tr>
              </thead>
              <tbody>
                {parameters.map((p, i) => (
                  <tr key={p.name} className="border-t border-gray-100">
                    <td className="p-2 font-mono">{p.name}</td>
                    <td className="p-2">
                      <select
                        value={p.type}
                        onChange={(e) => updateParam(i, { type: e.target.value })}
                        className="rounded border border-gray-300 px-1 py-0.5"
                      >
                        <option value="string">string</option>
                        <option value="integer">integer</option>
                        <option value="number">number</option>
                        <option value="boolean">boolean</option>
                      </select>
                    </td>
                    <td className="p-2">
                      <input
                        type="checkbox"
                        checked={p.required}
                        onChange={(e) => updateParam(i, { required: e.target.checked })}
                      />
                    </td>
                    <td className="p-2">{p.example}</td>
                    <td className="p-2">
                      <input
                        type="text"
                        value={p.description ?? ''}
                        onChange={(e) => updateParam(i, { description: e.target.value })}
                        className="w-full rounded border border-gray-300 px-1 py-0.5"
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section>
          <h2 className="text-sm font-semibold text-gray-900 mb-2">Extraction</h2>
          <ExtractionEditor extraction={extraction} onChange={setExtraction} />
        </section>

        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      </main>
    </div>
  )
}

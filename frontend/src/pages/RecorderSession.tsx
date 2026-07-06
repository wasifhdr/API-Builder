import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import ExtractionEditor from '../components/ExtractionEditor'
import { useRecorder } from '../hooks/useRecorder'
import { describeStep } from '../lib/steps'
import type { ExtractionConfig, RecorderStatus } from '../lib/types'

const STATUS_LABEL: Record<RecorderStatus, string> = {
  connecting: 'Connecting…',
  launching: 'Launching browser…',
  ready: 'Recording — interact with the browser window',
  closed: 'Closed',
  died: 'Recorder crashed',
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

  function updateExtraction(next: ExtractionConfig) {
    setExtractionState(next)
    setExtraction(next)
  }

  function addFieldFromPick() {
    if (!pickResult) return
    const next: ExtractionConfig = {
      ...extraction,
      fields: [
        ...extraction.fields,
        { name: `field${extraction.fields.length + 1}`, selector: pickResult.selectors[0], take: 'text', transform: 'none' },
      ],
    }
    updateExtraction(next)
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
      <div className="min-h-screen flex items-center justify-center bg-white">
        <div className="text-center space-y-4">
          <p className="text-gray-900 font-medium">Workflow saved.</p>
          <div className="flex items-center gap-3 justify-center">
            <button
              type="button"
              onClick={() => navigate(`/workflows/${workflowId}/edit`)}
              className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800"
            >
              Edit workflow
            </button>
            <button
              type="button"
              onClick={() => navigate('/dashboard')}
              className="text-sm text-gray-600 hover:text-gray-900"
            >
              Back to dashboard
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-white">
      <header className="border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <Link to="/dashboard" className="text-sm text-gray-600 hover:text-gray-900">
          &larr; Dashboard
        </Link>
        <span className="text-sm font-medium text-gray-700">{STATUS_LABEL[status]}</span>
      </header>
      <main className="p-6 max-w-2xl space-y-6">
        <p className="text-sm text-gray-500">
          A Chromium window opened on your desktop — browse there and steps will appear here live.
        </p>
        {error && <p className="text-red-600 text-sm">{error}</p>}

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setMode('record')}
            disabled={!interactive}
            className={`text-sm px-3 py-1.5 rounded-md ${mode === 'record' ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-700'} disabled:opacity-40`}
          >
            Record
          </button>
          <button
            type="button"
            onClick={() => setMode('pick')}
            disabled={!interactive}
            className={`text-sm px-3 py-1.5 rounded-md ${mode === 'pick' ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-700'} disabled:opacity-40`}
          >
            Pick element
          </button>
          <button
            type="button"
            onClick={bringToFront}
            disabled={!interactive}
            className="text-sm text-gray-600 hover:text-gray-900 underline disabled:opacity-40 disabled:no-underline ml-auto"
          >
            Bring window to front
          </button>
        </div>

        {mode === 'pick' && (
          <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-sm space-y-2">
            {!pickResult && <p className="text-gray-500">Click an element in the browser window to pick it.</p>}
            {pickResult && (
              <>
                <p className="text-gray-700">
                  Picked: <span className="font-mono">{pickResult.selectors[0]}</span>
                  {pickResult.preview && <> — “{pickResult.preview.slice(0, 60)}”</>}
                </p>
                <p className="text-gray-500">{pickResult.count} similar element(s) found on the page.</p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={useAsListRoot}
                    className="text-xs rounded bg-blue-600 text-white px-2 py-1"
                  >
                    Use as list root
                  </button>
                  <button
                    type="button"
                    onClick={addFieldFromPick}
                    className="text-xs rounded bg-gray-700 text-white px-2 py-1"
                  >
                    Add as field
                  </button>
                </div>
              </>
            )}
          </div>
        )}

        <section>
          <h2 className="text-sm font-semibold text-gray-900 mb-2">Steps</h2>
          <ul className="divide-y divide-gray-100 border border-gray-200 rounded-md">
            {steps.length === 0 && (
              <li className="px-3 py-2 text-sm text-gray-400">No steps recorded yet.</li>
            )}
            {steps.map((step) => {
              const isValueStep = step.type === 'fill' || step.type === 'select_option'
              const isParam = step.value && 'param' in step.value
              return (
                <li key={step.i} className="px-3 py-2 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="text-gray-800">{describeStep(step)}</span>
                    <div className="flex items-center gap-2">
                      {isValueStep && !isParam && paramFormFor !== step.i && (
                        <button
                          type="button"
                          onClick={() => setParamFormFor(step.i)}
                          disabled={!interactive}
                          className="text-xs text-blue-600 hover:text-blue-800 disabled:opacity-40"
                        >
                          Make parameter
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => undoStep(step.i)}
                        disabled={!interactive}
                        className="text-xs text-gray-400 hover:text-red-600 disabled:opacity-40"
                      >
                        Undo
                      </button>
                    </div>
                  </div>
                  {paramFormFor === step.i && (
                    <div className="flex items-center gap-2 mt-2">
                      <input
                        type="text"
                        autoFocus
                        value={paramName}
                        onChange={(e) => setParamName(e.target.value)}
                        placeholder="parameter name, e.g. query"
                        className="flex-1 rounded border border-gray-300 px-2 py-1 text-xs"
                      />
                      <button
                        type="button"
                        onClick={() => submitParamForm(step.i)}
                        className="text-xs rounded bg-gray-900 text-white px-2 py-1"
                      >
                        Confirm
                      </button>
                      <button
                        type="button"
                        onClick={() => setParamFormFor(null)}
                        className="text-xs text-gray-500"
                      >
                        Cancel
                      </button>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </section>

        <section>
          <h2 className="text-sm font-semibold text-gray-900 mb-2">Extraction</h2>
          <ExtractionEditor extraction={extraction} onChange={updateExtraction} />

          <div className="flex items-center gap-3 pt-2">
            <button
              type="button"
              onClick={testExtraction}
              disabled={!interactive || extraction.fields.length === 0}
              className="rounded-md bg-gray-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-gray-600 disabled:opacity-50"
            >
              Test extraction
            </button>
          </div>

          {extractionResult && (
            <pre className="max-h-48 overflow-auto rounded bg-gray-50 p-2 text-xs mt-2">
              {JSON.stringify(extractionResult.sample, null, 2)}
            </pre>
          )}
        </section>

        <div className="flex items-center gap-3 pt-2">
          <button
            type="button"
            onClick={save}
            disabled={!interactive || steps.length === 0}
            className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800 disabled:opacity-50"
          >
            Save
          </button>
          <button
            type="button"
            onClick={cancel}
            disabled={!interactive}
            className="text-sm text-gray-500 hover:text-red-600 disabled:opacity-40"
          >
            Cancel
          </button>
        </div>
      </main>
    </div>
  )
}

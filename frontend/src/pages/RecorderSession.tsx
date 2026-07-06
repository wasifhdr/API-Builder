import { Link, useNavigate, useParams } from 'react-router-dom'
import { useRecorder } from '../hooks/useRecorder'
import type { RecorderStatus, Step } from '../lib/types'

const STATUS_LABEL: Record<RecorderStatus, string> = {
  connecting: 'Connecting…',
  launching: 'Launching browser…',
  ready: 'Recording — interact with the browser window',
  closed: 'Closed',
  died: 'Recorder crashed',
}

function describeStep(step: Step): string {
  const selector = step.selectors?.[0] ?? ''
  switch (step.type) {
    case 'goto':
      return `Go to ${step.url}`
    case 'click':
      return `Click ${selector}`
    case 'fill':
      return `Fill ${selector} = "${step.value}"`
    case 'press':
      return `Press ${step.key} on ${selector}`
    case 'select_option':
      return `Select "${step.value}" in ${selector}`
    default:
      return step.type
  }
}

export default function RecorderSession() {
  const { workflowId } = useParams<{ workflowId: string }>()
  const navigate = useNavigate()
  const { status, steps, error, saved, undoStep, bringToFront, save, cancel } = useRecorder(workflowId!)
  const interactive = status === 'ready'

  if (saved) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white">
        <div className="text-center space-y-4">
          <p className="text-gray-900 font-medium">Workflow saved as a draft.</p>
          <button
            type="button"
            onClick={() => navigate('/dashboard')}
            className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800"
          >
            Back to dashboard
          </button>
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
      <main className="p-6 max-w-lg space-y-4">
        <p className="text-sm text-gray-500">
          A Chromium window opened on your desktop — browse there and steps will appear here live.
        </p>
        {error && <p className="text-red-600 text-sm">{error}</p>}

        <button
          type="button"
          onClick={bringToFront}
          disabled={!interactive}
          className="text-sm text-gray-600 hover:text-gray-900 underline disabled:opacity-40 disabled:no-underline"
        >
          Bring window to front
        </button>

        <ul className="divide-y divide-gray-100 border border-gray-200 rounded-md">
          {steps.length === 0 && (
            <li className="px-3 py-2 text-sm text-gray-400">No steps recorded yet.</li>
          )}
          {steps.map((step) => (
            <li key={step.i} className="px-3 py-2 text-sm flex items-center justify-between">
              <span className="text-gray-800">{describeStep(step)}</span>
              <button
                type="button"
                onClick={() => undoStep(step.i)}
                disabled={!interactive}
                className="text-xs text-gray-400 hover:text-red-600 disabled:opacity-40"
              >
                Undo
              </button>
            </li>
          ))}
        </ul>

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

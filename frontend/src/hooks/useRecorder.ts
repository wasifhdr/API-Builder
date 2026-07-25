import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  CompiledField,
  ExtractionConfig,
  ExtractionField,
  ExtractionFieldSuggestion,
  Parameter,
  ParameterSuggestion,
  PickCandidate,
  RecorderStatus,
  Step,
  WizardStep,
} from '../lib/types'

interface RecorderState {
  status: RecorderStatus
  steps: Step[]
  error: string | null
  saved: boolean
  mode: 'record' | 'pick'
  pickResult: PickCandidate | null
  extractionResult: { sample: unknown; schema: unknown } | null
  parameters: Parameter[]
  warnings: string[]
  authoringPending: boolean
  parameterSuggestions: ParameterSuggestion[]
  extractionFieldSuggestions: ExtractionFieldSuggestion[]
  wizardStep: WizardStep
  wizardMode: 'single' | 'list'
  wizardRoots: string[]
  wizardFields: ExtractionField[]
  lastCompiled: CompiledField | null
  /** A compile_root/compile_field round-trip is in flight. The worker locks the
   * recorder window for its duration, so the wizard must say so and refuse a
   * second request until the reply lands. */
  compiling: boolean
}

const RECONNECT_DELAY_MS = 2000

/** Mirror of the worker's undo bookkeeping: `i` is a position, so removing a
 * step renumbers everything after it and shifts every index that referenced it.
 * Applied optimistically on click so a second undo sent before the round-trip
 * still targets the step the user sees; the worker's `step_removed` snapshot
 * then overwrites this with the authoritative state. */
function applyStepRemoval(s: RecorderState, i: number): RecorderState {
  return {
    ...s,
    steps: s.steps.filter((step) => step.i !== i).map((step, j) => ({ ...step, i: j })),
    parameters: s.parameters
      .filter((p) => p.source_step !== i)
      .map((p) => (p.source_step !== null && p.source_step > i ? { ...p, source_step: p.source_step - 1 } : p)),
    parameterSuggestions: s.parameterSuggestions
      .filter((sg) => sg.step_i !== i)
      .map((sg) => (sg.step_i > i ? { ...sg, step_i: sg.step_i - 1 } : sg)),
  }
}

export function useRecorder(workflowId: string) {
  const [state, setState] = useState<RecorderState>({
    status: 'connecting',
    steps: [],
    error: null,
    saved: false,
    mode: 'record',
    pickResult: null,
    extractionResult: null,
    parameters: [],
    warnings: [],
    authoringPending: false,
    parameterSuggestions: [],
    extractionFieldSuggestions: [],
    wizardStep: 'idle',
    wizardMode: 'single',
    wizardRoots: [],
    wizardFields: [],
    lastCompiled: null,
    compiling: false,
  })
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<number | null>(null)
  const terminal = useRef(false)
  /** Undos applied locally that the worker hasn't echoed back yet. */
  const pendingUndos = useRef(0)

  const connect = useCallback(() => {
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${protocol}://${location.host}/api/ws/recordings/${workflowId}`)
    wsRef.current = ws

    ws.onopen = () => {
      setState((s) => ({ ...s, status: 'launching' }))
    }

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      switch (msg.t) {
        case 'status':
          setState((s) => ({ ...s, status: msg.state }))
          if (msg.state === 'closed') terminal.current = true
          break
        case 'step_recorded':
          setState((s) => ({ ...s, steps: [...s.steps, msg.step] }))
          break
        case 'step_removed':
          setState((s) => {
            // Our own undo already applied optimistically; only remap for a
            // removal we didn't make (another tab, or a pre-snapshot worker).
            const alreadyApplied = pendingUndos.current > 0
            if (alreadyApplied) pendingUndos.current -= 1
            const next = alreadyApplied ? s : applyStepRemoval(s, msg.i)
            // The worker is authoritative about indices — prefer its snapshot.
            return {
              ...next,
              steps: msg.steps ?? next.steps,
              parameters: msg.parameters ?? next.parameters,
            }
          })
          break
        case 'pick_result':
          setState((s) => ({ ...s, pickResult: msg.candidate }))
          break
        case 'root_compiled':
          setState((s) => ({
            ...s,
            wizardRoots: msg.roots ?? [],
            wizardStep: 'choose-values',
            pickResult: null,
            lastCompiled: null,
            compiling: false,
          }))
          break
        case 'field_compiled':
          setState((s) => ({ ...s, lastCompiled: msg.field, compiling: false }))
          break
        case 'extraction_result':
          setState((s) => ({ ...s, extractionResult: { sample: msg.sample, schema: msg.schema } }))
          break
        case 'param_marked':
          setState((s) => ({
            ...s,
            parameters: [...s.parameters.filter((p) => p.name !== msg.parameter.name), msg.parameter],
            steps: s.steps.map((step) => (step.i === msg.step.i ? msg.step : step)),
            parameterSuggestions: s.parameterSuggestions.filter((sg) => sg.step_i !== msg.step.i),
          }))
          break
        case 'authoring_suggestions':
          setState((s) => ({
            ...s,
            authoringPending: false,
            parameterSuggestions: msg.parameters ?? [],
            extractionFieldSuggestions: msg.extraction_fields ?? [],
          }))
          break
        case 'error':
          setState((s) => ({ ...s, error: msg.message, authoringPending: false, compiling: false }))
          break
        case 'warning':
          setState((s) => ({ ...s, warnings: [...s.warnings, msg.message] }))
          break
        case 'saved':
          setState((s) => ({ ...s, saved: true }))
          break
        case 'died':
          terminal.current = true
          setState((s) => ({
            ...s,
            status: 'died',
            error: 'The recorder crashed unexpectedly.',
            compiling: false,
          }))
          break
      }
    }

    ws.onclose = () => {
      if (terminal.current) return
      reconnectTimer.current = window.setTimeout(connect, RECONNECT_DELAY_MS)
    }

    ws.onerror = () => {
      ws.close()
    }
  }, [workflowId])

  useEffect(() => {
    connect()
    return () => {
      terminal.current = true
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  const send = useCallback((cmd: Record<string, unknown>) => {
    wsRef.current?.send(JSON.stringify(cmd))
  }, [])

  const setMode = useCallback(
    (mode: 'record' | 'pick') => {
      setState((s) => ({
        ...s,
        mode,
        pickResult: null,
        // The wizard only runs in pick mode, so leaving pick mode closes it.
        // Without this, hitting Record mid-wizard leaves the panel asking for a
        // pick that can no longer happen — clicks in the browser are being
        // recorded as steps again, and the wizard sits there waiting forever.
        ...(mode === 'record'
          ? { wizardStep: 'idle' as WizardStep, lastCompiled: null, compiling: false }
          : {}),
      }))
      send({ t: 'set_mode', mode })
    },
    [send],
  )

  const startWizard = useCallback(() => {
    setState((s) => ({
      ...s,
      wizardStep: 'choose-mode',
      wizardMode: 'single',
      wizardRoots: [],
      wizardFields: [],
      lastCompiled: null,
      mode: 'pick',
      pickResult: null,
    }))
    send({ t: 'set_mode', mode: 'pick' })
  }, [send])

  const chooseWizardMode = useCallback((mode: 'single' | 'list') => {
    setState((s) => ({
      ...s,
      wizardMode: mode,
      wizardStep: mode === 'list' ? 'pick-root' : 'choose-values',
    }))
  }, [])

  const confirmRoot = useCallback(() => {
    // Ask the worker to compile the picked element into ranked root selectors;
    // the 'root_compiled' event advances the wizard to 'choose-values'.
    setState((s) => ({ ...s, compiling: true }))
    send({ t: 'compile_root' })
  }, [send])

  const compileValue = useCallback(
    (name: string, description: string, take: string) => {
      setState((s) => ({ ...s, lastCompiled: null, compiling: true }))
      send({
        t: 'compile_field',
        mode: state.wizardMode,
        root: state.wizardRoots[0] ?? null,
        name,
        description,
        take,
      })
    },
    [send, state.wizardMode, state.wizardRoots],
  )

  const addCompiledField = useCallback(() => {
    setState((s) => {
      if (!s.lastCompiled) return s
      const field: ExtractionField = {
        name: s.lastCompiled.name,
        description: s.lastCompiled.description,
        take: s.lastCompiled.take,
        example: s.lastCompiled.example ?? undefined,
        selectors: s.lastCompiled.selectors,
        transform: 'none',
      }
      return { ...s, wizardFields: [...s.wizardFields, field], lastCompiled: null, pickResult: null }
    })
  }, [])

  const undoStep = useCallback(
    (i: number) => {
      send({ t: 'undo_step', i })
      pendingUndos.current += 1
      setState((s) => applyStepRemoval(s, i))
    },
    [send],
  )

  const undoPick = useCallback(() => {
    setState((s) => ({ ...s, pickResult: null, lastCompiled: null }))
  }, [])

  const finishWizard = useCallback(() => {
    setState((s) => {
      const config: ExtractionConfig = {
        mode: s.wizardMode,
        engine: 'compiled',
        roots: s.wizardMode === 'list' ? s.wizardRoots : undefined,
        fields: s.wizardFields,
      }
      send({ t: 'set_extraction', config })
      send({ t: 'set_mode', mode: 'record' })
      return { ...s, wizardStep: 'idle', mode: 'record', pickResult: null, lastCompiled: null }
    })
  }, [send])

  // Cancelling the wizard is exactly "go back to recording" — same teardown, so
  // the two paths can't drift apart.
  const cancelWizard = useCallback(() => setMode('record'), [setMode])

  const suggestAuthoring = useCallback(() => {
    setState((s) => ({ ...s, authoringPending: true }))
    send({ t: 'suggest_authoring' })
  }, [send])

  const dismissParameterSuggestion = useCallback((stepI: number) => {
    setState((s) => ({ ...s, parameterSuggestions: s.parameterSuggestions.filter((sg) => sg.step_i !== stepI) }))
  }, [])

  const dismissExtractionFieldSuggestion = useCallback((selector: string) => {
    setState((s) => ({
      ...s,
      extractionFieldSuggestions: s.extractionFieldSuggestions.filter((sg) => sg.selector !== selector),
    }))
  }, [])

  return {
    status: state.status,
    steps: state.steps,
    error: state.error,
    saved: state.saved,
    mode: state.mode,
    pickResult: state.pickResult,
    extractionResult: state.extractionResult,
    parameters: state.parameters,
    warnings: state.warnings,
    authoringPending: state.authoringPending,
    parameterSuggestions: state.parameterSuggestions,
    extractionFieldSuggestions: state.extractionFieldSuggestions,
    wizardStep: state.wizardStep,
    wizardMode: state.wizardMode,
    wizardRoots: state.wizardRoots,
    wizardFields: state.wizardFields,
    lastCompiled: state.lastCompiled,
    compiling: state.compiling,
    setMode,
    undoStep,
    bringToFront: () => send({ t: 'bring_to_front' }),
    markParam: (stepI: number, name: string, type?: string, description?: string | null) =>
      send({ t: 'mark_param', step_i: stepI, name, type, description }),
    setExtraction: (config: ExtractionConfig) => send({ t: 'set_extraction', config }),
    testExtraction: () => send({ t: 'test_extraction' }),
    suggestAuthoring,
    dismissParameterSuggestion,
    dismissExtractionFieldSuggestion,
    save: () => send({ t: 'save' }),
    /** Ends the recording session — the browser closes and the worker persists
     * everything — without claiming the workflow is saved. The user stays on
     * the recorder page to review the steps and save from there. */
    endSession: () => send({ t: 'end_session' }),
    cancel: () => send({ t: 'cancel' }),
    startWizard,
    chooseWizardMode,
    confirmRoot,
    compileValue,
    addCompiledField,
    undoPick,
    finishWizard,
    cancelWizard,
  }
}

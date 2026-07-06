import { useCallback, useEffect, useRef, useState } from 'react'
import type { RecorderStatus, Step } from '../lib/types'

interface RecorderState {
  status: RecorderStatus
  steps: Step[]
  error: string | null
  saved: boolean
}

const RECONNECT_DELAY_MS = 2000

export function useRecorder(workflowId: string) {
  const [state, setState] = useState<RecorderState>({
    status: 'connecting',
    steps: [],
    error: null,
    saved: false,
  })
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<number | null>(null)
  const terminal = useRef(false)

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
          setState((s) => ({ ...s, steps: s.steps.filter((step) => step.i !== msg.i) }))
          break
        case 'error':
          setState((s) => ({ ...s, error: msg.message }))
          break
        case 'saved':
          setState((s) => ({ ...s, saved: true }))
          break
        case 'died':
          terminal.current = true
          setState((s) => ({ ...s, status: 'died', error: 'The recorder crashed unexpectedly.' }))
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

  return {
    status: state.status,
    steps: state.steps,
    error: state.error,
    saved: state.saved,
    undoStep: (i: number) => send({ t: 'undo_step', i }),
    bringToFront: () => send({ t: 'bring_to_front' }),
    save: () => send({ t: 'save' }),
    cancel: () => send({ t: 'cancel' }),
  }
}

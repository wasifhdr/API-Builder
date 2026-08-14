import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../lib/api'
import type { AgentActivityEntry, AgentRun, AgentVerifyCheck } from '../lib/agentTypes'

interface AgentRunState {
  run: AgentRun | null
  activity: AgentActivityEntry[]
  checks: AgentVerifyCheck[]
  connectionError: string | null
}

const RECONNECT_DELAY_MS = 2000
const TERMINAL_STATES = new Set(['succeeded', 'failed'])

/** Connects to an agent run's progress channel and keeps AgentRunOut fresh.
 *
 * The WS carries lightweight notifications only (status transitions, tool
 * activity, verify results) — it is not the source of truth for the run's
 * full state (plan, resolved_url, attempt, failure_reason). Every WS message
 * triggers a re-fetch of GET /agent/runs/:id, which is: REST for state,
 * WS for "something changed, go refetch." */
export function useAgentRun(runId: string | null) {
  const [state, setState] = useState<AgentRunState>({
    run: null,
    activity: [],
    checks: [],
    connectionError: null,
  })
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<number | null>(null)
  const terminal = useRef(false)

  const refresh = useCallback(async () => {
    if (!runId) return
    try {
      const run = await api.get<AgentRun>(`/agent/runs/${runId}`)
      setState((s) => ({ ...s, run, connectionError: null }))
      if (TERMINAL_STATES.has(run.status)) terminal.current = true
    } catch (err) {
      setState((s) => ({ ...s, connectionError: err instanceof Error ? err.message : 'Failed to load run' }))
    }
  }, [runId])

  const connect = useCallback(() => {
    if (!runId) return
    const protocol = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${protocol}://${location.host}/api/ws/agent/${runId}`)
    wsRef.current = ws

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      if (msg.t === 'step') {
        setState((s) => ({ ...s, activity: [...s.activity, { tool: msg.tool, detail: msg.detail }] }))
      } else if (msg.t === 'verify') {
        setState((s) => ({ ...s, checks: msg.checks ?? [] }))
      }
      // status / workflow_ready and everything else: just go refetch the
      // authoritative row rather than trying to keep two copies in sync.
      void refresh()
    }

    ws.onclose = () => {
      if (terminal.current) return
      reconnectTimer.current = window.setTimeout(connect, RECONNECT_DELAY_MS)
    }

    ws.onerror = () => ws.close()
  }, [runId, refresh])

  useEffect(() => {
    if (!runId) return
    terminal.current = false
    void refresh()
    connect()
    return () => {
      terminal.current = true
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [runId, connect, refresh])

  const confirmUrl = useCallback(
    async (ok: boolean) => {
      if (!runId) return
      await api.post(`/agent/runs/${runId}/confirm`, { ok })
    },
    [runId],
  )

  return {
    run: state.run,
    activity: state.activity,
    checks: state.checks,
    connectionError: state.connectionError,
    confirmUrl,
  }
}

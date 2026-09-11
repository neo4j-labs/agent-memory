import { useCallback, useEffect, useRef, useState } from 'react'
import { streamChatMessage, type AgentEvent } from '../lib/api'

export interface AgentToolCall {
  tool: string
  args: Record<string, unknown>
  result?: string
  durationMs?: number
  timestamp?: number
}

export interface MemoryAccess {
  operation: string
  tool: string
  query?: string
  timestamp?: number
}

export interface AgentState {
  status: 'pending' | 'active' | 'complete'
  toolCalls: AgentToolCall[]
  memoryAccesses: MemoryAccess[]
  /**
   * Accumulated `thinking` text. Strands streams this as token deltas (one SSE
   * event per delta), so the deltas are concatenated into one buffer rather
   * than kept as separate entries.
   */
  thinking: string
  startedAt?: number
  completedAt?: number
}

export interface StreamResult {
  sessionId: string | null
  agentsConsulted: string[]
  toolCallCount: number
  totalDurationMs: number
  traceId: string | null
}

/** Everything one completed (or failed) turn produced. */
export interface StreamOutcome {
  response: string | null
  error: string | null
  agentStates: Map<string, AgentState>
  streamResult: StreamResult | null
  aborted: boolean
}

const emptyAgent = (): AgentState => ({
  status: 'pending',
  toolCalls: [],
  memoryAccesses: [],
  thinking: '',
})

/**
 * Consume `POST /api/chat/stream` and expose per-agent state for the UI.
 *
 * `startStream` resolves with a `StreamOutcome`, so callers update their own
 * state when the turn ends instead of mirroring hook state through effects.
 *
 * The hook handles the whole event protocol in `lib/api.ts`, including the
 * `agent_delegate` and `memory_access` events the backend does not emit yet:
 * the orchestration panel fills in further with no frontend change.
 */
export function useAgentStream() {
  const [isStreaming, setIsStreaming] = useState(false)
  const [activeAgent, setActiveAgent] = useState<string | null>(null)
  const [agentStates, setAgentStates] = useState<Map<string, AgentState>>(new Map())
  const [delegationChain, setDelegationChain] = useState<Array<{ from: string; to: string }>>([])
  const [traceId, setTraceId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const abortRef = useRef<AbortController | null>(null)

  // Tear the request down on unmount so the agent is not left running.
  useEffect(() => () => abortRef.current?.abort(), [])

  const startStream = useCallback(
    async (message: string, sessionId?: string, customerId?: string): Promise<StreamOutcome> => {
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      const outcome: StreamOutcome = {
        response: null,
        error: null,
        agentStates: new Map<string, AgentState>(),
        streamResult: null,
        aborted: false,
      }

      setIsStreaming(true)
      setActiveAgent(null)
      setAgentStates(outcome.agentStates)
      setDelegationChain([])
      setTraceId(null)
      setError(null)

      // The backend cannot attribute every event (Strands reports tool use
      // without an agent), so unattributed events land on the agent that is
      // currently running.
      let currentAgent = 'supervisor'

      const updateAgent = (name: string, updater: (state: AgentState) => AgentState) => {
        const next = new Map(outcome.agentStates)
        next.set(name, updater(next.get(name) ?? emptyAgent()))
        outcome.agentStates = next
        setAgentStates(next)
      }

      const handleEvent = (event: AgentEvent) => {
        switch (event.type) {
          case 'agent_start': {
            currentAgent = event.agent
            setActiveAgent(event.agent)
            updateAgent(event.agent, (s) => ({
              ...s,
              status: 'active',
              startedAt: event.timestamp,
            }))
            break
          }

          case 'agent_complete': {
            updateAgent(event.agent, (s) => ({
              ...s,
              status: 'complete',
              completedAt: event.timestamp,
            }))
            setActiveAgent((current) => (current === event.agent ? null : current))
            break
          }

          case 'agent_delegate': {
            currentAgent = event.to
            setDelegationChain((prev) => [...prev, { from: event.from, to: event.to }])
            // The delegation target may not have emitted agent_start yet.
            updateAgent(event.to, (s) => s)
            break
          }

          case 'tool_call': {
            updateAgent(event.agent ?? currentAgent, (s) => ({
              ...s,
              toolCalls: [
                ...s.toolCalls,
                { tool: event.tool, args: event.args ?? {}, timestamp: event.timestamp },
              ],
            }))
            break
          }

          case 'tool_result': {
            updateAgent(event.agent ?? currentAgent, (s) => {
              // Attach the result to the latest still-pending call of this tool.
              const fromEnd = [...s.toolCalls]
                .reverse()
                .findIndex((call) => call.tool === event.tool && call.result === undefined)
              if (fromEnd === -1) return s
              const target = s.toolCalls.length - 1 - fromEnd
              return {
                ...s,
                toolCalls: s.toolCalls.map((call, i) =>
                  i === target
                    ? { ...call, result: event.result, durationMs: event.duration_ms }
                    : call,
                ),
              }
            })
            break
          }

          case 'memory_access': {
            updateAgent(event.agent ?? currentAgent, (s) => ({
              ...s,
              memoryAccesses: [
                ...s.memoryAccesses,
                {
                  operation: event.operation,
                  tool: event.tool,
                  query: event.query,
                  timestamp: event.timestamp,
                },
              ],
            }))
            break
          }

          case 'thinking': {
            // The backend sends `content`; accept `thought` too.
            const delta = event.thought ?? event.content
            if (!delta) break
            updateAgent(event.agent ?? currentAgent, (s) => ({
              ...s,
              thinking: s.thinking + delta,
            }))
            break
          }

          case 'response': {
            outcome.response = event.content
            break
          }

          case 'trace_saved': {
            // The reasoning trace is in Neo4j now; the memory panel can read it.
            setTraceId(event.trace_id)
            break
          }

          case 'done': {
            outcome.streamResult = {
              sessionId: event.session_id,
              agentsConsulted: event.agents_consulted ?? [],
              toolCallCount: event.tool_call_count ?? 0,
              totalDurationMs: event.total_duration_ms ?? 0,
              traceId: event.trace_id ?? null,
            }
            if (event.trace_id) setTraceId(event.trace_id)
            break
          }

          case 'error': {
            outcome.error = event.message
            setError(event.message)
            break
          }
        }
      }

      try {
        await streamChatMessage(
          { message, session_id: sessionId, customer_id: customerId },
          handleEvent,
          { signal: controller.signal },
        )
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          outcome.aborted = true
        } else {
          outcome.error = err instanceof Error ? err.message : 'Stream failed'
          setError(outcome.error)
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null
        setIsStreaming(false)
        setActiveAgent(null)
      }

      return outcome
    },
    [],
  )

  /** Abort the in-flight turn (the backend sees the client disconnect). */
  const stop = useCallback(() => abortRef.current?.abort(), [])

  const reset = useCallback(() => {
    abortRef.current?.abort()
    setIsStreaming(false)
    setActiveAgent(null)
    setAgentStates(new Map())
    setDelegationChain([])
    setTraceId(null)
    setError(null)
  }, [])

  return {
    isStreaming,
    activeAgent,
    agentStates,
    delegationChain,
    traceId,
    error,
    startStream,
    stop,
    reset,
  }
}

import axios from 'axios'

/**
 * Base URL for the FastAPI backend.
 *
 * Development: leave `VITE_API_URL` unset and the app talks to the relative
 * `/api` path, which `vite.config.ts` proxies to http://localhost:8000.
 * Deployment: set `VITE_API_URL` (see `.env.example`) to the absolute URL of
 * the backend, e.g. `https://compliance-api.example.com/api`.
 */
export const API_BASE = import.meta.env.VITE_API_URL || '/api'

const api = axios.create({
  baseURL: API_BASE,
  headers: {
    'Content-Type': 'application/json',
  },
})

// ── Types ────────────────────────────────────────────────────────────────────

/** A customer as returned by `GET /api/customers`. */
export interface Customer {
  id: string
  name: string
  type: string
  jurisdiction?: string | null
  /** Derived server-side; upper-case (`LOW` | `MEDIUM` | `HIGH` | `CRITICAL`). */
  risk_level: string
  risk_score?: number
  risk_factors?: string[]
  kyc_status?: string
  nationality?: string | null
  business_type?: string | null
}

export interface CustomerRisk {
  customer_id: string
  overall_risk: string
  risk_score: number
  geographic_risk: number
  customer_type_risk: number
  transaction_risk: number
  network_risk: number
  risk_factors: string[]
  recommendations: string[]
}

/** An alert as returned by `GET /api/alerts`. Status/severity are upper-case. */
export interface Alert {
  id: string
  type: string
  severity: string
  status: string
  customer_id: string
  customer_name?: string | null
  title: string
  description: string
  evidence?: string[]
  requires_sar?: boolean
  created_at?: string | null
  acknowledged_at?: string | null
  resolved_at?: string | null
}

export interface AlertSummary {
  total: number
  /** Keys are upper-case (`NEW`, `ACKNOWLEDGED`, ...). Use `countByStatus()`. */
  by_status: Record<string, number>
  by_severity: Record<string, number>
  critical_unresolved: number
  high_unresolved: number
}

/** An investigation as returned by `GET /api/investigations`. */
export interface Investigation {
  id: string
  customer_id: string
  customer_name?: string | null
  title: string
  status: string
  priority: string
  created_at?: string | null
  /** Not set by the backend today; kept optional so the UI can degrade. */
  findings_count?: number
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: string | null
}

export interface ChatResponse {
  response: string
  session_id: string
  agent: string
  metadata: Record<string, unknown>
}

// ── Graph shapes ─────────────────────────────────────────────────────────────

export interface GraphNode {
  id: string
  label: string
  labels: string[]
  properties: Record<string, unknown>
}

export interface GraphRelationship {
  id: string
  from: string
  to: string
  type: string
}

export interface GraphData {
  nodes: GraphNode[]
  relationships: GraphRelationship[]
}

/**
 * `GET /api/graph/neighbors/{id}` currently returns a single `type` string per
 * node instead of the `labels` array `GET /api/graph/memory` returns. Both are
 * declared here so `normalizeNeighborNode()` can bridge the two shapes until
 * the backend emits `labels` everywhere (see the README's "Known gaps").
 */
export interface NeighborNode {
  id: string
  label?: string
  labels?: string[]
  type?: string
  isRoot?: boolean
}

export interface NeighborEdge {
  from?: string
  to: string
  relationship?: string
}

export interface NeighborsResponse {
  entity_id: string
  depth: number
  nodes: NeighborNode[]
  edges: NeighborEdge[]
  total_nodes: number
  total_edges: number
}

/** Bring a `/graph/neighbors` node onto the `/graph/memory` node shape. */
export function normalizeNeighborNode(node: NeighborNode): GraphNode {
  const labels =
    node.labels && node.labels.length > 0 ? node.labels : node.type ? [node.type] : []
  return {
    id: node.id,
    label: node.label || node.id,
    labels,
    properties: { ...node },
  }
}

// ── SSE event protocol ───────────────────────────────────────────────────────

/**
 * Events the backend's `POST /api/chat/stream` emits.
 *
 * The backend drives `supervisor.stream_async(...)`, so `thinking`, `tool_call`
 * and `tool_result` arrive while the turn runs. `agent_delegate` and
 * `memory_access` are part of the protocol but not emitted yet (the supervisor
 * owns every tool today) - `useAgentStream` handles them regardless.
 *
 * Fields the backend cannot always fill in are optional here, so a partially
 * attributed event still renders instead of producing an `undefined` agent.
 */
export type AgentEvent =
  | { type: 'agent_start'; agent: string; timestamp: number }
  | { type: 'agent_complete'; agent: string; timestamp: number }
  | { type: 'agent_delegate'; from: string; to: string; timestamp: number }
  | {
      type: 'tool_call'
      /** Omitted when the backend cannot attribute the call; defaults to the active agent. */
      agent?: string
      tool: string
      /** Strands streams the tool input incrementally, so this can be absent. */
      args?: Record<string, unknown>
      timestamp?: number
    }
  | {
      type: 'tool_result'
      agent?: string
      tool: string
      result: string
      duration_ms?: number
      timestamp?: number
    }
  | {
      type: 'memory_access'
      agent?: string
      operation: string
      tool: string
      query?: string
      timestamp?: number
    }
  | {
      type: 'thinking'
      agent?: string
      /** The backend sends `content`; `thought` is accepted too. */
      thought?: string
      content?: string
      timestamp?: number
    }
  | { type: 'response'; content: string; session_id: string }
  | { type: 'trace_saved'; trace_id: string; step_count: number; tool_call_count: number }
  | {
      type: 'done'
      session_id: string
      agents_consulted: string[]
      tool_call_count: number
      total_duration_ms: number
      trace_id?: string | null
    }
  | { type: 'error'; message: string }

const KNOWN_EVENT_TYPES: ReadonlySet<string> = new Set<AgentEvent['type']>([
  'agent_start',
  'agent_complete',
  'agent_delegate',
  'tool_call',
  'tool_result',
  'memory_access',
  'thinking',
  'response',
  'trace_saved',
  'done',
  'error',
])

// ── Reasoning traces ─────────────────────────────────────────────────────────

export interface TraceToolCall {
  tool_name: string
  arguments: Record<string, unknown>
  result: unknown
  status: string
  duration_ms?: number | null
}

export interface TraceStep {
  id: string
  step_number: number
  thought?: string | null
  action?: string | null
  observation?: string | null
  tool_calls: TraceToolCall[]
}

export interface ReasoningTrace {
  id: string
  session_id: string
  task: string
  outcome?: string | null
  success?: boolean | null
  started_at?: string | null
  completed_at?: string | null
  steps: TraceStep[]
}

// ── SSE parsing ──────────────────────────────────────────────────────────────

/** One complete `event:`/`data:` frame from the stream. */
export interface SseFrame {
  event: string
  data: string
}

/**
 * Split a decoded chunk buffer into complete SSE frames plus the unparsed
 * remainder.
 *
 * Frames are delimited by a blank line, so an `event:` line and its `data:`
 * line are only ever handed over together — a network chunk that ends between
 * the two no longer loses the event. Keep the returned `rest` and prepend it to
 * the next chunk.
 */
export function splitSseFrames(buffer: string): { frames: SseFrame[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, '\n')
  const frames: SseFrame[] = []
  let rest = normalized

  for (;;) {
    const boundary = rest.indexOf('\n\n')
    if (boundary === -1) break
    const frame = parseSseFrame(rest.slice(0, boundary))
    rest = rest.slice(boundary + 2)
    if (frame) frames.push(frame)
  }

  return { frames, rest }
}

function parseSseFrame(raw: string): SseFrame | null {
  let event = ''
  const dataLines: string[] = []

  for (const line of raw.split('\n')) {
    if (line.startsWith(':') || line.trim() === '') continue
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice('data:'.length).replace(/^ /, ''))
    }
  }

  if (!event || dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}

/** Turn a frame into a typed event, or `null` (with a warning) if malformed. */
export function decodeAgentEvent(frame: SseFrame): AgentEvent | null {
  if (!KNOWN_EVENT_TYPES.has(frame.event)) {
    console.warn(`[sse] ignoring unknown event type "${frame.event}"`)
    return null
  }
  try {
    const payload = JSON.parse(frame.data) as Record<string, unknown>
    return { ...payload, type: frame.event } as AgentEvent
  } catch (err) {
    console.warn(`[sse] could not parse "${frame.event}" payload`, err)
    return null
  }
}

export interface StreamChatParams {
  message: string
  session_id?: string
  customer_id?: string
}

/**
 * POST a message and invoke `onEvent` for every SSE frame the backend sends.
 *
 * Pass `signal` (from an `AbortController`) so unmounting or navigating away
 * tears the request down instead of leaving the agent running.
 */
export async function streamChatMessage(
  params: StreamChatParams,
  onEvent: (event: AgentEvent) => void,
  options: { signal?: AbortSignal } = {},
): Promise<void> {
  const response = await fetch(`${API_BASE}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
    signal: options.signal,
  })

  if (!response.ok) {
    throw new Error(`Stream failed: ${response.status} ${response.statusText}`)
  }

  const reader = response.body?.getReader()
  if (!reader) throw new Error('No response body')

  const decoder = new TextDecoder()
  let buffer = ''

  const emit = (frames: SseFrame[]) => {
    for (const frame of frames) {
      const event = decodeAgentEvent(frame)
      if (event) onEvent(event)
    }
  }

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const { frames, rest } = splitSseFrames(buffer)
      buffer = rest
      emit(frames)
    }

    // A final frame may arrive without its trailing blank line.
    buffer += decoder.decode()
    const { frames, rest } = splitSseFrames(`${buffer}\n\n`)
    if (rest.trim() === '') emit(frames)
  } finally {
    await reader.cancel().catch(() => undefined)
  }
}

// ── API clients ──────────────────────────────────────────────────────────────

/** Some endpoints return a bare list, others an envelope - accept both. */
function asList<T>(data: unknown, key: string): T[] {
  if (Array.isArray(data)) return data as T[]
  if (data && typeof data === 'object') {
    const envelope = (data as Record<string, unknown>)[key]
    if (Array.isArray(envelope)) return envelope as T[]
  }
  return []
}

/** Read a count out of an alert summary regardless of key casing. */
export function countByStatus(summary: AlertSummary | undefined, status: string): number {
  if (!summary?.by_status) return 0
  const entry = Object.entries(summary.by_status).find(
    ([key]) => key.toLowerCase() === status.toLowerCase(),
  )
  return entry?.[1] ?? 0
}

export const chatApi = {
  sendMessage: async (
    message: string,
    sessionId?: string,
    customerId?: string,
  ): Promise<ChatResponse> => {
    const { data } = await api.post('/chat', {
      message,
      session_id: sessionId,
      customer_id: customerId,
    })
    return data
  },

  getHistory: async (sessionId: string, limit = 50): Promise<ChatMessage[]> => {
    const { data } = await api.get(`/chat/history/${encodeURIComponent(sessionId)}`, {
      params: { limit },
    })
    return asList<ChatMessage>(data, 'messages')
  },
}

export const customerApi = {
  list: async (page = 1, pageSize = 20): Promise<{ customers: Customer[]; total: number }> => {
    const { data } = await api.get('/customers', { params: { page, page_size: pageSize } })
    const customers = asList<Customer>(data, 'customers')
    return { customers, total: data?.total ?? customers.length }
  },

  get: async (customerId: string): Promise<Customer> => {
    const { data } = await api.get(`/customers/${encodeURIComponent(customerId)}`)
    return data
  },

  getRisk: async (customerId: string): Promise<CustomerRisk> => {
    const { data } = await api.get(`/customers/${encodeURIComponent(customerId)}/risk`)
    return data
  },
}

export const alertApi = {
  list: async (params?: { status?: string; severity?: string }): Promise<Alert[]> => {
    const { data } = await api.get('/alerts', { params })
    return asList<Alert>(data, 'alerts')
  },

  get: async (alertId: string): Promise<Alert> => {
    const { data } = await api.get(`/alerts/${encodeURIComponent(alertId)}`)
    return data
  },

  /**
   * Acknowledge an alert. The backend exposes this as a generic
   * `PATCH /alerts/{id}` status update - there is no `/acknowledge` route.
   */
  acknowledge: async (alertId: string, analystId?: string): Promise<Alert> => {
    const { data } = await api.patch(`/alerts/${encodeURIComponent(alertId)}`, {
      status: 'ACKNOWLEDGED',
      assigned_to: analystId,
    })
    return data
  },

  getSummary: async (): Promise<AlertSummary> => {
    const { data } = await api.get('/alerts/summary')
    return data
  },
}

export const investigationApi = {
  list: async (params?: { status?: string; customer_id?: string }): Promise<Investigation[]> => {
    const { data } = await api.get('/investigations', { params })
    return asList<Investigation>(data, 'investigations')
  },

  get: async (investigationId: string): Promise<Investigation> => {
    const { data } = await api.get(`/investigations/${encodeURIComponent(investigationId)}`)
    return data
  },

  create: async (investigation: {
    customer_id: string
    title: string
    description: string
    trigger: string
  }): Promise<Investigation> => {
    const { data } = await api.post('/investigations', investigation)
    return data
  },

  start: async (
    investigationId: string,
  ): Promise<{ status: string; preliminary_response?: string }> => {
    const { data } = await api.post(
      `/investigations/${encodeURIComponent(investigationId)}/start`,
      {
        run_kyc: true,
        run_aml: true,
        run_relationship: true,
        run_compliance: true,
      },
    )
    return data
  },
}

export const tracesApi = {
  /** Reasoning traces recorded for a chat session (survives a page reload). */
  getSessionTraces: async (sessionId: string, limit = 20): Promise<ReasoningTrace[]> => {
    const { data } = await api.get(`/traces/${encodeURIComponent(sessionId)}`, {
      params: { limit },
    })
    return asList<ReasoningTrace>(data, 'traces')
  },

  getTraceDetail: async (traceId: string): Promise<ReasoningTrace> => {
    const { data } = await api.get(`/traces/detail/${encodeURIComponent(traceId)}`)
    return data
  },
}

// ── Long-term memory (optional backend endpoint) ─────────────────────────────

export interface MemoryEntity {
  name: string
  type?: string | null
  subtype?: string | null
  description?: string | null
  mention_count?: number | null
}

export interface MemoryPreference {
  category: string
  value: string
  confidence?: number | null
}

export interface MemoryContext {
  session_id?: string
  entities: MemoryEntity[]
  preferences: MemoryPreference[]
}

export const memoryApi = {
  /**
   * Long-term memory for a session: entities extracted from the conversation
   * plus any detected preferences.
   *
   * `GET /api/memory/context` is not implemented by the backend yet (the
   * entities *are* written - `chat_stream` calls `add_session(...,
   * extract_entities=True)` - they are just not exposed over HTTP). Returns
   * `null` on 404 so the panel can say so instead of erroring.
   */
  getContext: async (sessionId: string): Promise<MemoryContext | null> => {
    try {
      const { data } = await api.get('/memory/context', { params: { session_id: sessionId } })
      return {
        session_id: data?.session_id ?? sessionId,
        entities: asList<MemoryEntity>(data, 'entities'),
        preferences: asList<MemoryPreference>(data, 'preferences'),
      }
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 404) return null
      throw err
    }
  },
}

export const graphApi = {
  getMemoryGraph: async (limit = 500, sessionId?: string): Promise<GraphData> => {
    const { data } = await api.get('/graph/memory', {
      params: { limit, session_id: sessionId },
    })
    return {
      nodes: asList<GraphNode>(data, 'nodes'),
      relationships: asList<GraphRelationship>(data, 'relationships'),
    }
  },

  getNeighbors: async (nodeId: string, depth = 1, limit = 20): Promise<NeighborsResponse> => {
    const { data } = await api.get(`/graph/neighbors/${encodeURIComponent(nodeId)}`, {
      params: { depth, limit },
    })
    return data
  },
}

export default api

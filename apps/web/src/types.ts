export interface ToolCall {
  tool: string
  input: Record<string, unknown>
  result: string
}

export interface AgentSession {
  session_id: string
  task: string
  status: 'running' | 'completed' | 'failed'
  messages: unknown[]
  tool_calls: ToolCall[]
  result: string | null
  created_at: string
  completed_at: string | null
}

// ---------- RAG chat ----------

export type ChatMode = 'rag' | 'agent'

export interface MetaClassification {
  route: ChatMode
  reasoning: string
}

export interface ChatSource {
  title: string
  url: string
  source: string
  snippet: string
}

// SSE frame types emitted by /agent_query_stream_v2.
export type StreamFrame =
  | {
      type: 'metadata'
      session_id: string | null
      resolved_question: string | null
      route: 'definitional' | 'multi_hop' | 'general' | null
      grade: 'relevant' | 'partial' | 'poor' | null
      attempt: number | null
      trace: string[]
      sources: ChatSource[]
      retrieved_chunks: number
      history_turns_loaded: number
    }
  | { type: 'token'; content: string }
  | { type: 'answer_complete'; content: string }
  | { type: 'error'; message: string }
  | { type: 'done' }

// Shape returned by GET /api/v1/rag/sessions/{id}/turns. Only role + content
// were persisted; the route/grade/sources from the original message are lost
// (deliberate: the conversation store is the source of truth for memory,
// not for UI replay).
export interface StoredTurn {
  role: 'user' | 'assistant'
  content: string
  turn_index: number
  timestamp: number
}

// One displayed chat message; the UI accumulates these in order.
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  // Only set on assistant messages produced by the RAG path:
  mode?: ChatMode
  reasoning?: string                                    // meta-classifier output
  ragRoute?: 'definitional' | 'multi_hop' | 'general'  // RAG agent's internal route
  ragGrade?: 'relevant' | 'partial' | 'poor'           // RAG agent's grade
  sources?: ChatSource[]
  toolCalls?: ToolCall[]                                // for agent-mode messages
  streaming?: boolean                                   // true while tokens still arriving
  error?: string
  hydrated?: boolean                                    // true if loaded from /sessions/{id}/turns
}

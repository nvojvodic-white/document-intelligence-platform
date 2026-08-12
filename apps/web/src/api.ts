import type {
  AgentSession,
  MetaClassification,
  StoredTurn,
  StreamFrame,
} from './types'

const BASE = '/api/v1'

// Optional API key — set VITE_API_KEY in frontend/.env.local for local dev
const API_KEY = import.meta.env.VITE_API_KEY as string | undefined

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra }
  if (API_KEY) h['X-API-Key'] = API_KEY
  return h
}

export async function listSessions(): Promise<AgentSession[]> {
  const res = await fetch(`${BASE}/sessions`, { headers: headers() })
  if (!res.ok) throw new Error('Failed to fetch sessions')
  return res.json()
}

export async function getSession(id: string): Promise<AgentSession> {
  const res = await fetch(`${BASE}/sessions/${id}`, { headers: headers() })
  if (!res.ok) throw new Error('Failed to fetch session')
  return res.json()
}

export async function createSession(task: string): Promise<{ session_id: string; status: string }> {
  const res = await fetch(`${BASE}/sessions`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ task }),
  })
  if (!res.ok) throw new Error('Failed to create session')
  return res.json()
}

export async function deleteSession(id: string): Promise<void> {
  await fetch(`${BASE}/sessions/${id}`, { method: 'DELETE', headers: headers() })
}

// ---------- RAG: meta-classifier + streaming chat -------------------------

export async function routeQuestion(
  question: string,
  history?: { role: string; content: string }[],
): Promise<MetaClassification> {
  const body: Record<string, unknown> = { question }
  if (history && history.length > 0) body.history = history
  const res = await fetch(`${BASE}/rag/route_question`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`routeQuestion failed: ${res.status}`)
  return res.json()
}

export async function deleteRagSession(sessionId: string): Promise<void> {
  await fetch(`${BASE}/rag/sessions/${sessionId}`, {
    method: 'DELETE',
    headers: headers(),
  })
}

/**
 * Fetch stored conversation turns for a RAG session. Used to hydrate the chat
 * on page reload. Returns turns oldest-first. May be empty for a fresh session.
 */
export async function getRagSessionTurns(
  sessionId: string,
  limit = 50,
): Promise<StoredTurn[]> {
  const res = await fetch(
    `${BASE}/rag/sessions/${sessionId}/turns?limit=${limit}`,
    { headers: headers() },
  )
  if (!res.ok) throw new Error(`getRagSessionTurns failed: ${res.status}`)
  const data = (await res.json()) as { session_id: string; turns: StoredTurn[] }
  return data.turns
}

/**
 * Stream a RAG agent query as Server-Sent Events. Calls onFrame for each
 * parsed `data:` frame as it arrives; resolves when the server emits `done`
 * (or the stream closes). Uses fetch+ReadableStream (not EventSource: POST
 * with a JSON body is not supported by EventSource).
 */
export async function streamRagQuery(
  question: string,
  sessionId: string | null,
  onFrame: (frame: StreamFrame) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${BASE}/rag/agent_query_stream_v2`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ question, session_id: sessionId }),
    signal,
  })
  if (!res.ok || !res.body) {
    throw new Error(`streamRagQuery failed: ${res.status}`)
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // SSE frames are separated by blank lines (\n\n). The last fragment may be
    // a partial frame; preserve it in the buffer for the next read.
    const parts = buffer.split('\n\n')
    buffer = parts.pop() ?? ''
    for (const part of parts) {
      const line = part.trim()
      if (!line.startsWith('data: ')) continue
      try {
        const payload = JSON.parse(line.slice(6)) as StreamFrame
        onFrame(payload)
        if (payload.type === 'done') return
      } catch (e) {
        console.warn('SSE frame parse error', e, line)
      }
    }
  }
}

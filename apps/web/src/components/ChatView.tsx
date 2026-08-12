import { useCallback, useEffect, useRef, useState } from 'react'
import {
  createSession,
  deleteRagSession,
  getRagSessionTurns,
  getSession,
  routeQuestion,
  streamRagQuery,
} from '../api'
import { useChatSession } from '../hooks/useChatSession'
import type { ChatMessage, ChatMode } from '../types'

const POLL_INTERVAL_MS = 1500
const POLL_TIMEOUT_MS = 120_000

/**
 * Unified chat surface. Auto-routes each user question via the
 * /route_question meta-classifier, then dispatches to either:
 *   - RAG path: streams tokens from /agent_query_stream_v2 (live)
 *   - Agent path: creates a session via /sessions and polls until done
 *
 * The meta-classifier's reasoning + chosen mode are shown on the assistant
 * message, with a "Re-run with X" button so a silent mis-route stays visible
 * and overridable.
 */
export function ChatView() {
  const { sessionId, resetSession } = useChatSession()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  // Hydrate visible chat from the backend conversation store when sessionId
  // changes (mount or post-reset). Only RAG turns are stored; agent turns
  // won't reappear (they live in app/agent/store.py). Per-turn metadata
  // (route, grade, sources, reasoning) was not persisted, so hydrated
  // assistant messages render without those badges and the source panel.
  // The `hydrated` flag lets MessageBubble suppress the override button on
  // restored turns (no original-question context to re-run from).
  useEffect(() => {
    let cancelled = false
    getRagSessionTurns(sessionId)
      .then((turns) => {
        if (cancelled || turns.length === 0) return
        setMessages((prev) => {
          // Only hydrate into an EMPTY chat. If the user has already started
          // sending messages in this mount, don't overwrite them.
          if (prev.length > 0) return prev
          return turns.map((t) => ({
            id: `hydrated-${sessionId}-${t.turn_index}`,
            role: t.role,
            content: t.content,
            mode: t.role === 'assistant' ? 'rag' : undefined,
            hydrated: true,
            streaming: false,
          }))
        })
      })
      .catch((e) => {
        // Non-fatal: a fresh session has no turns and may 404 or return [].
        console.warn('hydrate failed', e)
      })
    return () => {
      cancelled = true
    }
  }, [sessionId])

  const appendUser = (content: string): string => {
    const id = mkId()
    setMessages((prev) => [...prev, { id, role: 'user', content }])
    return id
  }

  const appendAssistant = (partial: Partial<ChatMessage>): string => {
    const id = mkId()
    setMessages((prev) => [
      ...prev,
      { id, role: 'assistant', content: '', streaming: true, ...partial },
    ])
    return id
  }

  const updateAssistant = (id: string, patch: Partial<ChatMessage>) => {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, ...patch } : m)),
    )
  }

  const appendToAssistant = (id: string, more: string) => {
    setMessages((prev) =>
      prev.map((m) =>
        m.id === id ? { ...m, content: m.content + more } : m,
      ),
    )
  }

  // RAG path: stream tokens, update message in place.
  const runRag = useCallback(
    async (question: string, assistantId: string, reasoning?: string) => {
      const ctrl = new AbortController()
      abortRef.current = ctrl
      try {
        await streamRagQuery(
          question,
          sessionId,
          (frame) => {
            if (frame.type === 'metadata') {
              updateAssistant(assistantId, {
                ragRoute: frame.route ?? undefined,
                ragGrade: frame.grade ?? undefined,
                sources: frame.sources,
                reasoning,
              })
            } else if (frame.type === 'token') {
              appendToAssistant(assistantId, frame.content)
            } else if (frame.type === 'error') {
              updateAssistant(assistantId, {
                error: frame.message,
                streaming: false,
              })
            } else if (frame.type === 'done') {
              updateAssistant(assistantId, { streaming: false })
            }
          },
          ctrl.signal,
        )
      } catch (e) {
        if ((e as Error).name === 'AbortError') return
        updateAssistant(assistantId, {
          error: (e as Error).message,
          streaming: false,
        })
      } finally {
        abortRef.current = null
      }
    },
    [sessionId],
  )

  // Agent path: create a session, poll until completed/failed.
  const runAgent = useCallback(
    async (task: string, assistantId: string, reasoning?: string) => {
      try {
        const { session_id } = await createSession(task)
        updateAssistant(assistantId, { reasoning, content: '' })
        const deadline = Date.now() + POLL_TIMEOUT_MS
        while (Date.now() < deadline) {
          await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS))
          const s = await getSession(session_id)
          if (s.status === 'completed' || s.status === 'failed') {
            updateAssistant(assistantId, {
              content:
                s.result ?? (s.status === 'failed' ? '(agent failed)' : ''),
              toolCalls: s.tool_calls,
              streaming: false,
              error: s.status === 'failed' ? (s.result ?? 'failed') : undefined,
            })
            return
          }
        }
        updateAssistant(assistantId, {
          error: 'agent timed out',
          streaming: false,
        })
      } catch (e) {
        updateAssistant(assistantId, {
          error: (e as Error).message,
          streaming: false,
        })
      }
    },
    [],
  )

  const sendQuestion = useCallback(
    async (question: string, forceMode?: ChatMode) => {
      if (!question.trim() || busy) return
      setBusy(true)
      // Snapshot the history BEFORE appending the user turn, so the slice
      // we send to the meta-classifier is the prior conversation, not the
      // question itself. Limit to last 4 turns (2 user/assistant pairs): the
      // classifier only needs enough context to spot a follow-up topic.
      const historyForRouting = messages
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .slice(-4)
        .map((m) => ({ role: m.role, content: m.content }))
      appendUser(question)
      let mode: ChatMode
      let reasoning: string
      if (forceMode) {
        mode = forceMode
        reasoning = `manual override: ${forceMode}`
      } else {
        try {
          const meta = await routeQuestion(question, historyForRouting)
          mode = meta.route
          reasoning = meta.reasoning
        } catch (e) {
          mode = 'agent'
          reasoning = `routing failed (${(e as Error).message}); defaulting to agent`
        }
      }
      const aid = appendAssistant({ mode, reasoning })
      if (mode === 'rag') {
        await runRag(question, aid, reasoning)
      } else {
        await runAgent(question, aid, reasoning)
      }
      setBusy(false)
    },
    [busy, messages, runAgent, runRag],
  )

  const retryWithMode = useCallback(
    (originalQuestion: string, otherMode: ChatMode) => {
      sendQuestion(originalQuestion, otherMode)
    },
    [sendQuestion],
  )

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const q = input.trim()
    if (!q) return
    setInput('')
    sendQuestion(q)
  }

  const onResetConversation = async () => {
    abortRef.current?.abort()
    const oldId = sessionId
    resetSession()
    setMessages([])
    try {
      await deleteRagSession(oldId)
    } catch {
      // Non-fatal: the new session id is fresh either way.
    }
  }

  return (
    <div className="chat">
      <div className="chat-header">
        <div className="chat-session">
          <span className="chat-session-label">RAG session</span>
          <code className="chat-session-id">{sessionId.slice(0, 16)}...</code>
        </div>
        <button
          type="button"
          className="chat-reset"
          onClick={onResetConversation}
          disabled={busy}
        >
          New conversation
        </button>
      </div>

      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            Ask a question. Middle-earth lore questions route to the RAG
            service (streaming, with citations); other questions go to the
            general agent (with tools).
          </div>
        )}
        {messages.map((m) => (
          <MessageBubble
            key={m.id}
            message={m}
            onRetryWithMode={(otherMode) => {
              const idx = messages.findIndex((x) => x.id === m.id)
              for (let i = idx - 1; i >= 0; i--) {
                if (messages[i].role === 'user') {
                  retryWithMode(messages[i].content, otherMode)
                  return
                }
              }
            }}
          />
        ))}
      </div>

      <form className="chat-input" onSubmit={onSubmit}>
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={
            busy ? 'Waiting for response...' : 'Ask anything (lore or general)'
          }
          disabled={busy}
        />
        <button type="submit" disabled={busy || !input.trim()}>
          Send
        </button>
      </form>
    </div>
  )
}

function MessageBubble({
  message,
  onRetryWithMode,
}: {
  message: ChatMessage
  onRetryWithMode: (otherMode: ChatMode) => void
}) {
  if (message.role === 'user') {
    return (
      <div className="msg msg-user">
        <div className="msg-body">{message.content}</div>
      </div>
    )
  }
  const otherMode: ChatMode = message.mode === 'rag' ? 'agent' : 'rag'
  return (
    <div className={`msg msg-assistant msg-${message.mode ?? 'unknown'}`}>
      <div className="msg-meta">
        {message.mode && (
          <span className={`mode-badge mode-${message.mode}`}>
            {message.mode.toUpperCase()}
          </span>
        )}
        {message.ragRoute && (
          <span className="rag-route">→ {message.ragRoute}</span>
        )}
        {message.ragGrade && (
          <span className={`rag-grade grade-${message.ragGrade}`}>
            grade: {message.ragGrade}
          </span>
        )}
        {message.hydrated && (
          <span className="hydrated-tag" title="Restored from previous session">
            restored
          </span>
        )}
        {message.streaming && <span className="streaming-dot" />}
      </div>
      {message.reasoning && (
        <div className="msg-reasoning">{message.reasoning}</div>
      )}
      <div className="msg-body">
        {message.error ? (
          <span className="msg-error">{message.error}</span>
        ) : (
          message.content || (message.streaming ? '...' : '')
        )}
      </div>
      {message.sources && message.sources.length > 0 && (
        <details className="msg-sources">
          <summary>{message.sources.length} sources</summary>
          <ul>
            {message.sources.map((s, i) => (
              <li key={i}>
                <strong>{s.title}</strong>{' '}
                <span className="src-source">[{s.source}]</span>
                {s.url && (
                  <>
                    {' '}
                    <a href={s.url} target="_blank" rel="noreferrer">
                      link
                    </a>
                  </>
                )}
                <div className="src-snippet">{s.snippet}</div>
              </li>
            ))}
          </ul>
        </details>
      )}
      {message.toolCalls && message.toolCalls.length > 0 && (
        <details className="msg-tools">
          <summary>{message.toolCalls.length} tool calls</summary>
          <ul>
            {message.toolCalls.map((t, i) => (
              <li key={i}>
                <code>{t.tool}</code>: {JSON.stringify(t.input)}
              </li>
            ))}
          </ul>
        </details>
      )}
      {!message.streaming && message.mode && !message.hydrated && (
        <button
          type="button"
          className="msg-retry"
          onClick={() => onRetryWithMode(otherMode)}
        >
          Re-run with {otherMode.toUpperCase()}
        </button>
      )}
    </div>
  )
}

function mkId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return (crypto as Crypto).randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

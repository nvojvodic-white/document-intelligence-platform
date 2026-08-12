import { useCallback, useEffect, useState } from 'react'

const KEY = 'agent-platform.rag-session-id'

/**
 * RAG conversation id persisted in localStorage so memory survives reload.
 * Auto-generates on first mount. resetSession() makes a new id (and the caller
 * should also DELETE the old one server-side).
 */
export function useChatSession(): {
  sessionId: string
  resetSession: () => string
} {
  const [sessionId, setSessionId] = useState<string>(() => {
    const existing = localStorage.getItem(KEY)
    if (existing) return existing
    const fresh = newId()
    localStorage.setItem(KEY, fresh)
    return fresh
  })

  useEffect(() => {
    localStorage.setItem(KEY, sessionId)
  }, [sessionId])

  const resetSession = useCallback(() => {
    const fresh = newId()
    setSessionId(fresh)
    return fresh
  }, [])

  return { sessionId, resetSession }
}

function newId(): string {
  // crypto.randomUUID exists in modern browsers; fallback for older envs.
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return `chat-${(crypto as Crypto).randomUUID()}`
  }
  return `chat-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`
}

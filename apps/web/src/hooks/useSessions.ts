import { useState, useEffect, useCallback } from 'react'
import { listSessions } from '../api'
import type { AgentSession } from '../types'

const POLL_MS = 3000

export function useSessions() {
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const fetch = useCallback(async () => {
    try {
      const data = await listSessions()
      setSessions(data.sort((a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      ))
      setError(null)
    } catch {
      setError('Cannot reach API')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    // Schedule the first poll instead of calling fetch() inline: the state
    // update then comes from a timer callback, and unmounting cancels the
    // initial request the same way it cancels the interval.
    const first = setTimeout(fetch, 0)
    const id = setInterval(fetch, POLL_MS)
    return () => {
      clearTimeout(first)
      clearInterval(id)
    }
  }, [fetch])

  return { sessions, loading, error, refresh: fetch }
}

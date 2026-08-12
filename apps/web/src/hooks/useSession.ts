import { useState, useEffect } from 'react'
import { getSession } from '../api'
import type { AgentSession } from '../types'

const POLL_MS = 2000

export function useSession(id: string | null) {
  const [session, setSession] = useState<AgentSession | null>(null)

  useEffect(() => {
    if (!id) return

    let cancelled = false

    getSession(id)
      .then(data => { if (!cancelled) setSession(data) })
      .catch(() => null)

    const interval = setInterval(async () => {
      const data = await getSession(id).catch(() => null)
      if (!cancelled && data) {
        setSession(data)
        if (data.status !== 'running') clearInterval(interval)
      }
    }, POLL_MS)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [id])

  return {
    session: id ? session : null,
    loading: id !== null && session === null,
  }
}

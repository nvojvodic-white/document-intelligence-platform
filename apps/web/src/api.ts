import type {
  BrowseResult,
  Datasource,
  DevUser,
  Directory,
  IndexedFile,
  StreamFrame,
  SyncRun,
} from './types'

// In compose the UI and API are on different origins, so the base is explicit.
// Falls back to the vite proxy path for `npm run dev` against a local API.
const BASE = `${import.meta.env.VITE_API_BASE ?? ''}/api/v1`

// The token is the only thing that identifies the caller. The server reads the
// user from it and ignores anything else we might send, so there is no user id
// in any request body here.
let token: string | null = localStorage.getItem('token')

export function getToken(): string | null {
  return token
}

export function setToken(value: string | null): void {
  token = value
  if (value) localStorage.setItem('token', value)
  else localStorage.removeItem('token')
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra }
  if (token) h['Authorization'] = `Bearer ${token}`
  return h
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text()
    throw new Error(`${res.status}: ${detail.slice(0, 300)}`)
  }
  return res.json() as Promise<T>
}

// --- auth -------------------------------------------------------------------

export async function listDevUsers(): Promise<DevUser[]> {
  const res = await fetch(`${BASE}/auth/dev-users`)
  return (await json<{ users: DevUser[] }>(res)).users
}

export async function devLogin(userId: string): Promise<string> {
  const res = await fetch(`${BASE}/auth/dev-login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_id: userId }),
  })
  const data = await json<{ access_token: string }>(res)
  setToken(data.access_token)
  return data.access_token
}

export async function me(): Promise<DevUser> {
  return json<DevUser>(await fetch(`${BASE}/auth/me`, { headers: headers() }))
}

// --- datasources and directories --------------------------------------------

export async function listDatasources(): Promise<Datasource[]> {
  return json<Datasource[]>(await fetch(`${BASE}/datasources`, { headers: headers() }))
}

export async function connectS3(bucket: string, name: string): Promise<Datasource> {
  const res = await fetch(`${BASE}/datasources`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ bucket, name }),
  })
  return json<Datasource>(res)
}

export async function browse(datasourceId: string, path: string): Promise<BrowseResult> {
  const res = await fetch(
    `${BASE}/datasources/${datasourceId}/browse?path=${encodeURIComponent(path)}`,
    { headers: headers() },
  )
  return json<BrowseResult>(res)
}

export async function registerDirectory(
  datasourceId: string,
  path: string,
): Promise<{ directory: Directory; created: boolean }> {
  const res = await fetch(`${BASE}/directories`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ datasource_id: datasourceId, path }),
  })
  return json<{ directory: Directory; created: boolean }>(res)
}

export async function listDirectories(): Promise<Directory[]> {
  const res = await fetch(`${BASE}/directories`, { headers: headers() })
  return (await json<{ directories: Directory[] }>(res)).directories
}

export async function listFiles(directoryId: string): Promise<IndexedFile[]> {
  const res = await fetch(`${BASE}/directories/${directoryId}/files`, {
    headers: headers(),
  })
  return (await json<{ files: IndexedFile[] }>(res)).files
}

export async function triggerSync(
  directoryId: string,
): Promise<{ run: SyncRun; already_in_progress: boolean }> {
  const res = await fetch(`${BASE}/directories/${directoryId}/sync`, {
    method: 'POST',
    headers: headers(),
  })
  return json<{ run: SyncRun; already_in_progress: boolean }>(res)
}

export async function getRun(runId: string): Promise<SyncRun> {
  return json<SyncRun>(await fetch(`${BASE}/runs/${runId}`, { headers: headers() }))
}

export async function removeFile(fileId: string): Promise<{ vectors_dropped: number }> {
  const res = await fetch(`${BASE}/files/${fileId}`, {
    method: 'DELETE',
    headers: headers(),
  })
  return json<{ vectors_dropped: number }>(res)
}

// --- chat -------------------------------------------------------------------

/**
 * Stream an answer. Frames arrive as SSE; the caller gets each one as it lands.
 *
 * fetch + ReadableStream rather than EventSource because EventSource cannot
 * send an Authorization header, and identity here travels in the token.
 */
export async function streamChat(
  question: string,
  sessionId: string,
  onFrame: (frame: StreamFrame) => void,
): Promise<void> {
  const res = await fetch(`${BASE}/rag/agent_query_stream_v2`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ question, session_id: sessionId }),
  })
  if (!res.ok || !res.body) {
    throw new Error(`chat failed: ${res.status} ${await res.text()}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    // Frames are separated by a blank line. Keep the trailing partial in the
    // buffer - a chunk boundary can land mid-frame.
    const parts = buffer.split('\n\n')
    buffer = parts.pop() ?? ''
    for (const part of parts) {
      const line = part.trim()
      if (!line.startsWith('data:')) continue
      try {
        onFrame(JSON.parse(line.slice(5).trim()) as StreamFrame)
      } catch {
        // A frame we cannot parse is not worth killing the stream over.
      }
    }
  }
}

export interface DevUser {
  user_id: string
  email: string
}

export interface Datasource {
  id: string
  kind: string
  name: string
  config: { bucket: string; endpoint_url: string | null; region: string }
  secret_ref: string | null
  created_at: number
}

export interface SyncRun {
  id: string
  directory_id: string
  state: 'queued' | 'running' | 'succeeded' | 'partial' | 'failed'
  files_seen: number
  files_new: number
  files_skipped: number
  files_failed: number
  files_deleted: number
  chunks_embedded: number
  chunks_reused: number
  bytes_downloaded: number
  error: string | null
  started_at: number | null
  finished_at: number | null
}

export interface Directory {
  id: string
  datasource_id: string
  path: string
  status: string
  created_at: number
  latest_run?: SyncRun | null
  file_count?: number
}

export interface IndexedFile {
  id: string
  provider_key: string
  size: number | null
  sha256: string | null
  state: string
  error: string | null
}

export interface BrowseResult {
  path: string
  directories: string[]
  files: { key: string; size: number | null; mtime: number | null }[]
  truncated: boolean
}

/** A citation back to the source file, as this user names it. */
export interface Source {
  title: string
  url: string
  source: string
  snippet: string
  file_id: string
  chunk_id: string
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  route?: string
  grade?: string
  pending?: boolean
  /** Loaded from history rather than streamed. Only role and content are
   *  persisted, so a hydrated message has no citations to show. */
  hydrated?: boolean
}

/** Shape returned by GET /rag/sessions/{id}/turns. */
export interface StoredTurn {
  role: 'user' | 'assistant'
  content: string
  turn_index: number
  timestamp: number
}

/** SSE frames from /rag/agent_query_stream_v2. */
export type StreamFrame =
  | {
      type: 'metadata'
      route: string
      grade: string
      sources: Source[]
      retrieved_chunks: number
      trace: string[]
    }
  | { type: 'token'; content: string }
  | { type: 'answer_complete'; content: string }
  | { type: 'error'; message: string }
  | { type: 'done' }

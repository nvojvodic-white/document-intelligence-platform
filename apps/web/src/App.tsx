import { useCallback, useEffect, useRef, useState } from 'react'
import * as api from './api'
import type {
  ChatMessage,
  Datasource,
  DevUser,
  Directory,
  IndexedFile,
  Source,
} from './types'
import './App.css'

/**
 * The walkthrough, in one screen: log in, connect S3, browse, register a
 * directory, sync and watch the counters, ask a question, read the citations,
 * remove a file and see the answer change.
 *
 * Deliberately plain. UI polish is not scored, so this spends its complexity
 * budget on showing the things that are - sync counters read from the database,
 * dedup savings per run, and citations that name the source file.
 */
export default function App() {
  const [users, setUsers] = useState<DevUser[]>([])
  const [user, setUser] = useState<DevUser | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listDevUsers().then(setUsers).catch((e) => setError(String(e)))
    if (api.getToken()) api.me().then(setUser).catch(() => api.setToken(null))
  }, [])

  const login = async (userId: string) => {
    setError(null)
    try {
      await api.devLogin(userId)
      setUser(await api.me())
    } catch (e) {
      setError(String(e))
    }
  }

  const logout = () => {
    api.setToken(null)
    setUser(null)
  }

  return (
    <div className="app">
      <header>
        <h1>Document Intelligence</h1>
        {user ? (
          <div className="who">
            signed in as <strong>{user.user_id}</strong>
            <button onClick={logout}>switch user</button>
          </div>
        ) : (
          <div className="who">
            {users.map((u) => (
              <button key={u.user_id} onClick={() => login(u.user_id)}>
                log in as {u.user_id}
              </button>
            ))}
          </div>
        )}
      </header>

      {error && <p className="error">{error}</p>}

      {/* Keyed on the user so switching identity remounts everything. Reusing
          the mounted tree would leave the previous user's data on screen. */}
      {user ? (
        <Workspace key={user.user_id} userId={user.user_id} />
      ) : (
        <p>Pick a user to begin.</p>
      )}
    </div>
  )
}

function Workspace({ userId }: { userId: string }) {
  const [datasources, setDatasources] = useState<Datasource[]>([])
  const [directories, setDirectories] = useState<Directory[]>([])
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  // Callers ask for a reload by bumping a counter; the effect owns the fetch.
  // The cancelled flag matters on user switch - the outgoing tree unmounts
  // while its requests are still in flight, and without it a late response
  // would set state on a dead component with the previous user's data.
  const refresh = useCallback(() => setReloadKey((k) => k + 1), [])

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const [ds, dirs] = await Promise.all([
          api.listDatasources(),
          api.listDirectories(),
        ])
        if (cancelled) return
        setDatasources(ds)
        setDirectories(dirs)
        setError(null)
      } catch (e) {
        if (!cancelled) setError(String(e))
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [reloadKey])

  return (
    <div className="workspace">
      {error && <p className="error">{error}</p>}
      <section>
        <h2>1. Datasource</h2>
        <Datasources datasources={datasources} onChange={refresh} />
      </section>
      {datasources.length > 0 && (
        <section>
          <h2>2. Browse and register a directory</h2>
          <Browser datasource={datasources[0]} onRegistered={refresh} />
        </section>
      )}
      <section>
        <h2>3. Directories</h2>
        <Directories directories={directories} onChange={refresh} />
      </section>
      <section>
        <h2>4. Ask</h2>
        <Chat userId={userId} />
      </section>
    </div>
  )
}

function Datasources({
  datasources,
  onChange,
}: {
  datasources: Datasource[]
  onChange: () => void
}) {
  const [bucket, setBucket] = useState('tolkien-corpus')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const connect = async () => {
    setBusy(true)
    setError(null)
    try {
      await api.connectS3(bucket, 'S3')
      onChange()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      {datasources.map((d) => (
        <div key={d.id} className="row">
          <code>
            s3://{d.config.bucket} ({d.kind})
          </code>
        </div>
      ))}
      <div className="row">
        <input value={bucket} onChange={(e) => setBucket(e.target.value)} />
        <button onClick={connect} disabled={busy}>
          {busy ? 'checking…' : 'connect S3'}
        </button>
      </div>
      {/* The bucket is probed before the datasource is recorded, so a typo
          fails here rather than as a mystery sync failure later. */}
      {error && <p className="error">{error}</p>}
    </div>
  )
}

function Browser({
  datasource,
  onRegistered,
}: {
  datasource: Datasource
  onRegistered: () => void
}) {
  const [path, setPath] = useState('')
  const [dirs, setDirs] = useState<string[]>([])
  const [files, setFiles] = useState<{ key: string }[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .browse(datasource.id, path)
      .then((r) => {
        setDirs(r.directories)
        setFiles(r.files)
        setError(null)
      })
      .catch((e) => setError(String(e)))
  }, [datasource.id, path])

  const parent = path.replace(/[^/]+\/$/, '')

  return (
    <div>
      <div className="row">
        <code>/{path}</code>
        {path && <button onClick={() => setPath(parent)}>up</button>}
        {path && (
          <button
            onClick={async () => {
              await api.registerDirectory(datasource.id, path)
              onRegistered()
            }}
          >
            register this directory
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      <ul className="tree">
        {dirs.map((d) => (
          <li key={d}>
            <button className="link" onClick={() => setPath(d)}>
              📁 {d}
            </button>
          </li>
        ))}
        {files.map((f) => (
          <li key={f.key}>📄 {f.key.split('/').pop()}</li>
        ))}
      </ul>
    </div>
  )
}

function Directories({
  directories,
  onChange,
}: {
  directories: Directory[]
  onChange: () => void
}) {
  const [note, setNote] = useState<string | null>(null)
  // Poll while anything is in flight. Counters live in the database, so this
  // reads real progress rather than anything cached in the page.
  const active = directories.some(
    (d) => d.status === 'queued' || d.status === 'running',
  )

  useEffect(() => {
    if (!active) return
    const t = setInterval(onChange, 1000)
    return () => clearInterval(t)
  }, [active, onChange])

  const sync = async (directoryId: string) => {
    const res = await api.triggerSync(directoryId)
    setNote(
      res.already_in_progress
        ? 'already in progress — showing the run already in flight'
        : null,
    )
    onChange()
  }

  if (directories.length === 0) return <p>No directories registered yet.</p>

  return (
    <div>
      {note && <p className="note">{note}</p>}
      {directories.map((d) => (
        <DirectoryRow key={d.id} directory={d} onSync={sync} onChange={onChange} />
      ))}
    </div>
  )
}

function DirectoryRow({
  directory,
  onSync,
  onChange,
}: {
  directory: Directory
  onSync: (id: string) => void
  onChange: () => void
}) {
  const [files, setFiles] = useState<IndexedFile[] | null>(null)
  const run = directory.latest_run

  const toggle = async () => {
    setFiles(files ? null : await api.listFiles(directory.id))
  }

  return (
    <div className="card">
      <div className="row">
        <code>{directory.path}</code>
        <span className={`badge ${directory.status}`}>{directory.status}</span>
        <button onClick={() => onSync(directory.id)}>sync</button>
        <button onClick={toggle}>{files ? 'hide files' : `files (${directory.file_count ?? 0})`}</button>
      </div>
      {run && (
        <div className="counters">
          seen {run.files_seen} · new {run.files_new} · skipped {run.files_skipped} ·
          failed {run.files_failed} · deleted {run.files_deleted}
          {/* The dedup receipt: embeddings paid for versus served from cache. */}
          <span className="dedup">
            embedded {run.chunks_embedded} · reused {run.chunks_reused} chunks ·
            downloaded {run.bytes_downloaded} bytes
          </span>
          {run.error && <span className="error"> {run.error}</span>}
        </div>
      )}
      {files && (
        <ul className="files">
          {files.map((f) => (
            <li key={f.id}>
              <span>{f.provider_key}</span>
              <code className="sha">{f.sha256?.slice(0, 10)}</code>
              <button
                onClick={async () => {
                  await api.removeFile(f.id)
                  setFiles(await api.listFiles(directory.id))
                  onChange()
                }}
              >
                remove
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * A session id that survives switching users.
 *
 * Switching identity remounts the whole workspace (it is keyed on the user), so
 * a session id generated at mount time would be a new one every time and the
 * previous conversation would be unreachable - the turns are still on the
 * server, but nothing knows their id. Storing it per user makes returning to a
 * user return to their conversation.
 */
function sessionIdFor(userId: string): string {
  const key = `session:${userId}`
  let id = localStorage.getItem(key)
  if (!id) {
    id = `s-${Math.random().toString(36).slice(2)}`
    localStorage.setItem(key, id)
  }
  return id
}

function Chat({ userId }: { userId: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [hydrating, setHydrating] = useState(true)
  const sessionId = useRef(sessionIdFor(userId))

  // Load prior turns for this user. Only role and content are persisted, so
  // hydrated messages render without route/grade/citation badges - correct,
  // because those were never stored and inventing them after the fact would be
  // worse than omitting them.
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const turns = await api.fetchTurns(sessionId.current)
        if (cancelled) return
        setMessages(
          turns.map((t) => ({
            role: t.role,
            content: t.content,
            hydrated: true,
          })),
        )
      } catch {
        // An unreadable history is not worth blocking a new conversation over.
      } finally {
        if (!cancelled) setHydrating(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [])

  const reset = async () => {
    await api.clearSession(sessionId.current)
    localStorage.removeItem(`session:${userId}`)
    sessionId.current = sessionIdFor(userId)
    setMessages([])
  }

  const ask = async () => {
    const q = question.trim()
    if (!q || busy) return
    setQuestion('')
    setBusy(true)
    setMessages((m) => [
      ...m,
      { role: 'user', content: q },
      { role: 'assistant', content: '', pending: true },
    ])

    const patch = (fn: (m: ChatMessage) => ChatMessage) =>
      setMessages((prev) => {
        const next = [...prev]
        next[next.length - 1] = fn(next[next.length - 1])
        return next
      })

    try {
      await api.streamChat(q, sessionId.current, (frame) => {
        if (frame.type === 'metadata') {
          patch((m) => ({
            ...m,
            sources: frame.sources,
            route: frame.route,
            grade: frame.grade,
          }))
        } else if (frame.type === 'token') {
          patch((m) => ({ ...m, content: m.content + frame.content }))
        } else if (frame.type === 'error') {
          patch((m) => ({ ...m, content: `error: ${frame.message}` }))
        } else if (frame.type === 'done') {
          patch((m) => ({ ...m, pending: false }))
        }
      })
    } catch (e) {
      patch((m) => ({ ...m, content: String(e), pending: false }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="chat">
        {hydrating && <div className="note">loading conversation…</div>}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div>{m.content || (m.pending ? '…' : '')}</div>
            {m.route && (
              <div className="meta">
                route {m.route} · retrieval graded {m.grade}
              </div>
            )}
            {m.hydrated && m.role === 'assistant' && (
              <div className="meta">from history · citations not stored per turn</div>
            )}
            {m.sources && m.sources.length > 0 && <Citations sources={m.sources} />}
          </div>
        ))}
      </div>
      <div className="row">
        <input
          value={question}
          placeholder="Ask about your documents…"
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && ask()}
        />
        <button onClick={ask} disabled={busy}>
          ask
        </button>
        {messages.length > 0 && (
          <button onClick={reset} disabled={busy}>
            new conversation
          </button>
        )}
      </div>
    </div>
  )
}

/** Citations name the source file, which is what the user recognises. */
function Citations({ sources }: { sources: Source[] }) {
  return (
    <ol className="sources">
      {sources.map((s, i) => (
        <li key={s.chunk_id || i}>
          <strong>{s.title}</strong> <code>{s.source}</code>
          <div className="snippet">{s.snippet}</div>
        </li>
      ))}
    </ol>
  )
}

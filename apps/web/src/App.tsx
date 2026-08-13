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

/** Connect a datasource, sync a directory, ask questions over what you synced. */
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

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" />
          Document Intelligence
          <span className="brand-sub">/ multi-tenant RAG</span>
        </div>
        <div className="switcher">
          {users.map((u) => (
            <button
              key={u.user_id}
              aria-current={user?.user_id === u.user_id}
              onClick={() => login(u.user_id)}
            >
              <span className="avatar">{u.user_id[0]}</span>
              {u.user_id}
            </button>
          ))}
        </div>
      </header>

      {!user && (
        <>
          <div className="hero">
            <h1>
              Your documents,
              <br />
              answerable.
            </h1>
            <p>
              Connect storage, sync a directory into your own knowledge base, and
              ask questions answered only from what you indexed.
            </p>
          </div>
          <div className="gate">
            {error && <div className="error">{error}</div>}
            Pick a user above to begin.
          </div>
        </>
      )}

      {/* Keyed on the user: switching identity remounts everything rather than
          leaving the previous user's data on screen. */}
      {user && <Workspace key={user.user_id} userId={user.user_id} />}
    </div>
  )
}

function Workspace({ userId }: { userId: string }) {
  const [datasources, setDatasources] = useState<Datasource[]>([])
  const [directories, setDirectories] = useState<Directory[]>([])
  const [error, setError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  const refresh = useCallback(() => setReloadKey((k) => k + 1), [])

  useEffect(() => {
    // cancelled guards against a late response repainting after a user switch.
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
    <main className="workspace">
      <div className="col">
        {error && <div className="error">{error}</div>}

        <Panel step={1} title="Datasource" hint={`signed in as ${userId}`}>
          <Datasources datasources={datasources} onChange={refresh} />
        </Panel>

        {datasources.length > 0 && (
          <Panel step={2} title="Browse and register">
            <Browser datasource={datasources[0]} onRegistered={refresh} />
          </Panel>
        )}

        <Panel step={3} title="Directories" hint={`${directories.length} registered`}>
          <Directories directories={directories} onChange={refresh} />
        </Panel>
      </div>

      <div className="col col-side">
        <Chat userId={userId} />
      </div>
    </main>
  )
}

function Panel({
  step,
  title,
  hint,
  children,
}: {
  step: number
  title: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <section className="panel">
      <div className="panel-head">
        <span className="step">{step}</span>
        <h2>{title}</h2>
        {hint && <span className="hint">{hint}</span>}
      </div>
      <div className="panel-body">{children}</div>
    </section>
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
        <div key={d.id} className="item">
          <span className="glyph">◈</span>
          <span className="grow">
            s3://{d.config.bucket}
          </span>
          <span className="pill succeeded">connected</span>
        </div>
      ))}
      <div className="row" style={{ marginTop: datasources.length ? '0.6rem' : 0 }}>
        <input
          value={bucket}
          onChange={(e) => setBucket(e.target.value)}
          placeholder="bucket name"
        />
        <button className="primary" onClick={connect} disabled={busy}>
          {busy ? 'Checking…' : 'Connect S3'}
        </button>
      </div>
      {/* The bucket is probed before it is recorded, so a typo fails here. */}
      {error && <div className="error" style={{ marginTop: '0.6rem' }}>{error}</div>}
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
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .browse(datasource.id, path)
      .then((r) => {
        if (cancelled) return
        setDirs(r.directories)
        setFiles(r.files)
        setError(null)
      })
      .catch((e) => !cancelled && setError(String(e)))
    return () => {
      cancelled = true
    }
  }, [datasource.id, path])

  const register = async () => {
    setBusy(true)
    try {
      await api.registerDirectory(datasource.id, path)
      onRegistered()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div>
      <div className="row">
        <span className="crumb">/{path}</span>
        <span style={{ flex: 1 }} />
        {path && (
          <button className="ghost" onClick={() => setPath(path.replace(/[^/]+\/$/, ''))}>
            ↑ Up
          </button>
        )}
        {path && (
          <button className="primary" onClick={register} disabled={busy}>
            Register
          </button>
        )}
      </div>

      {error && <div className="error" style={{ marginTop: '0.6rem' }}>{error}</div>}

      <ul className="tree">
        {dirs.map((d) => (
          <li key={d} className="item">
            <span className="glyph">▸</span>
            <button className="link grow" onClick={() => setPath(d)}>
              {d.replace(path, '') || d}
            </button>
          </li>
        ))}
        {files.map((f) => (
          <li key={f.key} className="item">
            <span className="glyph">·</span>
            <span className="grow">{f.key.split('/').pop()}</span>
          </li>
        ))}
        {dirs.length === 0 && files.length === 0 && (
          <li className="empty">Nothing here.</li>
        )}
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

  // Counters live in the database, so polling shows real progress mid-run.
  const active = directories.some((d) => d.status === 'queued' || d.status === 'running')
  useEffect(() => {
    if (!active) return
    const t = setInterval(onChange, 1000)
    return () => clearInterval(t)
  }, [active, onChange])

  const sync = async (id: string) => {
    const res = await api.triggerSync(id)
    setNote(res.already_in_progress ? 'Already in progress — showing the run in flight.' : null)
    onChange()
  }

  if (directories.length === 0) {
    return <div className="empty">No directories yet. Register one above.</div>
  }

  return (
    <div>
      {note && <div className="note">{note}</div>}
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

  const toggle = async () => setFiles(files ? null : await api.listFiles(directory.id))

  return (
    <div className="dir">
      <div className="dir-head">
        <span className="path">{directory.path}</span>
        <span className={`pill ${directory.status}`}>{directory.status}</span>
        <button onClick={() => onSync(directory.id)}>Sync</button>
        <button className="ghost" onClick={toggle}>
          {files ? 'Hide' : `Files (${directory.file_count ?? 0})`}
        </button>
      </div>

      {run && (
        <div className="counters">
          <span>seen <b>{run.files_seen}</b></span>
          <span>new <b>{run.files_new}</b></span>
          <span>skipped <b>{run.files_skipped}</b></span>
          {run.files_failed > 0 && <span>failed <b>{run.files_failed}</b></span>}
          {run.files_deleted > 0 && <span>deleted <b>{run.files_deleted}</b></span>}
          <span>embedded <b>{run.chunks_embedded.toLocaleString()}</b></span>
          <span className="win">reused <b>{run.chunks_reused.toLocaleString()}</b></span>
          {run.error && <span style={{ color: 'var(--red)' }}>{run.error}</span>}
        </div>
      )}

      {files && (
        <ul className="files">
          {files.map((f) => (
            <li key={f.id} className="item">
              <span className="glyph">·</span>
              <span className="grow">{f.provider_key}</span>
              <span className="fade">{f.sha256?.slice(0, 8)}</span>
              <button
                className="ghost"
                onClick={async () => {
                  await api.removeFile(f.id)
                  setFiles(await api.listFiles(directory.id))
                  onChange()
                }}
              >
                Remove
              </button>
            </li>
          ))}
          {files.length === 0 && <li className="empty">No files indexed.</li>}
        </ul>
      )}
    </div>
  )
}

/** Session id kept per user so switching away and back returns to the same chat. */
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
  const sessionId = useRef(sessionIdFor(userId))
  const scroller = useRef<HTMLDivElement>(null)

  // Only role and content are persisted, so hydrated turns have no citations.
  useEffect(() => {
    let cancelled = false
    api
      .fetchTurns(sessionId.current)
      .then((turns) => {
        if (cancelled) return
        setMessages(turns.map((t) => ({ role: t.role, content: t.content, hydrated: true })))
      })
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

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
          patch((m) => ({ ...m, sources: frame.sources, route: frame.route, grade: frame.grade }))
        } else if (frame.type === 'token') {
          patch((m) => ({ ...m, content: m.content + frame.content }))
        } else if (frame.type === 'error') {
          patch((m) => ({ ...m, content: `Error: ${frame.message}` }))
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
    <section className="panel chat-panel">
      <div className="panel-head">
        <span className="step">4</span>
        <h2>Ask</h2>
        {messages.length > 0 && (
          <button className="ghost hint" onClick={reset} disabled={busy}>
            New chat
          </button>
        )}
      </div>

      <div className="chat" ref={scroller}>
        {messages.length === 0 && (
          <div className="empty">
            Answers come only from documents you have synced, with citations back
            to the source file.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="bubble">
              {m.content || (m.pending && (
                <span className="typing">
                  <span /><span /><span />
                </span>
              ))}
            </div>
            {m.route && (
              <div className="meta">
                route {m.route} · graded {m.grade}
              </div>
            )}
            {m.hydrated && m.role === 'assistant' && (
              <div className="meta">from history · citations not stored per turn</div>
            )}
            {m.sources && m.sources.length > 0 && <Citations sources={m.sources} />}
          </div>
        ))}
      </div>

      <div className="composer">
        <input
          value={question}
          placeholder="Ask about your documents…"
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && ask()}
        />
        <button className="primary" onClick={ask} disabled={busy || !question.trim()}>
          Ask
        </button>
      </div>
    </section>
  )
}

function Citations({ sources }: { sources: Source[] }) {
  return (
    <ol className="sources">
      {sources.map((s, i) => (
        <li key={s.chunk_id || i} className="source">
          <div className="name">
            <span>{s.title}</span>
            <span className="key">{s.source}</span>
          </div>
          <div className="snippet">{s.snippet}</div>
        </li>
      ))}
    </ol>
  )
}

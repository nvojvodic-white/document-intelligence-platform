import { useSession } from '../hooks/useSession'
import { StatusBadge } from './StatusBadge'
import type { ToolCall } from '../types'

interface Props {
  sessionId: string | null
}

// Render tool input: string values shown as-is (preserving newlines),
// other values JSON-stringified. Avoids double-escaping \n in code strings.
function ToolInput({ input }: { input: Record<string, unknown> }) {
  const entries = Object.entries(input)
  if (entries.length === 1 && typeof entries[0][1] === 'string') {
    // Single string value (e.g. code) — show raw
    return <pre className="tool-call-pre">{entries[0][1] as string}</pre>
  }
  return (
    <div className="tool-input-entries">
      {entries.map(([key, val]) => (
        <div key={key} className="tool-input-entry">
          <span className="tool-input-key">{key}</span>
          <pre className="tool-call-pre">
            {typeof val === 'string' ? val : JSON.stringify(val, null, 2)}
          </pre>
        </div>
      ))}
    </div>
  )
}

// Minimal markdown renderer: bold, inline code, numbered lists, line breaks
function Markdown({ text }: { text: string }) {
  const lines = text.split('\n')
  const elements: React.ReactNode[] = []

  let i = 0
  while (i < lines.length) {
    const line = lines[i]

    // Numbered list item
    if (/^\d+\.\s/.test(line)) {
      const listItems: string[] = []
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) {
        listItems.push(lines[i].replace(/^\d+\.\s/, ''))
        i++
      }
      elements.push(
        <ol key={i} className="md-list">
          {listItems.map((item, j) => (
            <li key={j}>{renderInline(item)}</li>
          ))}
        </ol>
      )
      continue
    }

    // Empty line → spacer
    if (line.trim() === '') {
      elements.push(<div key={i} className="md-spacer" />)
    } else {
      elements.push(<p key={i} className="md-p">{renderInline(line)}</p>)
    }
    i++
  }

  return <div className="md-body">{elements}</div>
}

function renderInline(text: string): React.ReactNode[] {
  // Split on **bold** and `code` patterns
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/)
  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={i}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return <code key={i}>{part.slice(1, -1)}</code>
    }
    return part
  })
}

function ToolCallCard({ tc, index }: { tc: ToolCall; index: number }) {
  return (
    <div className="tool-call">
      <div className="tool-call-header">
        <span className="tool-call-index">{index + 1}</span>
        <span className="tool-call-name">⚙ {tc.tool}</span>
      </div>
      <div className="tool-call-body">
        <div className="tool-call-section-label">Input</div>
        <ToolInput input={tc.input} />
      </div>
      {tc.result && (
        <div className="tool-call-result">
          <div className="tool-call-section-label">Output</div>
          <pre className="tool-call-pre tool-call-pre--output">{tc.result}</pre>
        </div>
      )}
    </div>
  )
}

function EmptyState() {
  return (
    <div className="detail-empty">
      <div className="empty-state">
        <div className="empty-state-icon">
          <svg width="48" height="48" viewBox="0 0 48 48" fill="none">
            <rect x="6" y="10" width="36" height="28" rx="4" stroke="currentColor" strokeWidth="2" strokeDasharray="4 3"/>
            <circle cx="24" cy="24" r="6" stroke="currentColor" strokeWidth="2"/>
            <path d="M24 18v-4M24 34v-4M18 24h-4M34 24h-4" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
          </svg>
        </div>
        <h3>No session selected</h3>
        <p>Create a new session or select one from the list to see tool calls, results, and timing.</p>
      </div>
    </div>
  )
}

function RunningIndicator({ startedAt }: { startedAt: string }) {
  return (
    <div className="running-banner">
      <span className="running-dots">
        <span /><span /><span />
      </span>
      <span>Agent is working</span>
      <span className="running-elapsed">
        started {new Date(startedAt).toLocaleTimeString()}
      </span>
    </div>
  )
}

export function SessionDetail({ sessionId }: Props) {
  const { session, loading } = useSession(sessionId)

  if (!sessionId) return <EmptyState />

  if (loading && !session) {
    return <div className="detail-empty"><p style={{ color: 'var(--text-dim)' }}>Loading…</p></div>
  }

  if (!session) {
    return <div className="detail-empty"><p style={{ color: 'var(--text-dim)' }}>Session not found</p></div>
  }

  const durationMs = session.completed_at
    ? new Date(session.completed_at).getTime() - new Date(session.created_at).getTime()
    : null

  return (
    <div className="session-detail">
      {session.status === 'running' && (
        <RunningIndicator startedAt={session.created_at} />
      )}

      <div className="detail-header">
        <StatusBadge status={session.status} />
        {durationMs !== null && (
          <span className="detail-duration">{(durationMs / 1000).toFixed(1)}s</span>
        )}
      </div>

      <h2 className="detail-task">{session.task}</h2>

      <p className="detail-meta">
        ID: <code>{session.session_id}</code> · {new Date(session.created_at).toLocaleString()}
      </p>

      {session.tool_calls.length > 0 ? (
        <section className="detail-section">
          <h3>Tool Calls <span className="section-count">{session.tool_calls.length}</span></h3>
          {session.tool_calls.map((tc, i) => (
            <ToolCallCard key={i} tc={tc} index={i} />
          ))}
        </section>
      ) : session.status !== 'running' && (
        <section className="detail-section">
          <h3>Tool Calls</h3>
          <p className="no-tool-calls">No tool calls were made for this session.</p>
        </section>
      )}

      {session.result && (
        <section className="detail-section">
          <h3>Result</h3>
          <div className="result-box">
            <Markdown text={session.result} />
          </div>
        </section>
      )}
    </div>
  )
}

import type { AgentSession } from '../types'
import { StatusBadge } from './StatusBadge'
import { deleteSession } from '../api'

interface Props {
  sessions: AgentSession[]
  selectedId: string | null
  onSelect: (id: string) => void
  onDeleted: () => void
}

function duration(session: AgentSession): string {
  const end = session.completed_at ? new Date(session.completed_at) : new Date()
  const secs = Math.round((end.getTime() - new Date(session.created_at).getTime()) / 1000)
  return secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m ${secs % 60}s`
}

export function SessionList({ sessions, selectedId, onSelect, onDeleted }: Props) {
  if (sessions.length === 0) {
    return <p className="empty">No sessions yet. Run your first agent above.</p>
  }

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.stopPropagation()
    await deleteSession(id)
    onDeleted()
  }

  return (
    <ul className="session-list">
      {sessions.map(s => (
        <li
          key={s.session_id}
          className={`session-item ${s.session_id === selectedId ? 'selected' : ''}`}
          onClick={() => onSelect(s.session_id)}
        >
          <div className="session-item-header">
            <StatusBadge status={s.status} />
            <span className="session-duration">{duration(s)}</span>
            <button
              className="btn-icon"
              title="Delete session"
              onClick={e => handleDelete(e, s.session_id)}
            >✕</button>
          </div>
          <p className="session-task">{s.task}</p>
          <p className="session-meta">
            {s.tool_calls.length} tool call{s.tool_calls.length !== 1 ? 's' : ''} ·{' '}
            {new Date(s.created_at).toLocaleTimeString()}
          </p>
        </li>
      ))}
    </ul>
  )
}

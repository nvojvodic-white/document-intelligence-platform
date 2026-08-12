import { useState } from 'react'
import { createSession } from '../api'

interface Props {
  onCreated: (id: string) => void
}

export function NewSessionForm({ onCreated }: Props) {
  const [task, setTask] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!task.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      const { session_id } = await createSession(task.trim())
      setTask('')
      onCreated(session_id)
    } catch {
      setError('Failed to create session')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form className="new-session-form" onSubmit={handleSubmit}>
      <textarea
        className="task-input"
        placeholder="Describe the task for the agent…"
        value={task}
        onChange={e => setTask(e.target.value)}
        rows={3}
        disabled={submitting}
        onKeyDown={e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) handleSubmit(e) }}
      />
      {error && <p className="form-error">{error}</p>}
      <button className="btn btn-primary" type="submit" disabled={submitting || !task.trim()}>
        {submitting ? 'Starting…' : 'Run Agent'}
      </button>
      <span className="hint">⌘ + Enter to submit</span>
    </form>
  )
}

import type { AgentSession } from '../types'

interface Props {
  status: AgentSession['status']
}

export function StatusBadge({ status }: Props) {
  const map = {
    running: { label: 'Running', cls: 'badge badge-running' },
    completed: { label: 'Completed', cls: 'badge badge-completed' },
    failed: { label: 'Failed', cls: 'badge badge-failed' },
  }
  const { label, cls } = map[status]
  return <span className={cls}>{status === 'running' && <span className="pulse" />}{label}</span>
}

import { useState } from 'react'
import { useSessions } from './hooks/useSessions'
import { NewSessionForm } from './components/NewSessionForm'
import { SessionList } from './components/SessionList'
import { SessionDetail } from './components/SessionDetail'
import { ChatView } from './components/ChatView'
import './App.css'

type View = 'chat' | 'sessions'

export default function App() {
  const { sessions, loading, error, refresh } = useSessions()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [view, setView] = useState<View>('chat')

  function handleCreated(id: string) {
    refresh()
    setSelectedId(id)
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-inner">
          <h1>Agent Platform</h1>
          <nav className="view-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={view === 'chat'}
              className={`view-tab ${view === 'chat' ? 'active' : ''}`}
              onClick={() => setView('chat')}
            >
              Chat
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === 'sessions'}
              className={`view-tab ${view === 'sessions' ? 'active' : ''}`}
              onClick={() => setView('sessions')}
            >
              Agent sessions
            </button>
          </nav>
          <span className="header-sub">Powered by Claude</span>
        </div>
      </header>

      {view === 'chat' ? (
        <main className="app-main chat-main">
          <ChatView />
        </main>
      ) : (
        <main className="app-main">
          <aside className="sidebar">
            <NewSessionForm onCreated={handleCreated} />

            <div className="sidebar-sessions">
              <div className="sidebar-heading">
                <span>Sessions</span>
                {loading && <span className="spinner" />}
                {error && <span className="error-dot" title={error} />}
              </div>
              <SessionList
                sessions={sessions}
                selectedId={selectedId}
                onSelect={setSelectedId}
                onDeleted={refresh}
              />
            </div>
          </aside>

          <section className="detail-pane">
            <SessionDetail sessionId={selectedId} />
          </section>
        </main>
      )}
    </div>
  )
}

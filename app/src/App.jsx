import { useEffect, useState } from 'react'
import { configured } from './lib/supabase.js'
import { fetchLatestDigest, loadCachedDigest } from './lib/data.js'
import DigestView from './components/DigestView.jsx'
import ArchiveView from './components/ArchiveView.jsx'
import ProfileView from './components/ProfileView.jsx'

const TABS = [
  { key: 'today', label: 'Today', icon: '☀' },
  { key: 'archive', label: 'Archive', icon: '☰' },
  { key: 'profile', label: 'Profile', icon: '◎' },
]

export default function App() {
  const [tab, setTab] = useState('today')
  const [payload, setPayload] = useState(null)
  const [offline, setOffline] = useState(false)
  const [status, setStatus] = useState('loading')

  useEffect(() => {
    if (!configured) {
      setStatus('unconfigured')
      return
    }
    fetchLatestDigest()
      .then((p) => {
        if (p) {
          setPayload(p)
          setStatus('ready')
        } else {
          setStatus('empty')
        }
      })
      .catch(() => {
        const cached = loadCachedDigest()
        if (cached) {
          setPayload(cached)
          setOffline(true)
          setStatus('ready')
        } else {
          setStatus('error')
        }
      })
  }, [])

  return (
    <div className="app">
      <main className="content">
        {tab === 'today' && (
          <>
            {status === 'loading' && <p className="status">Loading today's digest…</p>}
            {status === 'unconfigured' && (
              <p className="status">
                Not configured. Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY (see README).
              </p>
            )}
            {status === 'empty' && (
              <p className="status">No digest yet. Run the pipeline, then refresh.</p>
            )}
            {status === 'error' && (
              <p className="status">Couldn't reach Supabase and no cached digest is available.</p>
            )}
            {status === 'ready' && payload && <DigestView payload={payload} offline={offline} />}
          </>
        )}
        {tab === 'archive' &&
          (configured ? <ArchiveView /> : <p className="status">Not configured.</p>)}
        {tab === 'profile' &&
          (configured ? <ProfileView /> : <p className="status">Not configured.</p>)}
      </main>
      <nav className="tabbar">
        {TABS.map(({ key, label, icon }) => (
          <button
            key={key}
            className={tab === key ? 'tab active' : 'tab'}
            onClick={() => setTab(key)}
          >
            <span className="tab-icon">{icon}</span>
            {label}
          </button>
        ))}
      </nav>
    </div>
  )
}

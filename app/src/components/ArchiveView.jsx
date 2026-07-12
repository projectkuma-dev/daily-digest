import { useEffect, useState } from 'react'
import { fetchArchive, fetchDigestById } from '../lib/data.js'
import DigestView from './DigestView.jsx'

export default function ArchiveView() {
  const [list, setList] = useState(null)
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchArchive().then(setList).catch((e) => setError(e.message))
  }, [])

  async function open(id) {
    try {
      setSelected(await fetchDigestById(id))
    } catch (e) {
      setError(e.message)
    }
  }

  if (error) return <p className="status">Could not load archive: {error}</p>
  if (selected) {
    return (
      <div>
        <button className="back-btn" onClick={() => setSelected(null)}>
          ← All digests
        </button>
        <DigestView payload={selected} />
      </div>
    )
  }
  if (!list) return <p className="status">Loading…</p>
  if (!list.length) return <p className="status">No digests yet.</p>

  return (
    <div className="archive">
      <h2 className="section-title">Past digests</h2>
      {list.map((d) => (
        <button key={d.id} className="archive-row" onClick={() => open(d.id)}>
          <span className="archive-date">
            {new Date(`${d.digest_date}T12:00:00`).toLocaleDateString(undefined, {
              weekday: 'short',
              month: 'short',
              day: 'numeric',
            })}
          </span>
          <span className="archive-line">{d.bottom_line}</span>
        </button>
      ))}
    </div>
  )
}

import { useEffect, useState } from 'react'
import { fetchProfile, fetchTagStats } from '../lib/data.js'

export default function ProfileView() {
  const [profile, setProfile] = useState(null)
  const [stats, setStats] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    fetchProfile().then(setProfile).catch((e) => setError(e.message))
    fetchTagStats().then(setStats).catch(() => setStats([]))
  }, [])

  if (error) return <p className="status">Could not load profile: {error}</p>
  if (!profile) return <p className="status">Loading…</p>

  return (
    <div className="profile">
      <h2 className="section-title">Interest profile</h2>
      <p className="profile-text">{profile.profile_text}</p>
      <p className="profile-updated">
        Last updated {new Date(profile.updated_at).toLocaleDateString()}. Rewritten automatically
        every Sunday from your swipes.
      </p>

      <h2 className="section-title">What the system has learned</h2>
      {!stats ? (
        <p className="status">Loading…</p>
      ) : !stats.length ? (
        <p className="status">No feedback yet. Swipe cards on the Today view to teach it.</p>
      ) : (
        <table className="stats-table">
          <thead>
            <tr>
              <th>Tag</th>
              <th>Relevant</th>
              <th>Not relevant</th>
            </tr>
          </thead>
          <tbody>
            {stats.slice(0, 25).map((s) => (
              <tr key={s.tag}>
                <td>{s.tag}</td>
                <td className="num pos">{s.relevant}</td>
                <td className="num neg">{s.not_relevant}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

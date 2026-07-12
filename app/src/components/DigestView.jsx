import Card from './Card.jsx'

const SECTIONS = [
  { key: 'news', label: 'News' },
  { key: 'weather', label: 'Weather' },
  { key: 'finance', label: 'Finance' },
]

export default function DigestView({ payload, offline }) {
  const { digest, items, verdicts } = payload
  const date = new Date(`${digest.digest_date}T12:00:00`)
  const dateLabel = date.toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  })

  return (
    <div className="digest">
      <header className="digest-header">
        <h2>{dateLabel}</h2>
        {offline && <p className="offline-note">Offline: showing last loaded digest</p>}
        {digest.bottom_line && <p className="bottom-line">{digest.bottom_line}</p>}
        <p className="swipe-hint">Swipe right = relevant, left = not relevant. Tap for detail.</p>
      </header>
      {SECTIONS.map(({ key, label }) => {
        const sectionItems = items.filter((i) => i.section === key)
        if (!sectionItems.length) return null
        return (
          <section key={key}>
            <h2 className="section-title">{label}</h2>
            {sectionItems.map((item) => (
              <Card key={item.id} item={item} verdict={verdicts[item.id]} />
            ))}
          </section>
        )
      })}
    </div>
  )
}

import { useState } from 'react'
import { useSwipe } from '../lib/useSwipe.js'
import { saveFeedback } from '../lib/data.js'

export default function Card({ item, verdict: initialVerdict }) {
  const [expanded, setExpanded] = useState(false)
  const [verdict, setVerdict] = useState(initialVerdict ?? null)
  const [flash, setFlash] = useState(null)

  async function submit(v) {
    setVerdict(v)
    setFlash(v)
    setTimeout(() => setFlash(null), 600)
    try {
      await saveFeedback(item.id, v)
    } catch (err) {
      console.error('Feedback save failed:', err)
    }
  }

  const { dx, swiping, handlers } = useSwipe({
    onSwipeRight: () => submit('relevant'),
    onSwipeLeft: () => submit('not_relevant'),
  })

  const classes = ['card']
  if (verdict === 'not_relevant') classes.push('card-dimmed')
  if (flash === 'relevant') classes.push('card-flash-green')
  if (flash === 'not_relevant') classes.push('card-flash-gray')

  return (
    <article
      className={classes.join(' ')}
      style={{
        transform: swiping ? `translateX(${Math.max(-90, Math.min(90, dx))}px)` : undefined,
        transition: swiping ? 'none' : 'transform 0.2s ease',
      }}
      {...handlers}
      onClick={() => {
        if (!swiping) setExpanded((v) => !v)
      }}
    >
      <div className="card-head">
        <h3>{item.headline}</h3>
        {verdict === 'relevant' && <span className="check" aria-label="Marked relevant">✓</span>}
      </div>
      <p className="summary">{item.summary}</p>
      {expanded && (
        <div className="detail">
          {item.detail && <p>{item.detail}</p>}
          {Array.isArray(item.sources) && item.sources.length > 0 && (
            <ul className="sources">
              {item.sources.map((s, i) => (
                <li key={i}>
                  <a href={s.url} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>
                    {s.title || s.url}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="tags">
        {(item.tags ?? []).map((t) => (
          <span key={t} className="tag">
            {t}
          </span>
        ))}
      </div>
    </article>
  )
}

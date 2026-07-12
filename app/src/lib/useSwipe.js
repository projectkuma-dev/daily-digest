import { useRef, useState } from 'react'

const TRIGGER_PX = 60 // horizontal distance required to register a swipe
const LOCK_PX = 12 // movement before we decide the gesture's axis

/**
 * Horizontal swipe detection via pointer events. The gesture only engages
 * when movement is horizontal-dominant, so vertical scrolling never triggers
 * it (pair with `touch-action: pan-y` on the element).
 */
export function useSwipe({ onSwipeRight, onSwipeLeft }) {
  const gesture = useRef(null)
  const [dx, setDx] = useState(0)

  function onPointerDown(e) {
    if (e.pointerType === 'mouse' && e.button !== 0) return
    gesture.current = { x: e.clientX, y: e.clientY, axis: null, id: e.pointerId }
  }

  function onPointerMove(e) {
    const g = gesture.current
    if (!g || g.id !== e.pointerId) return
    const deltaX = e.clientX - g.x
    const deltaY = e.clientY - g.y

    if (g.axis === null) {
      if (Math.abs(deltaX) < LOCK_PX && Math.abs(deltaY) < LOCK_PX) return
      g.axis = Math.abs(deltaX) > Math.abs(deltaY) ? 'x' : 'y'
    }
    if (g.axis !== 'x') return
    e.currentTarget.setPointerCapture?.(e.pointerId)
    setDx(deltaX)
  }

  function finish(e) {
    const g = gesture.current
    if (!g || g.id !== e.pointerId) return
    const deltaX = e.clientX - g.x
    if (g.axis === 'x') {
      if (deltaX >= TRIGGER_PX) onSwipeRight?.()
      else if (deltaX <= -TRIGGER_PX) onSwipeLeft?.()
    }
    gesture.current = null
    setDx(0)
  }

  function onPointerCancel() {
    gesture.current = null
    setDx(0)
  }

  return {
    dx,
    swiping: gesture.current?.axis === 'x',
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: finish,
      onPointerCancel,
    },
  }
}

import { createClient } from '@supabase/supabase-js'

// Normalize whatever URL form the build secret carried: bare project URL,
// trailing slash, /rest/v1 endpoint URL, or a dashboard URL. Mirrors the
// same normalization in pipeline/digest.py (supabase_client).
function normalizeUrl(raw) {
  if (!raw) return raw
  try {
    const u = new URL(raw.includes('://') ? raw.trim() : `https://${raw.trim()}`)
    let host = u.host
    if (host.endsWith('supabase.com') && u.pathname.includes('/project/')) {
      const ref = u.pathname.split('/project/')[1].split('/')[0]
      host = `${ref}.supabase.co`
    }
    return `https://${host}`
  } catch {
    return raw
  }
}

const url = normalizeUrl(import.meta.env.VITE_SUPABASE_URL)
const anonKey = (import.meta.env.VITE_SUPABASE_ANON_KEY || '').trim()

export const configured = Boolean(url && anonKey)
export const supabase = configured ? createClient(url, anonKey) : null

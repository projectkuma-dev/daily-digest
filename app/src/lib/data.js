import { supabase } from './supabase.js'

const CACHE_KEY = 'daily-digest:last'

function cacheDigest(payload) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(payload))
  } catch {
    /* storage full or unavailable; offline cache is best-effort */
  }
}

export function loadCachedDigest() {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

async function fetchItemsAndFeedback(digest) {
  const { data: items, error: itemsError } = await supabase
    .from('digest_items')
    .select('*')
    .eq('digest_id', digest.id)
    .order('section')
    .order('position')
  if (itemsError) throw itemsError

  const ids = items.map((i) => i.id)
  let verdicts = {}
  if (ids.length) {
    const { data: fb } = await supabase.from('feedback').select('item_id, verdict').in('item_id', ids)
    for (const row of fb ?? []) verdicts[row.item_id] = row.verdict
  }
  return { digest, items, verdicts }
}

/** Latest digest with items and any existing feedback verdicts. */
export async function fetchLatestDigest() {
  const { data, error } = await supabase
    .from('digests')
    .select('*')
    .order('digest_date', { ascending: false })
    .limit(1)
  if (error) throw error
  if (!data?.length) return null
  const payload = await fetchItemsAndFeedback(data[0])
  cacheDigest(payload)
  return payload
}

export async function fetchDigestById(id) {
  const { data, error } = await supabase.from('digests').select('*').eq('id', id).single()
  if (error) throw error
  return fetchItemsAndFeedback(data)
}

/** Past digest dates for the archive list. */
export async function fetchArchive() {
  const { data, error } = await supabase
    .from('digests')
    .select('id, digest_date, bottom_line')
    .order('digest_date', { ascending: false })
    .limit(60)
  if (error) throw error
  return data ?? []
}

/** Insert or overwrite the verdict for an item (one verdict per item). */
export async function saveFeedback(itemId, verdict) {
  const { error } = await supabase
    .from('feedback')
    .upsert({ item_id: itemId, verdict }, { onConflict: 'item_id' })
  if (error) throw error
}

export async function fetchProfile() {
  const { data, error } = await supabase
    .from('interest_profile')
    .select('profile_text, updated_at')
    .eq('id', 1)
    .single()
  if (error) throw error
  return data
}

/** Tag-level relevant/not-relevant counts across all feedback. */
export async function fetchTagStats() {
  const { data, error } = await supabase.from('feedback').select('verdict, digest_items(tags)')
  if (error) throw error
  const counts = {}
  for (const row of data ?? []) {
    for (const tag of row.digest_items?.tags ?? []) {
      counts[tag] ??= { relevant: 0, not_relevant: 0 }
      counts[tag][row.verdict] += 1
    }
  }
  return Object.entries(counts)
    .map(([tag, c]) => ({ tag, ...c, total: c.relevant + c.not_relevant }))
    .sort((a, b) => b.total - a.total)
}

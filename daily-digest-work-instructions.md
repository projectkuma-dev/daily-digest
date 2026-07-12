# Work Instructions: Daily Digest PWA with Feedback Loop

## Objective
Build a personalized daily digest system: Claude curates news/weather/finance each weekday morning, stores it as structured data, and I read it in a PWA on my Android phone (Samsung Galaxy S22). The digest is a 7-minute-or-less read (~1,100–1,300 words total). The PWA captures per-item feedback (swipe right = relevant, swipe left = not relevant), and that feedback is injected into future curation prompts so the digest self-tunes over time.

## Architecture
- **Scheduler:** GitHub Actions cron (13:00 UTC weekdays, plus `workflow_dispatch` for manual runs)
- **Curation:** Python script calling the Anthropic Messages API, model `claude-sonnet-4-6`, with the web search tool (`web_search_20250305`) enabled. Output is structured JSON, not prose.
- **Storage:** Supabase (PostgreSQL) — digests, digest items, feedback
- **Frontend:** React + Vite PWA hosted on GitHub Pages, reading/writing Supabase via supabase-js
- **Notification:** ntfy.sh push ("Your digest is ready" + link to PWA). Web push is a stretch goal, not v1.
- **Repos:** One monorepo `daily-digest` with `/pipeline` (Python) and `/app` (PWA)

## Data model (Supabase)

```sql
digests (
  id uuid pk default gen_random_uuid(),
  digest_date date unique not null,
  bottom_line text,
  created_at timestamptz default now()
)

digest_items (
  id uuid pk default gen_random_uuid(),
  digest_id uuid references digests(id),
  section text check (section in ('news','weather','finance')),
  position int,
  headline text not null,
  summary text not null,        -- 2-3 sentences, shown on card
  detail text,                  -- expanded paragraph, shown on tap
  sources jsonb,                -- [{title, url}]
  tags text[]                   -- 2-3 topic tags assigned by Claude
)

feedback (
  id uuid pk default gen_random_uuid(),
  item_id uuid references digest_items(id),
  verdict text check (verdict in ('relevant','not_relevant')),
  created_at timestamptz default now()
)

interest_profile (
  id int pk default 1,
  profile_text text not null,
  updated_at timestamptz default now()
)
```

Enable RLS. Single-user app: use the anon key with policies allowing read on digests/items and insert on feedback. Pipeline writes use the service role key (held only in GitHub secrets).

## Curation pipeline (`/pipeline/digest.py`)

1. **Load context:** Fetch `interest_profile.profile_text` and a feedback summary from Supabase — aggregate last 30 days of feedback as tag-level counts (e.g., `ai-policy: 8 relevant / 0 not; crypto: 0 / 5`).
2. **Curate:** Call the Anthropic API with web search enabled. System prompt includes: today's date, the interest profile, the feedback summary ("weight topics accordingly"), content requirements below, and instructions to respond ONLY with JSON matching the item schema (no preamble, no markdown fences). Parse defensively: strip any ```json fences before `JSON.parse`/`json.loads`.
3. **Validate:** Check structure, word budget (~1,300 total across summaries+details), and that every item has ≥1 source URL and 2–3 tags. If invalid, one retry asking Claude to fix.
4. **Store:** Insert digest + items into Supabase.
5. **Notify:** POST to `https://ntfy.sh/{NTFY_TOPIC}` — title "Daily Digest {date}", body = bottom_line + link to the PWA. On pipeline failure, send a failure notification instead of silence.
6. **Weekly profile rewrite (Sundays):** Separate scheduled job. Sends current profile + full feedback tag stats to Claude and asks it to rewrite the interest profile (max 200 words). Store the new version in `interest_profile`. Log old versions to a history table or file for audit.

## Digest content requirements
- **News:** 4–6 items. Prioritize national/world headlines, defense/DoD and defense-tech news, AI industry news.
- **Weather:** 1 item. Today's San Luis Obispo, CA forecast — high/low, precip, wind, one-line run/hike conditions callout.
- **Finance:** 2–3 items. Prior close + pre-market for S&P/Nasdaq/Dow; anything notable on Boeing (BA), broad index ETFs, Fed/macro. Facts only, no investment advice.
- **Voice:** Concise, declarative. No em dashes. Hard cap 1,300 words total. Include one `bottom_line` sentence for the whole digest.

## Seed interest profile (initial row)
"Defense and DoD technology, military logistics and C2 (USTRANSCOM, sealift, Palantir ecosystem), AI industry and AI policy, enterprise software, macro markets and Fed policy, Boeing. Low interest: celebrity news, sports, crypto."

## PWA (`/app`)

**Views:**
1. **Today view (default):** Vertical list of cards grouped under News / Weather / Finance headers, plus the bottom line at top. Each card: headline + summary + tag chips.
2. **Card interactions:**
   - **Tap** → card expands inline to show `detail` and a source list (external links).
   - **Swipe right** → insert feedback row `relevant`; card shows a subtle green check and stays in place.
   - **Swipe left** → insert feedback row `not_relevant`; subtle gray/dim state.
   - Swiping is optional per item; a second swipe overwrites the prior verdict (upsert on item_id).
   - Use a lightweight touch handler or `react-swipeable`; animate with CSS transforms. Cards must not accidentally trigger swipes during vertical scrolling — require a horizontal-dominant gesture with a threshold (~60px).
3. **Archive view:** Date picker or simple list of past digests.
4. **Profile view (read-only v1):** Shows current interest profile text and top tag stats so I can see what the system has learned.

**PWA requirements:** manifest + service worker (installable on Android), cache-first shell, works offline for the last loaded digest. Mobile-first layout at ~380px. Dark mode via `prefers-color-scheme`.

**Config:** Supabase URL + anon key in the frontend (safe with RLS). Base path configured for GitHub Pages.

## GitHub Actions
- `digest.yml`: cron `0 13 * * 1-5` + manual dispatch → runs `pipeline/digest.py`.
- `profile.yml`: cron `0 16 * * 0` → runs profile rewrite job.
- `deploy.yml`: build and deploy `/app` to GitHub Pages on push to main.
- Secrets: `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `NTFY_TOPIC`.

## Acceptance criteria
- Manual pipeline run produces a stored digest and an ntfy push within 3 minutes.
- Digest renders in the PWA with correct sections, under 1,300 words, with working source links on every item.
- Swipe right/left writes feedback rows; re-swipe updates rather than duplicates.
- The next curation run's prompt demonstrably includes the feedback summary (log the prompt context, redacting nothing sensitive).
- Weekly job rewrites the profile and the change is visible in the Profile view.
- PWA installs to Android home screen and loads the latest digest offline after first visit.
- Failure path: bad API key produces an ntfy failure notification.
- No secrets committed; service role key never shipped to the frontend.

## Out of scope (v1)
Web push notifications, multi-user support, per-source preferences, digest email fallback, in-app profile editing.

## Notes for the builder
- **Build order:** Stand up the Supabase schema and curation pipeline first. Verify a digest lands in the database via a manual `workflow_dispatch` run before starting the PWA, then build the frontend against real data. Do not build the app against mock data.
- I will create the Supabase project and add all GitHub secrets myself; do not ask me to paste keys into chat or files.
- Include a README with: Supabase SQL setup script, ntfy Android app setup, secrets list, manual test steps, and a rough monthly API cost estimate (~22 digest runs + 4 profile runs with web search).
- Keep the pipeline and app decoupled: the app only reads Supabase; it never calls the Anthropic API directly.

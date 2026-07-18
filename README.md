# Daily Digest

Personalized daily digest system. A GitHub Actions cron runs a Python pipeline each weekday morning: it gathers free source material (curated RSS feeds, the National Weather Service forecast for San Luis Obispo, Yahoo Finance quotes), then Claude curates it into structured news/weather/finance JSON, stores it in Supabase, and pings your phone via ntfy. A React PWA on GitHub Pages renders the digest; swiping cards right/left records relevance feedback that is injected into future curation prompts, so the digest self-tunes. A second weekly job rewrites the interest profile from accumulated feedback.

```
/pipeline   Python curation pipeline (digest.py, profile_rewrite.py)
/app        React + Vite PWA (GitHub Pages)
/supabase   schema.sql — run once in the Supabase SQL editor
/.github    workflows: digest (weekday cron), profile (Sunday cron), deploy (Pages)
```

## Setup (in this order)

### 1. Supabase (shared with Running Ideas)

This app shares the **Running Ideas** Supabase project — the free tier allows 2 projects and both slots are used (running-ideas, finance-app). The tables coexist cleanly: running-ideas owns `ideas_history`; this app owns `digests`, `digest_items`, `feedback`, `interest_profile`, `profile_history`. RLS is per-table, so neither app affects the other. Bonus: the weekday digest cron keeps the shared project from being paused for inactivity.

1. Open the **running-ideas** project in the Supabase dashboard.
2. In **SQL Editor**, paste the contents of [`supabase/schema.sql`](supabase/schema.sql), run it. This creates the digest tables, enables RLS, and seeds the interest profile.
3. From **Project Settings → API**, note three values: the **Project URL**, the **anon public key**, and the **service_role key** (keep this one secret — note it can access running-ideas tables too, since it belongs to the shared project).

### 2. ntfy (Android push)

1. Install the **ntfy** app from the Play Store on the Galaxy S22.
2. In the app, tap **+ Subscribe to topic** and enter a topic name that is long and unguessable (it acts as a password), e.g. `bk-digest-x7q94mzt`. Server stays `ntfy.sh`.
3. That topic name is your `NTFY_TOPIC` secret below.

### 3. GitHub repo + secrets

1. Create a GitHub repo (e.g. `daily-digest`), push this folder to `main`.
2. In **Settings → Pages**, set Source to **GitHub Actions**.
3. In **Settings → Secrets and variables → Actions → Secrets**, add:

| Secret | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key (console.anthropic.com) |
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_KEY` | Supabase service_role key (pipeline writes; never shipped to the frontend) |
| `SUPABASE_ANON_KEY` | Supabase anon key (baked into the PWA build; safe with RLS) |
| `NTFY_TOPIC` | Your ntfy topic name |

Optional: set `PWA_URL` as a repository **variable** if your Pages URL differs from `https://<owner>.github.io/<repo>/` (the pipeline derives that automatically for the notification's click-through link).

### 4. First run (verify the pipeline before using the app)

1. **Actions → Daily digest → Run workflow** (manual dispatch).
2. Within ~3 minutes you should get an ntfy push ("Daily Digest YYYY-MM-DD") and see rows in Supabase (`digests`, `digest_items`). The full curation prompt (profile + feedback summary) is in the workflow log.
3. Pushing to `main` also triggers the Pages deploy. Open `https://<owner>.github.io/<repo>/` — the digest should render.
4. On the phone, open that URL in Chrome → menu → **Add to Home screen** to install the PWA.

## Manual test steps

- **Sources:** `python pipeline/sources.py` prints a self-test — news counts per feed, the NWS forecast, and quotes — without calling Claude or Supabase.
- **Curation dry run:** with just `ANTHROPIC_API_KEY` set (no Supabase needed), `python pipeline/digest.py --dry-run` curates a real digest from live sources using the seed profile and prints the JSON without storing or notifying. Costs ~$0.03. Use this to judge Haiku's curation quality before any infrastructure setup.
- **Pipeline:** dispatch *Daily digest*; confirm ntfy push + stored digest + prompt context in the log.
- **Failure path:** temporarily set `ANTHROPIC_API_KEY` to a bad value, dispatch again; confirm you get a high-priority "Daily Digest failed" ntfy notification. Restore the key.
- **App:** open the PWA; confirm sections render with working source links on every item; total read is under 1,300 words.
- **Feedback:** swipe a card right (green check appears) and left (card dims); check the `feedback` table has one row per item; re-swiping updates the row instead of duplicating.
- **Feedback loop:** dispatch *Daily digest* again the next day; the workflow log's prompt should show your tag counts under "READER FEEDBACK".
- **Profile rewrite:** dispatch *Weekly profile rewrite*; confirm `interest_profile` changed, the old text landed in `profile_history`, and the Profile view shows the new text.
- **Offline:** load the digest once, enable airplane mode, reopen the installed app; the last digest still renders.

## How the feedback loop works

1. Every card swipe upserts a row in `feedback` (one verdict per item).
2. Each digest run aggregates the last 30 days of feedback into tag-level counts (e.g. `ai-policy: 8 relevant / 0 not relevant`) and injects them into the curation prompt with instructions to weight topics accordingly.
3. Sundays, `profile_rewrite.py` sends the current profile + all-time tag stats to Claude, which rewrites the profile (≤200 words). Old versions are archived in `profile_history`.

## Estimated monthly API cost

Model: `claude-haiku-4-5` ($1 / $5 per MTok input/output). All source data (RSS, NWS, Yahoo Finance) is free, so the only metered cost is Claude tokens. Note the Anthropic API is pay-per-use, billed separately from a Claude.ai subscription.

| Job | Runs/mo | Per run | Monthly |
|---|---|---|---|
| Digest curation (~10K in: source material + profile; ~3–5K out) | ~22 | ~$0.03 | ~$0.65 |
| Profile rewrite (~2K in, ~500 out) | ~4 | <$0.01 | <$0.05 |

**Total: roughly $0.70–1/month.** Supabase free tier, ntfy.sh, NWS, RSS, and Yahoo quotes are $0; GitHub Actions usage is well within the free allowance. For a true $0/month option that runs curation on a Claude subscription instead of the metered API, see [docs/v2-path2-zero-cost.md](docs/v2-path2-zero-cost.md).

## Implementation notes

- Curation is grounded in fetched material, not web search: `pipeline/sources.py` pulls RSS headlines (feed list is a constant at the top — edit it to change coverage), the NWS forecast, and Yahoo quotes, and the prompt forbids inventing stories or URLs. A dead feed just shrinks the material; the run aborts only if fewer than 10 news items arrive.
- If curation quality feels thin on Haiku, switching to `claude-sonnet-4-6` is a one-line `MODEL` change in each pipeline script (roughly +$2/month at these token volumes).
- `feedback.item_id` has a unique constraint so re-swipes upsert instead of duplicating.
- The digest upserts on `digest_date`, so re-running the workflow on the same day replaces that day's digest instead of failing.
- If the digest is structurally invalid, the pipeline retries once with the specific problems; a word count slightly over budget (≤1,500) ships with a warning rather than failing the morning run.
- The app never calls the Anthropic API; it only reads/writes Supabase with the anon key under RLS.
- Offline support: the service worker caches the app shell; the last loaded digest is cached in localStorage.

## Local development

```sh
cd app
cp .env.example .env   # fill in Supabase URL + anon key
npm install
npm run dev
```

Pipeline locally: `pip install -r pipeline/requirements.txt`, set `ANTHROPIC_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (and optionally `NTFY_TOPIC`), then `python pipeline/digest.py`.

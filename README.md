# Daily Digest

Personalized daily digest system. A GitHub Actions cron runs a Python pipeline each weekday morning: Claude (with web search) curates news/weather/finance into structured JSON, stores it in Supabase, and pings your phone via ntfy. A React PWA on GitHub Pages renders the digest; swiping cards right/left records relevance feedback that is injected into future curation prompts, so the digest self-tunes. A second weekly job rewrites the interest profile from accumulated feedback.

```
/pipeline   Python curation pipeline (digest.py, profile_rewrite.py)
/app        React + Vite PWA (GitHub Pages)
/supabase   schema.sql — run once in the Supabase SQL editor
/.github    workflows: digest (weekday cron), profile (Sunday cron), deploy (Pages)
```

## Setup (in this order)

### 1. Supabase

1. Create a project at [supabase.com](https://supabase.com) (free tier is fine).
2. Open **SQL Editor**, paste the contents of [`supabase/schema.sql`](supabase/schema.sql), run it. This creates the tables, enables RLS, and seeds the interest profile.
3. From **Project Settings → API**, note three values: the **Project URL**, the **anon public key**, and the **service_role key** (keep this one secret).

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

Model: `claude-sonnet-4-6` ($3 / $15 per MTok input/output; web search $10 per 1,000 searches).

| Job | Runs/mo | Per run | Monthly |
|---|---|---|---|
| Digest: web search (~10–15 searches, capped at 15) | ~22 | $0.10–0.15 | $2.20–3.30 |
| Digest: tokens (~40–80K in incl. search results, ~4–8K out incl. thinking) | ~22 | $0.18–0.36 | $4.00–8.00 |
| Profile rewrite (~2K in, ~500 out) | ~4 | <$0.02 | <$0.10 |

**Total: roughly $6–11/month.** Supabase free tier and ntfy.sh are $0; GitHub Actions usage is well within the free allowance for public or personal repos.

## Implementation notes

- The pipeline uses the `web_search_20260209` tool variant (the current one for `claude-sonnet-4-6`, with dynamic result filtering) rather than the older `web_search_20250305` named in the original work instructions.
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

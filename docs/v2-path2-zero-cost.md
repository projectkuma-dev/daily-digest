# v2 alternative: Path 2 — $0/month via a scheduled Claude task

Status: **documented, not built.** Decision point: **~2026-08-18** (one month after Path 1 went live). A scheduled reminder exists for this assessment.

## Context

v1 (Path 1) curates from free sources (RSS + NWS + Yahoo Finance) with `claude-haiku-4-5` on the metered Anthropic API, called from GitHub Actions. Cost: roughly **$0.70–1/month**, all of it Claude tokens. Path 2 eliminates that remaining cost by running curation on the Claude.ai subscription instead of the pay-per-use API.

## Architecture

Replace the GitHub Actions `digest.yml` cron + `pipeline/digest.py` API call with a **scheduled Claude task** (same mechanism as the morning-brief routine; created via the schedule skill / scheduled cloud agents):

```
Scheduled Claude task (weekday cron, runs on subscription)
  1. Fetch material: RSS feeds, NWS forecast, Yahoo quotes
     (bash/curl or web fetch — same sources as pipeline/sources.py)
  2. Read interest_profile + feedback tag counts from Supabase REST
  3. Curate the digest JSON (the task IS Claude — no API call needed)
  4. POST digest + items to Supabase REST endpoints
  5. POST "digest ready" to ntfy.sh/{topic}
```

Everything else is untouched: Supabase schema, RLS, the PWA, Pages deploy, the ntfy channel, and the weekly profile-rewrite job (which could also become a scheduled task, or stay on Actions at <$0.05/month).

## What the task needs

- **The curation prompt** — port `build_system_prompt()` from `pipeline/digest.py` (profile, feedback weighting, content requirements, JSON schema, tag rules) into the task instructions.
- **Supabase write access** — the REST endpoint (`https://<project>.supabase.co/rest/v1/...`) with a write credential in the task setup. Options, best first:
  1. A Postgres function (`security definer`) exposed via RPC that only inserts digests/items, callable with the anon key — narrowest blast radius.
  2. The service-role key embedded in the task configuration — simplest, but it's a full-access key living outside GitHub's secret store, and the shared project also holds running-ideas tables.
- **The ntfy topic name.**

## Trade-offs vs Path 1

| | Path 1 (Actions + metered API) | Path 2 (scheduled Claude task) |
|---|---|---|
| Cost | ~$0.70–1/mo | $0 extra (subscription covers it) |
| Cron reliability | GitHub Actions — battle-hardened | Scheduled tasks — newer, less proven for must-run-daily jobs |
| Usage | Independent of subscription | Draws from subscription usage limits daily |
| Secrets | GitHub encrypted secrets | Credential lives in task config (mitigate via RPC option above) |
| Validation/retry | Deterministic Python (`validate_digest`) | Prompt-enforced; no hard validation layer unless the task scripts one |
| Debugging | Actions logs, exit codes, failure ntfy | Task session transcripts |

## Migration steps (when/if triggered)

1. Create the Supabase RPC insert function (option 1 above) and test it with the anon key.
2. Create the scheduled task with the ported prompt + fetch steps; run it manually once and verify rows land and the PWA renders them.
3. Disable the `digest.yml` cron (keep `workflow_dispatch` as a manual fallback).
4. Watch one full week for missed/duplicate runs before trusting it.
5. Optionally migrate `profile.yml` the same way.

## Assessment criteria (2026-08-18)

Stay on Path 1 unless:
- Actual Anthropic console spend for the month materially exceeds the ~$1 estimate, or
- The subscription's scheduled-task limits comfortably absorb a daily run, and the reliability trade feels acceptable after a trial, or
- Anthropic API billing/minimums make tiny monthly spends annoying in practice.

Also assess at the same time: Haiku curation quality (would the digest benefit from Sonnet at ~+$2/mo more than from going to $0?).

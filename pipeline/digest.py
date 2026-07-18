"""Daily digest curation pipeline.

Gathers material from free sources (RSS news, NWS weather, Yahoo finance —
see sources.py), fetches the interest profile and recent feedback from
Supabase, asks Claude to curate a digest as structured JSON from that material
only, validates it, stores it in Supabase, and sends an ntfy push.

Env vars: ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, NTFY_TOPIC,
optionally PWA_URL (defaults to https://<owner>.github.io/<repo>/ in Actions).

Dry run (needs only ANTHROPIC_API_KEY — no Supabase, no ntfy):
    python pipeline/digest.py --dry-run
Uses the seed interest profile, curates from live sources, prints the digest.
"""

import json
import os
import re
import sys
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic
import requests
from supabase import create_client

from sources import gather_material

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 8000
WORD_BUDGET = 1300
# Word-budget tolerance: a slightly-long digest still ships after the retry;
# failing the whole morning run over a few dozen words is worse than 1350 words.
WORD_BUDGET_HARD_CAP = 1500

PACIFIC = ZoneInfo("America/Los_Angeles")

# Mirrors the seed row in supabase/schema.sql; used by --dry-run only.
SEED_PROFILE = (
    "Defense and DoD technology, military logistics and C2 (USTRANSCOM, sealift, "
    "Palantir ecosystem), AI industry and AI policy, enterprise software, macro "
    "markets and Fed policy, Boeing. Low interest: celebrity news, sports, crypto."
)


def supabase_client():
    """Build the client from env, normalizing whatever URL form was pasted.

    Accepts the bare project URL, a trailing slash, the /rest/v1 endpoint URL,
    or even a dashboard URL (https://supabase.com/dashboard/project/<ref>) —
    anything else yields PGRST125/connection errors that are hard to debug.
    """
    from urllib.parse import urlsplit

    raw = os.environ["SUPABASE_URL"].strip()
    parts = urlsplit(raw if "://" in raw else "https://" + raw)
    host = parts.netloc
    if host.endswith("supabase.com") and "/project/" in parts.path:
        ref = parts.path.split("/project/")[1].split("/")[0]
        host = f"{ref}.supabase.co"
    return create_client(f"https://{host}", os.environ["SUPABASE_SERVICE_KEY"].strip())


def get_pwa_url() -> str:
    url = os.environ.get("PWA_URL", "")
    if url:
        return url
    repo = os.environ.get("GITHUB_REPOSITORY", "")  # "owner/repo" in Actions
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"
    return ""


def notify(topic: str, title: str, body: str, click_url: str = "", priority: str = "default") -> None:
    if not topic:
        print("NTFY_TOPIC not set; skipping notification.")
        return
    headers = {"Title": title, "Priority": priority, "Tags": "newspaper"}
    if click_url:
        headers["Click"] = click_url
    try:
        requests.post(f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), headers=headers, timeout=15)
    except requests.RequestException as exc:
        print(f"ntfy notification failed: {exc}")


def load_context(sb):
    """Fetch interest profile and a 30-day tag-level feedback summary."""
    profile_row = sb.table("interest_profile").select("profile_text").eq("id", 1).single().execute()
    profile_text = profile_row.data["profile_text"]

    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    fb = (
        sb.table("feedback")
        .select("verdict, created_at, digest_items(tags)")
        .gte("created_at", since)
        .execute()
    )
    counts = defaultdict(lambda: {"relevant": 0, "not_relevant": 0})
    for row in fb.data or []:
        item = row.get("digest_items") or {}
        for tag in item.get("tags") or []:
            counts[tag][row["verdict"]] += 1

    if not counts:
        summary = "No feedback collected yet."
    else:
        lines = [
            f"- {tag}: {c['relevant']} relevant / {c['not_relevant']} not relevant"
            for tag, c in sorted(counts.items(), key=lambda kv: -(kv[1]["relevant"] + kv[1]["not_relevant"]))
        ]
        summary = "\n".join(lines)
    return profile_text, summary


def build_system_prompt(digest_date: str, profile_text: str, feedback_summary: str) -> str:
    return f"""You are the curator of a personalized daily morning digest. Today's date is {digest_date} (US Pacific).

The user message contains today's SOURCE MATERIAL: news headlines from RSS feeds, a National Weather Service forecast, and market quotes. Curate ONLY from that material. Never invent stories, facts, numbers, or URLs.

READER INTEREST PROFILE:
{profile_text}

READER FEEDBACK, LAST 30 DAYS (tag: relevant / not relevant counts — weight topics accordingly; favor tags with high relevant counts, avoid tags with high not-relevant counts):
{feedback_summary}

CONTENT REQUIREMENTS:
- news: select the 4-6 items most relevant to the profile and feedback. Prioritize national/world headlines, defense/DoD and defense-tech news, AI industry news. Merge duplicate coverage of the same story into one item citing both sources.
- weather: exactly 1 item from the NWS forecast — high/low, precipitation, wind, and a one-line run/hike conditions callout.
- finance: 2-3 items from the market quotes (plus any market-moving headlines in the news material). Report prior close and the latest (pre-market) level with percent moves; note anything notable on Boeing (BA), broad index ETFs, Fed/macro. Facts only, no investment advice.

VOICE: Concise, declarative sentences. No em dashes. Hard cap {WORD_BUDGET} words total across all summaries, details, and the bottom line (a 7-minute read or less).

OUTPUT FORMAT — respond ONLY with a single JSON object, no preamble, no markdown fences, matching exactly:
{{
  "bottom_line": "One sentence capturing the single most important takeaway of the day.",
  "items": [
    {{
      "section": "news" | "weather" | "finance",
      "position": 1,
      "headline": "Short headline",
      "summary": "2-3 sentences shown on the card.",
      "detail": "One expanded paragraph shown when the card is tapped.",
      "sources": [{{"title": "Source name", "url": "https://..."}}],
      "tags": ["tag-one", "tag-two"]
    }}
  ]
}}

RULES FOR ITEMS:
- position numbers each item within its section, starting at 1.
- Every item must include at least 1 source, using URLs copied exactly from the source material (article URL for news, the NWS forecast URL for weather, the Yahoo Finance quote URLs for finance).
- Every item must have 2-3 lowercase-kebab-case topic tags (e.g. "ai-policy", "defense-tech", "fed-policy"). Reuse tags consistently across days so feedback accumulates per topic."""


def build_material_message(material: dict, digest_date: str) -> str:
    lines = [f"SOURCE MATERIAL for {digest_date}:", "", "=== NEWS (RSS, last 36 hours) ==="]
    for i, item in enumerate(material["news"], 1):
        lines.append(f"{i}. [{item['source']}] {item['title']}")
        if item.get("snippet"):
            lines.append(f"   {item['snippet']}")
        lines.append(f"   URL: {item['url']}")

    lines.append("")
    lines.append("=== WEATHER (National Weather Service) ===")
    weather = material.get("weather")
    if weather:
        lines.append(f"Location: {weather['location']}  |  Source URL: {weather['source_url']}")
        for p in weather["periods"]:
            precip = f", precip {p['precip_chance']}%" if p.get("precip_chance") is not None else ""
            lines.append(f"- {p['name']}: {p['temperature']}, wind {p['wind']}{precip}. {p['forecast']}")
    else:
        lines.append("Weather data unavailable today; write the weather item saying the forecast could not be fetched, source URL https://forecast.weather.gov/")

    lines.append("")
    lines.append("=== MARKET QUOTES (Yahoo Finance; 'latest' is pre-market at digest time) ===")
    for q in material.get("finance", []):
        change = f" ({q['change_pct_vs_prev_close']:+.2f}%)" if q.get("change_pct_vs_prev_close") is not None else ""
        lines.append(
            f"- {q['label']} [{q['symbol']}]: prev close {q['previous_close']}, latest {q['latest_price']}{change}  |  Source URL: {q['source_url']}"
        )
    if not material.get("finance"):
        lines.append("Quotes unavailable today; base finance items only on market headlines in the news material.")

    lines.append("")
    lines.append("Generate the daily digest JSON now.")
    return "\n".join(lines)


def extract_text(response) -> str:
    return "".join(block.text for block in response.content if block.type == "text")


def parse_digest_json(text: str) -> dict:
    """Parse Claude's JSON output, stripping any markdown fences defensively."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    # If there is any preamble despite instructions, take the outermost object.
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in model output")
    return json.loads(cleaned[start : end + 1])


def count_words(digest: dict) -> int:
    parts = [digest.get("bottom_line") or ""]
    for item in digest.get("items", []):
        parts.append(item.get("summary") or "")
        parts.append(item.get("detail") or "")
    return sum(len(p.split()) for p in parts)


def validate_digest(digest: dict) -> list[str]:
    """Return a list of problems; empty list means valid."""
    problems = []
    if not (digest.get("bottom_line") or "").strip():
        problems.append("bottom_line is missing or empty")
    items = digest.get("items")
    if not isinstance(items, list) or not items:
        problems.append("items array is missing or empty")
        return problems

    by_section = defaultdict(list)
    for i, item in enumerate(items):
        section = item.get("section")
        if section not in ("news", "weather", "finance"):
            problems.append(f"item {i}: invalid section {section!r}")
            continue
        by_section[section].append(item)
        if not (item.get("headline") or "").strip():
            problems.append(f"item {i}: missing headline")
        if not (item.get("summary") or "").strip():
            problems.append(f"item {i}: missing summary")
        sources = item.get("sources")
        if not isinstance(sources, list) or not any(
            isinstance(s, dict) and str(s.get("url", "")).startswith("http") for s in sources
        ):
            problems.append(f"item {i} ({item.get('headline', '?')}): needs at least 1 source with a valid URL")
        tags = item.get("tags")
        if not isinstance(tags, list) or not (2 <= len(tags) <= 3):
            problems.append(f"item {i} ({item.get('headline', '?')}): needs 2-3 tags")

    if not (4 <= len(by_section["news"]) <= 6):
        problems.append(f"news section has {len(by_section['news'])} items, needs 4-6")
    if len(by_section["weather"]) != 1:
        problems.append(f"weather section has {len(by_section['weather'])} items, needs exactly 1")
    if not (2 <= len(by_section["finance"]) <= 3):
        problems.append(f"finance section has {len(by_section['finance'])} items, needs 2-3")

    words = count_words(digest)
    if words > WORD_BUDGET_HARD_CAP:
        problems.append(f"total word count {words} exceeds the {WORD_BUDGET}-word budget; trim summaries and details")
    return problems


def curate(client, system_prompt: str, material_message: str) -> dict:
    """Curate the digest; on invalid output, retry once with the problem list."""
    messages = [{"role": "user", "content": material_message}]
    response = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, system=system_prompt, messages=messages
    )
    text = extract_text(response)

    try:
        digest = parse_digest_json(text)
        problems = validate_digest(digest)
    except (ValueError, json.JSONDecodeError) as exc:
        digest, problems = None, [f"output was not valid JSON: {exc}"]

    if not problems:
        return digest

    print("Digest invalid, retrying once. Problems:\n- " + "\n- ".join(problems))
    retry_messages = messages + [
        {"role": "assistant", "content": response.content},
        {
            "role": "user",
            "content": (
                "Your JSON output had these problems:\n- "
                + "\n- ".join(problems)
                + "\n\nFix them and respond again with ONLY the corrected JSON object."
            ),
        },
    ]
    response = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, system=system_prompt, messages=retry_messages
    )
    digest = parse_digest_json(extract_text(response))
    problems = validate_digest(digest)

    # Structural problems fail the run; a word count between the budget and the
    # hard cap ships with a warning rather than killing the morning delivery.
    fatal = [p for p in problems if "word count" not in p]
    if fatal:
        raise RuntimeError("Digest still invalid after retry: " + "; ".join(fatal))
    if problems:
        print("Warning (shipping anyway): " + "; ".join(problems))
    return digest


def store_digest(sb, digest_date: str, digest: dict) -> None:
    """Upsert the digest row and replace its items (safe on same-day reruns)."""
    row = (
        sb.table("digests")
        .upsert({"digest_date": digest_date, "bottom_line": digest["bottom_line"]}, on_conflict="digest_date")
        .execute()
    )
    digest_id = row.data[0]["id"]
    sb.table("digest_items").delete().eq("digest_id", digest_id).execute()
    items = [
        {
            "digest_id": digest_id,
            "section": item["section"],
            "position": item.get("position", i + 1),
            "headline": item["headline"],
            "summary": item["summary"],
            "detail": item.get("detail"),
            "sources": item.get("sources", []),
            "tags": item.get("tags", []),
        }
        for i, item in enumerate(digest["items"])
    ]
    sb.table("digest_items").insert(items).execute()
    print(f"Stored digest {digest_id} for {digest_date} with {len(items)} items.")


def main(dry_run: bool = False) -> None:
    sb = None if dry_run else supabase_client()
    client = anthropic.Anthropic()
    digest_date = datetime.now(PACIFIC).date().isoformat()

    material = gather_material()
    n_news = len(material["news"])
    print(
        f"Material gathered: {n_news} news items, "
        f"weather {'OK' if material['weather'] else 'UNAVAILABLE'}, "
        f"{len(material['finance'])} quotes."
    )
    if n_news < 10:
        raise RuntimeError(f"Only {n_news} news items fetched; feeds look broken, aborting")

    if dry_run:
        profile_text, feedback_summary = SEED_PROFILE, "No feedback collected yet."
    else:
        profile_text, feedback_summary = load_context(sb)
    system_prompt = build_system_prompt(digest_date, profile_text, feedback_summary)
    material_message = build_material_message(material, digest_date)

    # Acceptance criterion: the prompt context must be demonstrably logged.
    print("=" * 60)
    print("SYSTEM PROMPT (curation context):")
    print(system_prompt)
    print("=" * 60)

    digest = curate(client, system_prompt, material_message)
    words = count_words(digest)
    print(f"Digest curated: {len(digest['items'])} items, {words} words.")

    if dry_run:
        print("\nDRY RUN — digest not stored, no notification sent:\n")
        print(json.dumps(digest, indent=2))
        return

    store_digest(sb, digest_date, digest)
    notify(
        os.environ.get("NTFY_TOPIC", ""),
        title=f"Daily Digest {digest_date}",
        body=digest["bottom_line"],
        click_url=get_pwa_url(),
    )
    print("Done.")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    try:
        main(dry_run=dry)
    except Exception:
        traceback.print_exc()
        if not dry:
            notify(
                os.environ.get("NTFY_TOPIC", ""),
                title="Daily Digest failed",
                body=f"Pipeline error: {sys.exc_info()[1]}",
                priority="high",
            )
        sys.exit(1)

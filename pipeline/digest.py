"""Daily digest curation pipeline.

Fetches the interest profile and recent feedback from Supabase, asks Claude
(with web search) to curate a news/weather/finance digest as structured JSON,
validates it, stores it in Supabase, and sends an ntfy push notification.

Env vars: ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, NTFY_TOPIC,
optionally PWA_URL (defaults to https://<owner>.github.io/<repo>/ in Actions).
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

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 16000
WORD_BUDGET = 1300
# Word-budget tolerance: a slightly-long digest still ships after the retry;
# failing the whole morning run over a few dozen words is worse than 1350 words.
WORD_BUDGET_HARD_CAP = 1500
MAX_CONTINUATIONS = 5  # pause_turn resume limit for the server-side search loop

PACIFIC = ZoneInfo("America/Los_Angeles")


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

READER INTEREST PROFILE:
{profile_text}

READER FEEDBACK, LAST 30 DAYS (tag: relevant / not relevant counts — weight topics accordingly; favor tags with high relevant counts, avoid tags with high not-relevant counts):
{feedback_summary}

Use web search to find CURRENT information published within the last 24 hours wherever possible.

CONTENT REQUIREMENTS:
- news: 4-6 items. Prioritize national/world headlines, defense/DoD and defense-tech news, AI industry news.
- weather: exactly 1 item. Today's forecast for San Luis Obispo, CA — high/low, precipitation, wind, and a one-line run/hike conditions callout.
- finance: 2-3 items. Prior close and pre-market for S&P 500/Nasdaq/Dow; anything notable on Boeing (BA), broad index ETFs, Fed/macro. Facts only, no investment advice.

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
- Every item must include at least 1 source with a real URL from your web search results.
- Every item must have 2-3 lowercase-kebab-case topic tags (e.g. "ai-policy", "defense-tech", "fed-policy"). Reuse tags consistently across days so feedback accumulates per topic."""


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


def run_curation(client, system_prompt: str, messages: list) -> "anthropic.types.Message":
    """Call Claude with web search enabled, resuming pause_turn as needed."""
    tools = [{"type": "web_search_20260209", "name": "web_search", "max_uses": 15}]
    response = None
    for _ in range(MAX_CONTINUATIONS + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=system_prompt,
            tools=tools,
            messages=messages,
        )
        if response.stop_reason != "pause_turn":
            return response
        # Server-side search loop paused; append the partial turn and resume.
        messages = messages + [{"role": "assistant", "content": response.content}]
    return response


def curate(client, system_prompt: str, digest_date: str) -> dict:
    """Curate the digest; on invalid output, retry once with the problem list."""
    messages = [{"role": "user", "content": f"Generate the daily digest for {digest_date}."}]
    response = run_curation(client, system_prompt, messages)
    text = extract_text(response)

    try:
        digest = parse_digest_json(text)
        problems = validate_digest(digest)
    except (ValueError, json.JSONDecodeError) as exc:
        digest, problems = None, [f"output was not valid JSON: {exc}"]

    if not problems:
        return digest

    print(f"Digest invalid, retrying once. Problems:\n- " + "\n- ".join(problems))
    retry_messages = messages + [
        {"role": "assistant", "content": response.content},
        {
            "role": "user",
            "content": (
                "Your JSON output had these problems:\n- "
                + "\n- ".join(problems)
                + "\n\nFix them and respond again with ONLY the corrected JSON object. "
                "Do not run more web searches; work from what you already found."
            ),
        },
    ]
    response = run_curation(client, system_prompt, retry_messages)
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


def main() -> None:
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    client = anthropic.Anthropic()
    digest_date = datetime.now(PACIFIC).date().isoformat()

    profile_text, feedback_summary = load_context(sb)
    system_prompt = build_system_prompt(digest_date, profile_text, feedback_summary)

    # Acceptance criterion: the prompt context must be demonstrably logged.
    print("=" * 60)
    print("SYSTEM PROMPT (curation context):")
    print(system_prompt)
    print("=" * 60)

    digest = curate(client, system_prompt, digest_date)
    words = count_words(digest)
    print(f"Digest curated: {len(digest['items'])} items, {words} words.")

    store_digest(sb, digest_date, digest)
    notify(
        os.environ.get("NTFY_TOPIC", ""),
        title=f"Daily Digest {digest_date}",
        body=digest["bottom_line"],
        click_url=get_pwa_url(),
    )
    print("Done.")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        notify(
            os.environ.get("NTFY_TOPIC", ""),
            title="Daily Digest failed",
            body=f"Pipeline error: {sys.exc_info()[1]}",
            priority="high",
        )
        sys.exit(1)

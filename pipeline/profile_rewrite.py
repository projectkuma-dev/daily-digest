"""Weekly interest-profile rewrite (runs Sundays).

Sends the current profile plus all-time feedback tag stats to Claude, asks it
to rewrite the profile (max 200 words), archives the old version to
profile_history, and stores the new version in interest_profile.

Env vars: ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, NTFY_TOPIC.
"""

import os
import sys
import traceback
from collections import defaultdict

import anthropic
import requests
from supabase import create_client

MODEL = "claude-haiku-4-5"
MAX_PROFILE_WORDS = 200


def notify_failure(message: str) -> None:
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=f"Profile rewrite error: {message}".encode("utf-8"),
            headers={"Title": "Daily Digest profile rewrite failed", "Priority": "high", "Tags": "warning"},
            timeout=15,
        )
    except requests.RequestException as exc:
        print(f"ntfy notification failed: {exc}")


def load_tag_stats(sb) -> str:
    fb = sb.table("feedback").select("verdict, digest_items(tags)").execute()
    counts = defaultdict(lambda: {"relevant": 0, "not_relevant": 0})
    for row in fb.data or []:
        item = row.get("digest_items") or {}
        for tag in item.get("tags") or []:
            counts[tag][row["verdict"]] += 1
    if not counts:
        return "No feedback collected yet."
    return "\n".join(
        f"- {tag}: {c['relevant']} relevant / {c['not_relevant']} not relevant"
        for tag, c in sorted(counts.items(), key=lambda kv: -(kv[1]["relevant"] + kv[1]["not_relevant"]))
    )


def main() -> None:
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    client = anthropic.Anthropic()

    current = sb.table("interest_profile").select("profile_text").eq("id", 1).single().execute()
    profile_text = current.data["profile_text"]
    tag_stats = load_tag_stats(sb)

    prompt = f"""You maintain the interest profile that drives a personalized daily news digest.

CURRENT PROFILE:
{profile_text}

ALL-TIME FEEDBACK BY TAG (relevant / not relevant counts from the reader swiping on digest items):
{tag_stats}

Rewrite the interest profile to reflect what the feedback shows the reader actually finds relevant.
Rules:
- Keep it under {MAX_PROFILE_WORDS} words.
- Keep it as flowing descriptive text (topics and emphasis), ending with a "Low interest:" clause listing topics to avoid.
- Strengthen topics with consistently relevant feedback, soften or drop topics with consistently not-relevant feedback, and keep stated interests that have no feedback yet.
- Respond ONLY with the new profile text. No preamble, no quotes, no markdown."""

    print("=" * 60)
    print("PROFILE REWRITE PROMPT:")
    print(prompt)
    print("=" * 60)

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    new_profile = "".join(b.text for b in response.content if b.type == "text").strip().strip('"')
    if not new_profile:
        raise RuntimeError("Model returned an empty profile")
    words = len(new_profile.split())
    if words > MAX_PROFILE_WORDS + 50:
        raise RuntimeError(f"New profile is {words} words, over the {MAX_PROFILE_WORDS}-word limit")

    # Archive old version, then replace.
    sb.table("profile_history").insert({"profile_text": profile_text}).execute()
    sb.table("interest_profile").update(
        {"profile_text": new_profile, "updated_at": "now()"}
    ).eq("id", 1).execute()

    print(f"Profile updated ({words} words):\n{new_profile}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        notify_failure(str(sys.exc_info()[1]))
        sys.exit(1)

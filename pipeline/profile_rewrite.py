"""Weekly interest-profile rewrite (runs Sundays).

Sends the current profile plus feedback statistics to Claude, asks it to
adjust the profile (max 200 words), archives the old version to
profile_history, and stores the new version in interest_profile.

The job is deliberately conservative. Feedback is biased by what was served:
a topic that was rarely shown collects few swipes, which is NOT evidence of
disinterest. Without guardrails the profile ratchets toward whatever the feeds
happen to over-supply (this happened on 2026-07-19, when a defense-heavy feed
mix collapsed the whole profile into a defense brief). So the prompt below
receives served counts alongside swipe counts, must preserve every category,
and may only demote a topic on strong evidence.

Env vars: ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, NTFY_TOPIC.
"""

import os
import sys
import traceback
from collections import defaultdict

import anthropic
import requests

from digest import supabase_client

MODEL = "claude-haiku-4-5"
MAX_PROFILE_WORDS = 200
# A topic may only be demoted with at least this many swipes and this share negative.
DEMOTE_MIN_SWIPES = 4
DEMOTE_MIN_NEGATIVE_SHARE = 0.7


def notify(title: str, body: str, priority: str = "default") -> None:
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{topic}",
            data=body.encode("utf-8"),
            headers={"Title": title, "Priority": priority, "Tags": "gear"},
            timeout=15,
        )
    except requests.RequestException as exc:
        print(f"ntfy notification failed: {exc}")


def load_tag_stats(sb) -> str:
    """Tag table combining how often each tag was SERVED with how it was swiped.

    Served counts are what make the selection bias visible to the model.
    """
    served = defaultdict(int)
    items = sb.table("digest_items").select("tags").execute()
    for row in items.data or []:
        for tag in row.get("tags") or []:
            served[tag] += 1

    swipes = defaultdict(lambda: {"relevant": 0, "not_relevant": 0})
    fb = sb.table("feedback").select("verdict, digest_items(tags)").execute()
    for row in fb.data or []:
        item = row.get("digest_items") or {}
        for tag in item.get("tags") or []:
            swipes[tag][row["verdict"]] += 1

    if not served:
        return "No digests served yet."

    lines = ["tag | times served | relevant | not relevant"]
    for tag, n_served in sorted(served.items(), key=lambda kv: -kv[1]):
        s = swipes.get(tag, {"relevant": 0, "not_relevant": 0})
        lines.append(f"{tag} | {n_served} | {s['relevant']} | {s['not_relevant']}")
    return "\n".join(lines)


def build_prompt(profile_text: str, tag_stats: str) -> str:
    return f"""You maintain the interest profile that drives a personalized daily news digest. Your job is careful, conservative maintenance, not redesign.

CURRENT PROFILE:
{profile_text}

TAG STATISTICS:
{tag_stats}

HOW TO READ THE STATISTICS — read this carefully, it is the most common source of error:
The "times served" column shows how often the curator picked items with that tag. It reflects what the news feeds happened to supply, NOT what the reader wants. A topic served rarely will collect few swipes no matter how much the reader likes it. Absence of positive feedback is therefore NOT evidence of disinterest. Only the ratio of relevant to not-relevant swipes carries signal, and only once there are enough swipes to mean anything.

RULES:
1. PRESERVE BREADTH. The current profile names several distinct interest categories. Your rewrite must still name every one of them. You may adjust emphasis and wording, but never delete a category, and never merge one into another.
2. NEVER reframe one interest through the lens of another. For example, do not turn a general interest in markets into "markets as they relate to defense spending". Each category stands on its own terms.
3. DEMOTION THRESHOLD. Only move a topic toward lower interest if it has at least {DEMOTE_MIN_SWIPES} total swipes AND at least {int(DEMOTE_MIN_NEGATIVE_SHARE * 100)}% of them are not-relevant. Below that bar, leave the topic exactly as the profile currently describes it.
4. Never put a topic in the "Low interest" line if it also appears as an interest earlier in the profile. The profile must not contradict itself.
5. The "Low interest" line is stable. Keep the existing entries unless the reader's swipes clearly contradict them, and only add to it under rule 3.
6. Prefer small edits. If the statistics do not clearly justify a change, return the profile essentially unchanged. Returning it unchanged is a perfectly good outcome.
7. Keep it under {MAX_PROFILE_WORDS} words, as flowing descriptive prose, ending with the "Low interest:" line.

Respond ONLY with the profile text. No preamble, no quotes, no markdown."""


def main() -> None:
    sb = supabase_client()
    client = anthropic.Anthropic()

    current = sb.table("interest_profile").select("profile_text").eq("id", 1).single().execute()
    profile_text = current.data["profile_text"]
    tag_stats = load_tag_stats(sb)
    prompt = build_prompt(profile_text, tag_stats)

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

    if new_profile == profile_text:
        print("Profile unchanged; nothing to store.")
        return

    # Archive old version, then replace.
    sb.table("profile_history").insert({"profile_text": profile_text}).execute()
    sb.table("interest_profile").update(
        {"profile_text": new_profile, "updated_at": "now()"}
    ).eq("id", 1).execute()

    print(f"Profile updated ({words} words):\n{new_profile}")
    # Surface drift: the reader should notice when the profile changes under them.
    notify(
        "Digest profile updated",
        f"{new_profile[:350]}\n\nPrevious version saved to profile_history.",
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        notify("Daily Digest profile rewrite failed", f"Error: {sys.exc_info()[1]}", priority="high")
        sys.exit(1)

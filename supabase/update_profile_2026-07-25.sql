-- Interest profile reset, 2026-07-25.
--
-- Why: the 2026-07-19 automated rewrite collapsed the profile into a defense
-- brief (it reframed markets, AI and everything else through a defense lens,
-- and produced a self-contradictory "Low interest: AI-defense intersection"
-- line from a single negative swipe). Root cause was selection bias: 4 of 10
-- RSS feeds were defense-only, so defense dominated what was served, and the
-- rewrite read that volume as preference.
--
-- Fixed alongside this: feeds rebalanced across 8 categories (pipeline/sources.py),
-- curation prompt now allocates slots by category with at most 1 defense item and
-- 1 wildcard (pipeline/digest.py), and the weekly rewrite now sees served-counts
-- and may not delete categories or demote without strong evidence
-- (pipeline/profile_rewrite.py).
--
-- Run this in the Supabase SQL editor for the running-ideas project.
-- It archives the current profile to profile_history before replacing it.

insert into profile_history (profile_text)
select profile_text from interest_profile where id = 1;

update interest_profile
set profile_text = 'General-awareness reader who wants breadth across many topics rather than depth in any single one.

Core interests, roughly equal weight: world and US national news, including politics and policy; business and the economy, especially macro conditions, Federal Reserve policy, market moves, and major company and industry news; technology products, software engineering, and AI industry developments; science, especially space and astronomy; climate and energy, including the energy transition and power markets; and fitness, running, and longevity research.

Local coverage is wanted: San Luis Obispo county and the Central Coast, plus California statewide issues such as housing, water, wildfire, and the state economy.

Defense and DoD topics are professionally relevant, particularly military logistics, command and control, USTRANSCOM, sealift, and defense enterprise software. Defense should nonetheless be a small part of each digest, appearing only for genuinely significant stories rather than routine procurement or incremental program news.

Low interest: celebrity news, spectator sports, cryptocurrency.',
    updated_at = now()
where id = 1;

select profile_text, updated_at from interest_profile where id = 1;

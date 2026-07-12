-- Daily Digest — Supabase schema
-- Run this once in the Supabase SQL editor (Dashboard > SQL Editor > New query).

create table if not exists digests (
  id uuid primary key default gen_random_uuid(),
  digest_date date unique not null,
  bottom_line text,
  created_at timestamptz default now()
);

create table if not exists digest_items (
  id uuid primary key default gen_random_uuid(),
  digest_id uuid references digests(id) on delete cascade,
  section text check (section in ('news','weather','finance')),
  position int,
  headline text not null,
  summary text not null,        -- 2-3 sentences, shown on card
  detail text,                  -- expanded paragraph, shown on tap
  sources jsonb,                -- [{title, url}]
  tags text[]                   -- 2-3 topic tags assigned by Claude
);

create table if not exists feedback (
  id uuid primary key default gen_random_uuid(),
  item_id uuid unique references digest_items(id) on delete cascade,
  verdict text check (verdict in ('relevant','not_relevant')),
  created_at timestamptz default now()
);
-- item_id is unique so a re-swipe upserts (one verdict per item, single-user app).

create table if not exists interest_profile (
  id int primary key default 1,
  profile_text text not null,
  updated_at timestamptz default now()
);

-- Audit log of profile versions replaced by the weekly rewrite job.
create table if not exists profile_history (
  id uuid primary key default gen_random_uuid(),
  profile_text text not null,
  replaced_at timestamptz default now()
);

-- Seed interest profile
insert into interest_profile (id, profile_text) values (
  1,
  'Defense and DoD technology, military logistics and C2 (USTRANSCOM, sealift, Palantir ecosystem), AI industry and AI policy, enterprise software, macro markets and Fed policy, Boeing. Low interest: celebrity news, sports, crypto.'
) on conflict (id) do nothing;

-- Row Level Security
-- The PWA uses the anon key: read digests/items/profile, read+write feedback.
-- The pipeline uses the service role key, which bypasses RLS.
alter table digests enable row level security;
alter table digest_items enable row level security;
alter table feedback enable row level security;
alter table interest_profile enable row level security;
alter table profile_history enable row level security;

create policy "anon read digests" on digests
  for select to anon using (true);

create policy "anon read digest_items" on digest_items
  for select to anon using (true);

create policy "anon read feedback" on feedback
  for select to anon using (true);

create policy "anon insert feedback" on feedback
  for insert to anon with check (true);

create policy "anon update feedback" on feedback
  for update to anon using (true) with check (true);

create policy "anon read interest_profile" on interest_profile
  for select to anon using (true);

-- profile_history: no anon policies — service role only.

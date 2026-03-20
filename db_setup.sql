-- Run this in Supabase SQL Editor (supabase.com → your project → SQL Editor)
-- Creates tables for the concert agent. Independent of Setlist app auth.

-- Lightweight user table keyed on Spotify user_id (no Auth dependency)
create table if not exists agent_users (
    spotify_user_id  text primary key,
    display_name     text,
    created_at       timestamptz default now()
);

-- Ticket link clicks (for cart abandonment follow-up)
create table if not exists clicked_events (
    id            uuid primary key default gen_random_uuid(),
    spotify_user_id text references agent_users(spotify_user_id) on delete cascade,
    event_id      text not null,
    event_name    text,
    venue         text,
    event_date    date,
    url           text,
    clicked_at    timestamptz default now(),
    purchased     boolean,
    unique (spotify_user_id, event_id)
);

-- User-confirmed show attendance
create table if not exists attended_events (
    id              uuid primary key default gen_random_uuid(),
    spotify_user_id text references agent_users(spotify_user_id) on delete cascade,
    event_name      text not null,
    venue           text,
    event_date      date,
    attended        boolean not null,
    logged_at       timestamptz default now()
);

-- Disable RLS for simplicity (anon key can read/write)
alter table agent_users    enable row level security;
alter table clicked_events enable row level security;
alter table attended_events enable row level security;

create policy "open_agent_users"     on agent_users     for all using (true) with check (true);
create policy "open_clicked_events"  on clicked_events  for all using (true) with check (true);
create policy "open_attended_events" on attended_events for all using (true) with check (true);

-- Chat history (persists across page reloads)
create table if not exists chat_messages (
    id              uuid primary key default gen_random_uuid(),
    spotify_user_id text references agent_users(spotify_user_id) on delete cascade,
    role            text not null check (role in ('user', 'assistant')),
    content         text not null,
    created_at      timestamptz default now()
);

alter table chat_messages enable row level security;
create policy "open_chat_messages" on chat_messages for all using (true) with check (true);

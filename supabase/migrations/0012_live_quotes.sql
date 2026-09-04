-- =============================================================
-- Intraday index quotes.
--
-- One row per index, overwritten on each poll: this is a "what is it doing
-- right now" table, not a history. The end-of-day series in index_prices stays
-- the record, written once from the official archive after the close, and
-- nothing in the scoring engine reads from here — a score built on a price
-- that moves under it would change meaning between two glances at the screen.
--
-- Only indices. NSE's per-stock quote endpoint returns 403 to this connection
-- and Yahoo rate-limits it, so live prices for the 500 constituents need a
-- broker API and an account to go with it.
-- =============================================================

create table if not exists n500.live_quotes (
  name          text primary key,
  last          numeric,
  change        numeric,
  pct_change    numeric,
  open          numeric,
  high          numeric,
  low           numeric,
  prev_close    numeric,
  year_high     numeric,
  year_low      numeric,
  -- The exchange's own stamp, which is what says how fresh this really is.
  as_of         text,
  fetched_at    timestamptz not null default now()
);

alter table n500.live_quotes enable row level security;
drop policy if exists "live_quotes_read" on n500.live_quotes;
create policy "live_quotes_read" on n500.live_quotes
  for select to anon, authenticated using (true);

grant select on n500.live_quotes to anon, authenticated;
grant all on n500.live_quotes to service_role;

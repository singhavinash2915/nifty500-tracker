-- Daily snapshot of the support-reversal setup, alongside scores_daily.
-- Kept separate because it carries the *reasoning* — which gate fired, which
-- confirmations printed, where the stop sits — not just a number. When the
-- screener says "watching" you want to see why without re-running the engine.

create table if not exists ts_setups (
  symbol         text not null references stocks (symbol) on delete cascade,
  date           date not null,
  ts_score       numeric,
  setup_status   text check (setup_status in ('none','watching','triggered')),
  stop_price     numeric,
  target_price   numeric,
  reward_risk    numeric,
  headroom       numeric,                     -- fraction to the next resistance
  zone_floor     numeric,
  zone_ceil      numeric,
  zone_timeframe text,
  components     jsonb not null default '{}'::jsonb,
  confirmation   jsonb,                       -- which triggers printed
  caps           jsonb not null default '[]'::jsonb,
  reason         text,                        -- why it was not scored at all
  primary key (symbol, date)
);

create index if not exists ts_setups_status_idx on ts_setups (date, setup_status);

alter table ts_setups enable row level security;
drop policy if exists "ts_setups_read" on ts_setups;
create policy "ts_setups_read" on ts_setups for select using (true);

-- The market backdrop, and the explanation of each stock's conviction rank.
--
-- "The market is down, should I trust the list?" is a question the screener
-- could not answer. market_backdrop holds one row a session: breadth, the
-- equal-weighted index's drawdown and trend, and beside them what the
-- conviction ranking earned in past months with the same backdrop. See
-- n500.scoring.regime for the definitions and the study.
create table if not exists n500.market_backdrop (
  date              date primary key,
  breadth           numeric not null,   -- share of stocks above their 200-day average
  drawdown          numeric not null,   -- equal-weighted index vs its 52-week high
  vs_200dma         numeric not null,
  return_3m         numeric not null,
  regime            text not null check (regime in ('weak', 'strong')),
  hist_months       int,
  hist_top          numeric,            -- six-month return, conviction top decile
  hist_avg          numeric,            -- six-month return, average stock
  hist_ic_positive  numeric,            -- share of months the ranking helped
  hist_episodes     int,                -- separate sell-offs behind the weak months
  study             text,
  created_at        timestamptz not null default now()
);

alter table n500.market_backdrop enable row level security;
drop policy if exists "market_backdrop_read" on n500.market_backdrop;
create policy "market_backdrop_read" on n500.market_backdrop
  for select to anon, authenticated using (true);
grant select on n500.market_backdrop to anon, authenticated;
grant all on n500.market_backdrop to service_role;

-- Why each stock ranks where it does. The top three lifting features as
-- [[feature, points], ...], points being that feature's share of the score
-- above a neutral 50, and the share of the lift that comes from signals which
-- have gone flat in weak markets (momentum, value, margin revision).
alter table n500.scores_daily add column if not exists conviction_drivers jsonb;
alter table n500.scores_daily add column if not exists conviction_fading_share numeric;

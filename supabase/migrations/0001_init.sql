-- =============================================================
-- Nifty 500 Conviction Tracker — initial schema
-- Run in Supabase Dashboard -> SQL Editor
-- =============================================================

-- ---------- universe -----------------------------------------

create table if not exists stocks (
  symbol        text primary key,                    -- NSE symbol, e.g. 'HDFCBANK'
  company_name  text not null,
  sector        text,                                -- NSE macro sector, used for peer ranking
  industry      text,
  isin          text,
  series        text,
  mcap_cr       numeric,                             -- filled in phase 2
  mcap_band     text check (mcap_band in ('large','mid','small')),
  is_active     boolean not null default true,       -- currently in the index
  first_seen_on date not null default current_date,
  last_seen_on  date not null default current_date,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists stocks_sector_idx on stocks (sector) where is_active;

-- Point-in-time index membership. Written weekly from day one so that
-- backtests a year from now can reconstruct the real universe of any past
-- date instead of scoring today's survivors across history.
create table if not exists index_membership (
  symbol     text not null references stocks (symbol) on delete cascade,
  week_start date not null,                          -- Monday of the snapshot week
  index_name text not null default 'NIFTY500',
  primary key (index_name, week_start, symbol)
);

-- ---------- prices & technicals -------------------------------

create table if not exists prices_daily (
  symbol text not null references stocks (symbol) on delete cascade,
  date   date not null,
  open   numeric, high numeric, low numeric, close numeric,
  adj_close numeric,
  volume bigint,
  primary key (symbol, date)
);

create index if not exists prices_daily_date_idx on prices_daily (date);

create table if not exists technicals_daily (
  symbol           text not null references stocks (symbol) on delete cascade,
  date             date not null,
  sma20 numeric, sma50 numeric, sma200 numeric, wma50 numeric,
  sma200_slope     numeric,
  rsi14            numeric,
  macd_hist        numeric,
  adx14            numeric,
  atr14            numeric,
  atr_pct          numeric,
  ret_1m numeric, ret_3m numeric, ret_6m numeric, ret_12m numeric,
  mom_12_1         numeric,                          -- 12m return excluding last month
  rs_vs_index      numeric,
  dist_52w_high    numeric,                          -- fraction below the 52w high
  vol_ratio_20_100 numeric,
  max_dd_6m        numeric,
  primary key (symbol, date)
);

-- ---------- fundamentals --------------------------------------

-- filed_on is separate from period_end on purpose. Scoring and backtests
-- may only read rows where filed_on <= as_of_date; using period_end would
-- trade on information nobody had yet.
create table if not exists fundamentals_q (
  symbol     text not null references stocks (symbol) on delete cascade,
  period_end date not null,
  filed_on   date,
  revenue numeric, ebitda numeric, pat numeric, eps numeric,
  opm numeric, npm numeric,
  source     text,
  fetched_at timestamptz not null default now(),
  primary key (symbol, period_end)
);

create table if not exists fundamentals_y (
  symbol   text not null references stocks (symbol) on delete cascade,
  fy       int  not null,                            -- financial year ending, e.g. 2026
  period_end date,
  filed_on date,
  revenue numeric, ebitda numeric, pat numeric, eps numeric,
  cfo numeric, capex numeric, fcf numeric,
  roe numeric, roce numeric,
  debt numeric, equity numeric, debt_equity numeric,
  interest_cover numeric,
  debtor_days numeric,
  contingent_liab numeric,
  auditor_qualified boolean,
  source text,
  fetched_at timestamptz not null default now(),
  primary key (symbol, fy)
);

create table if not exists shareholding (
  symbol       text not null references stocks (symbol) on delete cascade,
  quarter_end  date not null,
  promoter_pct numeric,
  pledge_pct   numeric,
  fii_pct      numeric,
  dii_pct      numeric,
  public_pct   numeric,
  source       text,
  fetched_at   timestamptz not null default now(),
  primary key (symbol, quarter_end)
);

create table if not exists valuations_daily (
  symbol       text not null references stocks (symbol) on delete cascade,
  date         date not null,
  pe numeric, pb numeric, ev_ebitda numeric, ev_sales numeric,
  div_yield numeric,
  pe_5y_median numeric,
  earnings_yield numeric,
  primary key (symbol, date)
);

-- ---------- support zones -------------------------------------

-- Zones are never hard-deleted. A broken zone gets invalidated_on set, so a
-- backtest can replay exactly which zones were live on any past date.
create table if not exists support_zones (
  id            bigserial primary key,
  symbol        text not null references stocks (symbol) on delete cascade,
  timeframe     text not null check (timeframe in ('daily','weekly')),
  source        text not null check (source in ('pivot','cluster','volume_shelf','fib','ma')),
  floor_price   numeric not null,
  ceil_price    numeric not null,
  formed_on     date not null,
  last_touch_on date,
  touch_count   int  not null default 0,
  avg_reaction_atr numeric,                          -- mean bounce out of the zone, in ATR
  rejection_quality numeric,                         -- wick/close quality of the touches
  volume_score  numeric,
  confluence    jsonb not null default '{}'::jsonb,  -- {"wma50":true,"fib":0.618,...}
  strength      numeric,                             -- 0-100 composite
  invalidated_on date,
  updated_at    timestamptz not null default now(),
  check (ceil_price >= floor_price)
);

create index if not exists support_zones_live_idx
  on support_zones (symbol, timeframe) where invalidated_on is null;

create table if not exists zone_events (
  id        bigserial primary key,
  zone_id   bigint not null references support_zones (id) on delete cascade,
  symbol    text not null references stocks (symbol) on delete cascade,
  date      date not null,
  event     text not null check (event in ('touch','rejection','break','reclaim')),
  reaction_atr numeric,
  volume_ratio numeric
);

create index if not exists zone_events_zone_idx on zone_events (zone_id, date);

-- ---------- scores --------------------------------------------

create table if not exists scores_daily (
  symbol        text not null references stocks (symbol) on delete cascade,
  date          date not null,
  quality_score numeric,
  value_score   numeric,
  tm_score      numeric,                             -- momentum / breakout setup
  ts_score      numeric,                             -- support reversal setup
  blended       numeric,
  winning_setup text check (winning_setup in ('momentum','support','none')),
  setup_status  text check (setup_status in ('watching','triggered','none')),
  active_zone_id bigint references support_zones (id) on delete set null,
  stop_price    numeric,
  target_price  numeric,
  reward_risk   numeric,
  sector_rank   int,
  decile        int,
  flags         jsonb not null default '[]'::jsonb,  -- red flags with reasons
  primary key (symbol, date)
);

create index if not exists scores_daily_date_blend_idx on scores_daily (date, blended desc);

-- ---------- portfolio -----------------------------------------

create table if not exists watchlist (
  id         bigserial primary key,
  symbol     text not null references stocks (symbol) on delete cascade,
  note       text,
  added_at   timestamptz not null default now(),
  unique (symbol)
);

create table if not exists positions (
  id            bigserial primary key,
  symbol        text not null references stocks (symbol) on delete cascade,
  entry_date    date not null,
  entry_price   numeric not null,
  quantity      numeric not null,
  stop_price    numeric,
  target_price  numeric,
  thesis        text,
  setup         text check (setup in ('momentum','support','other')),
  exit_date     date,
  exit_price    numeric,
  exit_reason   text check (exit_reason in ('target','stop','thesis_broken','score_decay','manual')),
  created_at    timestamptz not null default now()
);

create table if not exists alerts (
  id         bigserial primary key,
  symbol     text not null references stocks (symbol) on delete cascade,
  date       date not null default current_date,
  rule       text not null,
  message    text not null,
  payload    jsonb not null default '{}'::jsonb,
  seen       boolean not null default false,
  created_at timestamptz not null default now()
);

-- ---------- backtest ------------------------------------------

create table if not exists backtest_runs (
  id          bigserial primary key,
  label       text,
  params      jsonb not null,                        -- weights, holding period, size, setup filter
  start_date  date not null,
  end_date    date not null,
  cagr numeric, hit_rate numeric, median_return numeric,
  max_drawdown numeric, turnover numeric,
  benchmark_cagr numeric,
  created_at  timestamptz not null default now()
);

create table if not exists backtest_trades (
  id          bigserial primary key,
  run_id      bigint not null references backtest_runs (id) on delete cascade,
  symbol      text not null,
  setup       text,
  entry_date  date not null,
  entry_price numeric not null,
  exit_date   date,
  exit_price  numeric,
  return_pct  numeric,
  score_at_entry numeric,
  decile_at_entry int
);

create index if not exists backtest_trades_run_idx on backtest_trades (run_id);

-- ---------- ingestion audit -----------------------------------

-- Without this a silently broken scraper rots the data for weeks unnoticed.
create table if not exists ingestion_runs (
  id          bigserial primary key,
  job         text not null,
  started_at  timestamptz not null default now(),
  finished_at timestamptz,
  status      text not null default 'running'
              check (status in ('running','ok','partial','failed')),
  rows_written int not null default 0,
  symbols_ok   int not null default 0,
  symbols_failed int not null default 0,
  errors      jsonb not null default '[]'::jsonb,
  notes       text
);

create index if not exists ingestion_runs_job_idx on ingestion_runs (job, started_at desc);

-- ---------- RLS ------------------------------------------------
-- Ingestion uses the service-role key and bypasses RLS. The web app uses the
-- anon key and gets read-only access; nothing in the browser may write.

do $$
declare t text;
begin
  foreach t in array array[
    'stocks','index_membership','prices_daily','technicals_daily',
    'fundamentals_q','fundamentals_y','shareholding','valuations_daily',
    'support_zones','zone_events','scores_daily',
    'watchlist','positions','alerts',
    'backtest_runs','backtest_trades','ingestion_runs'
  ]
  loop
    execute format('alter table %I enable row level security', t);
    execute format(
      'drop policy if exists "%s_read" on %I', t, t);
    execute format(
      'create policy "%s_read" on %I for select using (true)', t, t);
  end loop;
end $$;

-- Watchlist and positions are edited from the browser, so they need writes.
do $$
declare t text;
begin
  foreach t in array array['watchlist','positions','alerts']
  loop
    execute format('drop policy if exists "%s_write" on %I', t, t);
    execute format(
      'create policy "%s_write" on %I for all using (true) with check (true)', t, t);
  end loop;
end $$;

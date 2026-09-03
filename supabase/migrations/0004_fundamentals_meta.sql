-- Provenance for fundamentals.
--
-- filed_on drives every point-in-time query, so whether it is a real filing
-- date or an estimate has to travel with the row. Screener publishes the
-- period but not the filing date; the estimate uses the SEBI LODR deadline
-- (45 days after a quarter, 60 after a year), which errs late and can only
-- make a backtest pessimistic.

alter table fundamentals_q add column if not exists filed_on_is_estimated boolean default true;
alter table fundamentals_y add column if not exists filed_on_is_estimated boolean default true;

-- Banks and NBFCs report a different row set and must be scored differently.
alter table stocks add column if not exists company_type text
  check (company_type in ('financial','general'));

-- Screener's shareholding table carries no pledge figure, so the
-- promoter-pledge red flag cannot be evaluated from this source. NULL here
-- means "not checked", which is different from 0 — and a check that did not
-- run must never be reported as passed.
alter table shareholding add column if not exists pledge_checked boolean default false;

-- A professionally managed company (ITC, HDFC Bank, Infosys) genuinely has no
-- promoter. That is a fact about the company, not missing data, and the
-- promoter-selling flag is not applicable rather than unknown.
alter table shareholding add column if not exists has_promoter boolean;

-- Screener appends a duration when a company changes its year-end, e.g. a
-- 15-month "Mar 2023". Flow items are annualised to a 12-month basis before
-- storage — otherwise the change of calendar reads as a growth spurt followed
-- by a collapse — and the original length is kept so the adjustment is visible.
alter table fundamentals_y add column if not exists period_months int default 12;
alter table fundamentals_q add column if not exists period_months int default 3;

-- Screener's headline ratio block, kept as one row per company. These are the
-- inputs the value score needs that the financial statements do not give
-- directly: a market cap, a book value per share, and the site's own P/E.
create table if not exists company_ratios (
  symbol         text primary key references stocks (symbol) on delete cascade,
  market_cap_cr  numeric,
  pe             numeric,
  pb             numeric,
  book_value     numeric,
  dividend_yield numeric,
  roe            numeric,
  roce           numeric,
  company_type   text,
  fetched_at     timestamptz not null default now()
);

alter table company_ratios enable row level security;
drop policy if exists "company_ratios_read" on company_ratios;
create policy "company_ratios_read" on company_ratios for select using (true);

-- Q and V, kept apart from scores_daily because they carry the red-flag
-- verdicts. `excluded` is not "scored zero": a business that fails a hard gate
-- has no score at all, and the reason travels with the row so a name you liked
-- does not simply vanish from the screener.
create table if not exists fundamental_scores (
  symbol        text not null references stocks (symbol) on delete cascade,
  date          date not null,
  quality_score numeric,
  value_score   numeric,
  excluded      boolean not null default false,
  flags         jsonb not null default '[]'::jsonb,
  primary key (symbol, date)
);

create index if not exists fundamental_scores_date_idx on fundamental_scores (date);

alter table fundamental_scores enable row level security;
drop policy if exists "fundamental_scores_read" on fundamental_scores;
create policy "fundamental_scores_read" on fundamental_scores for select using (true);

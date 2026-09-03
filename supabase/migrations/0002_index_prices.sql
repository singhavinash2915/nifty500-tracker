-- =============================================================
-- Benchmark index history, from the NSE daily index archive.
--
-- Deliberately not a row in `stocks`: a benchmark is not a constituent, and
-- putting it there would leak a phantom 501st name into every screener query
-- and every sector ranking.
-- =============================================================

create table if not exists index_prices (
  index_name text not null,                 -- 'Nifty 500'
  date       date not null,
  open numeric, high numeric, low numeric, close numeric,
  volume     bigint,
  turnover_cr numeric,
  pe numeric, pb numeric, div_yield numeric,  -- index-level valuation context
  primary key (index_name, date)
);

create index if not exists index_prices_date_idx on index_prices (date);

alter table index_prices enable row level security;
drop policy if exists "index_prices_read" on index_prices;
create policy "index_prices_read" on index_prices for select using (true);

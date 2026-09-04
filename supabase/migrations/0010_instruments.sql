-- =============================================================
-- Track instruments that are not Nifty 500 constituents.
--
-- ETFs are ordinary EQ/STK rows in the bhavcopy, so prices, technicals and
-- zones all work on them unchanged. What they do not have is financial
-- statements — an ETF has no revenue and no promoter — so the quality and
-- value pillars stay null and the blend renormalises onto the technical alone.
-- That is the correct answer rather than a gap: scoring a gold ETF on return on
-- equity would be meaningless.
--
-- `instrument_type` is what lets the fundamentals jobs skip them without
-- logging 30 failures a night, and what lets the screener show them apart from
-- the 500.
-- =============================================================

alter table n500.stocks add column if not exists instrument_type text
  not null default 'equity'
  check (instrument_type in ('equity','etf'));

create index if not exists stocks_instrument_type_idx on n500.stocks (instrument_type);

-- More than one index is now stored: the Nifty 500 is still the benchmark for
-- relative strength, and the rest are market context.
alter table n500.index_prices add column if not exists is_benchmark boolean not null default false;

update n500.index_prices set is_benchmark = true where index_name = 'Nifty 500';

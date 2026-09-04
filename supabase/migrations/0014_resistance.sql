-- =============================================================
-- Zones now come in two kinds.
--
-- The engine only ever built support, which left the target price for a setup
-- guessed from clustered swing highs rather than from a rated level. It also
-- meant the screener could say a stock was at support but never that it was
-- stalling under resistance — and the failed breakout, where price closes
-- through a level and then closes back under it within a few bars, was
-- invisible entirely.
--
-- `respect` is borrowed from the TradingView script: rejections as a share of
-- decisive tests. A level tested five times and held five times is not the
-- same as one tested five times and broken twice, though both show five
-- touches.
-- =============================================================

alter table n500.support_zones add column if not exists kind text
  not null default 'support' check (kind in ('support','resistance'));

alter table n500.support_zones add column if not exists break_count int default 0;
alter table n500.support_zones add column if not exists respect numeric;

create index if not exists support_zones_kind_idx
  on n500.support_zones (symbol, kind) where invalidated_on is null;

-- Where the setup stands relative to overhead supply.
alter table n500.ts_setups add column if not exists resistance_floor numeric;
alter table n500.ts_setups add column if not exists resistance_ceil numeric;
alter table n500.ts_setups add column if not exists resistance_strength numeric;
alter table n500.ts_setups add column if not exists false_breakout jsonb;
alter table n500.ts_setups add column if not exists rejected_at_resistance boolean default false;

-- =============================================================
-- Who is on the buy list, and since when.
--
-- The list is computed nightly rather than in the browser because hysteresis
-- needs memory: a name stays on until it falls past rank 25, which cannot be
-- decided from today's ranking alone. Storing membership also makes "on the
-- list since 12 August" possible, which is the difference between a screener
-- and something you can hold a position against.
--
-- `buylist_since` is the date the name joined, carried forward while it stays.
-- A name that leaves and returns gets a new date, because the second visit is a
-- new idea rather than a continuation of the first.
-- =============================================================

alter table n500.scores_daily add column if not exists on_buylist boolean not null default false;
alter table n500.scores_daily add column if not exists buylist_since date;
alter table n500.scores_daily add column if not exists buylist_rank int;

create index if not exists scores_daily_buylist_idx
  on n500.scores_daily (date, on_buylist) where on_buylist;

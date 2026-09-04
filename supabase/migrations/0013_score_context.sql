-- =============================================================
-- Carry the display fields on scores_daily.
--
-- The screener reads one row per symbol and wants to show the close, the
-- momentum and whether the stock is above its 200DMA beside the score. Those
-- live in prices_daily and technicals_daily, which is correct as the source of
-- truth but means the browser needs a three-table join over 494 symbols to
-- render one list — awkward through PostgREST and slow over a phone
-- connection.
--
-- They are copied here at scoring time instead. scores_daily is already a
-- daily snapshot rather than a source, so a denormalised copy cannot drift:
-- it is rewritten by the same job that computes the score beside it.
-- =============================================================

alter table n500.scores_daily add column if not exists close numeric;
alter table n500.scores_daily add column if not exists mom_12_1 numeric;
alter table n500.scores_daily add column if not exists rs_vs_index numeric;
alter table n500.scores_daily add column if not exists dist_52w_high numeric;
alter table n500.scores_daily add column if not exists rsi14 numeric;
alter table n500.scores_daily add column if not exists above_200dma boolean;

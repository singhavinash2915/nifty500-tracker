-- =============================================================
-- The inputs the validated composite needs, computed nightly.
--
-- The held-out test fitted a composite on 2023-2024 and scored it once on
-- 2025-2026: IC +0.168, t +5.67, keeping 87% of its training strength. That is
-- the only number in this project that was not measured on the data it was
-- found in, and it is built from thirteen features — of which the nightly
-- pipeline was computing eight. The other five existed only inside the
-- backtest, which is a strange place for the best evidence to live.
--
-- Four candle readings at overhead resistance, and the respect ratio of the
-- support band below. Nothing here is a new idea; they are the same functions
-- the panel used, called on the latest bar instead of a historical one.
--
-- `margin_revision` goes on fundamental_scores for the same reason: it is
-- already computed by `revision.build_metrics` and then discarded once it has
-- been folded into the revision score, which the composite does not use.
--
-- `conviction` on scores_daily is the composite itself. It sits *beside*
-- `blended` rather than replacing it — the blend ranks on quality, value and
-- momentum, which out of sample scored t +1.51, +0.12 and +1.76 respectively,
-- and replacing it silently would leave nothing to compare against.
-- =============================================================

alter table n500.ts_setups add column if not exists zone_respect numeric;
alter table n500.ts_setups add column if not exists doji_at_resistance boolean;
alter table n500.ts_setups add column if not exists hanging_man_at_resistance boolean;
alter table n500.ts_setups add column if not exists shooting_star_at_resistance boolean;
alter table n500.ts_setups add column if not exists bearish_engulfing_at_resistance boolean;

alter table n500.fundamental_scores add column if not exists margin_revision numeric;

alter table n500.scores_daily add column if not exists conviction numeric;
alter table n500.scores_daily add column if not exists conviction_decile int;

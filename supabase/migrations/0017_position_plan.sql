-- =============================================================
-- A stop and a target for every symbol, not only the ones with a setup.
--
-- `stop_price` and `target_price` on this table are the support setup's, and a
-- support setup exists for about two names on a typical day. So the answer to
-- "where does the stop go?" was null for 492 of 494 stocks, and null for every
-- position already open — which is most of the times anyone asks.
--
-- These columns carry the same question answered for everything: the stop from
-- the nearest live support zone where there is one, from volatility where there
-- is not, and the next resistance band as a target. `plan_stop_basis` says which
-- of those it was, because a stop you cannot explain is a stop you will move.
--
-- Kept separate from the setup's own columns rather than filled in behind them.
-- A setup stop is a decision the engine is making about a trade it likes; a plan
-- stop is arithmetic offered for any stock at all, and conflating the two would
-- make the screener look as though it had opinions it does not have.
-- =============================================================

alter table n500.ts_setups add column if not exists plan_stop numeric;
alter table n500.ts_setups add column if not exists plan_stop_basis text;
alter table n500.ts_setups add column if not exists plan_stop_pct numeric;
alter table n500.ts_setups add column if not exists plan_target numeric;
alter table n500.ts_setups add column if not exists plan_reward_risk numeric;
alter table n500.ts_setups add column if not exists plan_note text;

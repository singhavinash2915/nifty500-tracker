-- =============================================================
-- Two new pillars and one new gate.
--
-- R (revision) and O (ownership) answer questions the existing four do not.
-- Q, V and the two technical setups all describe a *state* — how good the
-- business is, how cheap it is, where the price sits. R describes change in the
-- earnings stream and O describes change in who owns it. The sweep's one
-- significant finding was that the state-of-the-business pillar predicted the
-- next six months negatively, which is the standard shape of a quality score
-- standing in for an expensive multiple. Change is the thing that was missing.
--
-- The liquidity gate is not a signal. It exists because every number in this
-- project is computed from closing prices on the assumption a position could be
-- opened and closed near them, and on a stock trading half a crore a day that
-- assumption is false. Without it the backtest quietly reports returns from
-- trades nobody could have made.
--
-- All columns are nullable and nothing reads them until the jobs are re-run, so
-- this applies cleanly to a populated database.
-- =============================================================

alter table n500.fundamental_scores add column if not exists revision_score numeric;
alter table n500.fundamental_scores add column if not exists ownership_score numeric;

alter table n500.technicals_daily add column if not exists turnover_60d_cr numeric;

alter table n500.scores_daily add column if not exists revision_score numeric;
alter table n500.scores_daily add column if not exists ownership_score numeric;
alter table n500.scores_daily add column if not exists turnover_60d_cr numeric;

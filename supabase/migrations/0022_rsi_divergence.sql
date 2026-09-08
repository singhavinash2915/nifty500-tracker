-- =============================================================
-- RSI divergence, shown but not scored.
--
-- Measured on the training period, none of the four earns a weight and the one
-- that clears significance points the wrong way: bullish daily divergence — the
-- classic "lower low, higher RSI, buy the reversal" — scored IC -0.019 at
-- t -2.46. The weekly variants fire on 0.2% and 0.4% of observations, which is
-- too thin to conclude anything either way.
--
-- There is a plausible reason it fails in this model specifically. The strongest
-- measured effect here is that proximity to overhead resistance is bullish:
-- failed breakouts, names pressed under levels, stocks near their highs. Bullish
-- divergence is by construction a bottom-fishing signal — it fires on stocks
-- making lower lows — so it looks for the opposite kind of stock to everything
-- else that works, and in this sample the beaten-down names kept losing.
--
-- So these columns exist to be looked at, not ranked on. Putting a t of -2.46
-- into the composite because the pattern is famous is exactly the mistake this
-- project keeps catching.
-- =============================================================

alter table n500.ts_setups add column if not exists rsi_div_bullish_daily boolean;
alter table n500.ts_setups add column if not exists rsi_div_bearish_daily boolean;
alter table n500.ts_setups add column if not exists rsi_div_bullish_weekly boolean;
alter table n500.ts_setups add column if not exists rsi_div_bearish_weekly boolean;

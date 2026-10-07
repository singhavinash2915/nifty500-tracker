-- REITs as their own instrument type.
--
-- Three REITs joined the Nifty 500 in the September 2026 rebalance (Embassy
-- Office Parks, Brookfield India, Bagmane Prime Office). They trade in series
-- RR, which the price loader skipped, so they could not be scored at all. They
-- are now priced and ranked, but on price signals only: a REIT's accounts
-- (rental income, near-total payout, units rather than shares) would be scored
-- on value and quality as though it were a manufacturer. Typing them here is
-- what keeps the fundamentals jobs away from them, as it already does for ETFs.
alter table n500.stocks drop constraint if exists stocks_instrument_type_check;
alter table n500.stocks add constraint stocks_instrument_type_check
  check (instrument_type in ('equity', 'etf', 'reit'));

update n500.stocks set instrument_type = 'reit' where series = 'RR';

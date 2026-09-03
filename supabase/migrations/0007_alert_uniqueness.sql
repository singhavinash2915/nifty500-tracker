-- =============================================================
-- One alert per symbol, per day, per rule.
--
-- `alerts` had a bigserial primary key and nothing else unique, so the upsert's
-- ON CONFLICT (symbol, date, rule) had no constraint to match and Postgres
-- rejected it outright. The dry-run never caught it, because a JSON file has no
-- constraints to violate — a class of divergence that cannot be closed by
-- making dry-run stricter, only by running against the real database.
--
-- The constraint is also the correct model. Re-running the nightly job should
-- refresh tonight's alerts, not stack a second copy of each.
-- =============================================================

delete from n500.alerts a
using n500.alerts b
where a.symbol = b.symbol
  and a.date = b.date
  and a.rule = b.rule
  and a.id < b.id;

alter table n500.alerts
  add constraint alerts_symbol_date_rule_key unique (symbol, date, rule);

-- =============================================================
-- fundamentals_y is keyed on the period, not on a derived year.
--
-- `fy` was `period_end.year`, which is not unique per company. A company that
-- changes its year-end files two periods inside one calendar year — the same
-- irregular-period case that produces Screener's "Mar 202315m" labels — and
-- both rows then claim the same (symbol, fy). PostgREST rejects the whole
-- statement with "ON CONFLICT DO UPDATE command cannot affect row a second
-- time", so a single such company loses the entire batch: 500 companies of
-- fundamentals failed to write because of a handful.
--
-- period_end is the natural key. `fy` stays as a convenience column for
-- grouping and display, with no uniqueness claimed for it.
-- =============================================================

alter table n500.fundamentals_y drop constraint if exists fundamentals_y_pkey;

delete from n500.fundamentals_y a
using n500.fundamentals_y b
where a.symbol = b.symbol
  and a.period_end = b.period_end
  and a.ctid < b.ctid;

alter table n500.fundamentals_y
  alter column period_end set not null;

alter table n500.fundamentals_y
  add constraint fundamentals_y_pkey primary key (symbol, period_end);

create index if not exists fundamentals_y_fy_idx on n500.fundamentals_y (symbol, fy);

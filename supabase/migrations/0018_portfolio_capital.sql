-- =============================================================
-- Total capital, so risk means something.
--
-- The portfolio page computed each position's risk as a share of the *tracked*
-- positions' market value. With two of seven holdings recorded, that made the
-- denominator ₹1.22 lakh instead of ₹20 lakh, and a position risking 0.33% of
-- capital was reported at 5.4% and flagged as oversized. The rupees were right
-- and the denominator was whatever happened to be in the database — which is
-- the worst kind of wrong, because it looks like an answer.
--
-- Capital cannot be derived. A tracker sees the positions it is told about, and
-- the cash beside them is invisible: here, 76% of the account. So it is stated
-- once, here, and everything risk-related divides by it.
--
-- One row, enforced by the check rather than by convention. A second row would
-- silently double the capital of whichever query read first.
--
-- Private, like positions: what somebody has to invest is not public
-- information, and this deploys to a public URL.
-- =============================================================

create table if not exists n500.portfolio (
  id             int primary key default 1,
  total_capital  numeric not null,
  -- Fraction of capital risked per position. 1% is the convention, and with a
  -- 17% hit rate for a 25% move the arithmetic only works if no single wrong
  -- call takes a large bite.
  risk_pct       numeric not null default 0.01,
  updated_at     timestamptz not null default now(),
  constraint portfolio_single_row check (id = 1)
);

alter table n500.portfolio enable row level security;

drop policy if exists "portfolio_read" on n500.portfolio;
create policy "portfolio_read" on n500.portfolio
  for select to authenticated using (true);

drop policy if exists "portfolio_write" on n500.portfolio;
create policy "portfolio_write" on n500.portfolio
  for all to authenticated using (true) with check (true);

grant select, insert, update, delete on n500.portfolio to authenticated;
grant all on n500.portfolio to service_role;

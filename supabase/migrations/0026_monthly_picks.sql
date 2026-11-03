-- The month's picks, locked on its first scored session.
--
-- The buy list moves nightly by design. A plan to buy three stocks a month
-- needs an answer that stays put, so these are chosen once and never rewritten.
-- Kept for good, 36 rows a year: this is also the record of how the rule did.
--
-- Private, like positions. Held stocks are skipped when choosing and the budgets
-- come from capital, so the rows reveal both.
create table if not exists n500.monthly_picks (
  month            date not null,       -- first day of the month
  rank             int not null,
  symbol           text not null references n500.stocks (symbol),
  sector           text,
  as_of            date not null,       -- the session the picks were taken from
  conviction       numeric not null,
  close            numeric not null,    -- at as_of
  stop             numeric not null,
  stop_basis       text,
  target           numeric,
  -- Per-position limits at the lock: capital and the risk budget spread over
  -- every position this plan can hold at once. Shares are worked out from these
  -- at the latest price, because what you pay is not the locked close.
  position_budget  numeric,
  risk_budget      numeric,
  created_at       timestamptz not null default now(),
  primary key (month, rank),
  unique (month, symbol)
);

alter table n500.monthly_picks enable row level security;
revoke all on n500.monthly_picks from anon;
drop policy if exists "monthly_picks_read" on n500.monthly_picks;
create policy "monthly_picks_read" on n500.monthly_picks
  for select to authenticated using (true);
grant select on n500.monthly_picks to authenticated;
grant all on n500.monthly_picks to service_role;

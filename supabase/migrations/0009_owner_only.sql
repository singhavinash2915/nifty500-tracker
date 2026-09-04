-- =============================================================
-- Narrow "any signed-in user" to "the owner".
--
-- Requiring authentication was not enough. This Supabase project is shared with
-- another application and has open signups, so anybody could register an
-- account against it and immediately satisfy `to authenticated using (true)` —
-- which, behind a public GitHub Pages URL, is a hole with a front door.
--
-- Disabling signups would have been the obvious fix and the wrong one: the
-- setting is project-wide and would break the other application's own sign-up
-- flow. Scoping the policies to an email address is local to this schema and
-- touches nothing shared.
--
-- The address lives in a settings table rather than inlined into eighteen
-- policies, so changing it later is one UPDATE.
-- =============================================================

create table if not exists n500.owners (
  email text primary key,
  added_at timestamptz not null default now()
);

alter table n500.owners enable row level security;
revoke all on n500.owners from anon, authenticated;

insert into n500.owners (email) values ('singhavinash409@gmail.com')
on conflict (email) do nothing;

create or replace function n500.is_owner() returns boolean
language sql stable security definer set search_path = n500, public as $$
  select exists (
    select 1 from n500.owners
    where lower(email) = lower(coalesce(auth.jwt() ->> 'email', ''))
  );
$$;

grant execute on function n500.is_owner() to authenticated;

do $$
declare t text;
begin
  foreach t in array array[
    'stocks','index_membership','prices_daily','technicals_daily',
    'fundamentals_q','fundamentals_y','shareholding','valuations_daily',
    'company_ratios','index_prices','support_zones','zone_events',
    'scores_daily','ts_setups','fundamental_scores',
    'backtest_runs','backtest_trades','ingestion_runs'
  ]
  loop
    execute format('drop policy if exists %I on n500.%I', t || '_read', t);
    execute format(
      'create policy %I on n500.%I for select to authenticated '
      'using (n500.is_owner())', t || '_read', t
    );
  end loop;

  foreach t in array array['watchlist','positions','alerts']
  loop
    execute format('drop policy if exists %I on n500.%I', t || '_read', t);
    execute format('drop policy if exists %I on n500.%I', t || '_write', t);
    execute format(
      'create policy %I on n500.%I for select to authenticated '
      'using (n500.is_owner())', t || '_read', t
    );
    execute format(
      'create policy %I on n500.%I for all to authenticated '
      'using (n500.is_owner()) with check (n500.is_owner())', t || '_write', t
    );
  end loop;
end $$;

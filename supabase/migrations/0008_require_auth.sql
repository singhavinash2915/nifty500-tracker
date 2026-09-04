-- =============================================================
-- Require a signed-in user for everything.
--
-- Until now every policy was `using (true)`, which was fine for a tool running
-- on one laptop and is not fine behind a public URL. Vite inlines the anon key
-- into the JavaScript bundle — that is normal and expected for Supabase, but it
-- only works when RLS is doing real work. With `using (true)` anyone who opened
-- the page could read, alter or delete the positions table.
--
-- The boundary drawn here is authentication, not per-row ownership: this is a
-- single-account tool, so "is there a logged-in user" is the whole question and
-- a user_id column on every table would be ceremony. If it ever serves more
-- than one person, that changes — the policies below would need to compare
-- against auth.uid() and the tables would need an owner column.
--
-- Ingestion is unaffected. It connects with the service-role key, which
-- bypasses RLS entirely.
-- =============================================================

-- Reference and market data: readable once signed in, never writable from a
-- browser. Everything here is produced by the nightly pipeline.
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
      'create policy %I on n500.%I for select to authenticated using (true)',
      t || '_read', t
    );
    execute format('revoke all on n500.%I from anon', t);
  end loop;
end $$;

-- The user's own working set. Still authenticated-only; a single account means
-- there is nobody else's row to protect it from.
do $$
declare t text;
begin
  foreach t in array array['watchlist','positions','alerts']
  loop
    execute format('drop policy if exists %I on n500.%I', t || '_read', t);
    execute format('drop policy if exists %I on n500.%I', t || '_write', t);
    execute format(
      'create policy %I on n500.%I for select to authenticated using (true)',
      t || '_read', t
    );
    execute format(
      'create policy %I on n500.%I for all to authenticated '
      'using (true) with check (true)',
      t || '_write', t
    );
    execute format('revoke all on n500.%I from anon', t);
  end loop;
end $$;

-- Grants follow the same shape. A policy alone is not enough: PostgREST needs
-- the table privilege as well, and revoking anon's is the belt to the policy's
-- braces.
revoke all on all tables in schema n500 from anon;
revoke usage on schema n500 from anon;

grant usage on schema n500 to authenticated;
grant select on all tables in schema n500 to authenticated;
grant insert, update, delete on n500.watchlist to authenticated;
grant insert, update, delete on n500.positions to authenticated;
grant insert, update, delete on n500.alerts    to authenticated;
grant usage, select on all sequences in schema n500 to authenticated;

alter default privileges in schema n500 grant select on tables to authenticated;

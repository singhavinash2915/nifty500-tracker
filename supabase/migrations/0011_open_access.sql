-- =============================================================
-- Open the tracker to anonymous access.
--
-- Reverses 0008 and 0009 at the owner's explicit request. The magic-link flow
-- was costing more attempts than it was worth: Supabase's built-in mailer caps
-- at two emails an hour, which is a hard wall when a link opens in the wrong
-- browser and you need another one.
--
-- What this means, plainly: anyone with the URL can read every table and can
-- insert, update or delete rows in watchlist, positions and alerts. The market
-- data is public information anyway; the positions table is not, and it is the
-- one to think about before recording real holdings.
--
-- To reverse: re-run 0008 and 0009.
-- =============================================================

grant usage on schema n500 to anon;
grant select on all tables in schema n500 to anon;
grant insert, update, delete on n500.watchlist to anon;
grant insert, update, delete on n500.positions to anon;
grant insert, update, delete on n500.alerts    to anon;
grant usage, select on all sequences in schema n500 to anon;
alter default privileges in schema n500 grant select on tables to anon;

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
      'create policy %I on n500.%I for select to anon, authenticated using (true)',
      t || '_read', t
    );
  end loop;

  foreach t in array array['watchlist','positions','alerts']
  loop
    execute format('drop policy if exists %I on n500.%I', t || '_read', t);
    execute format('drop policy if exists %I on n500.%I', t || '_write', t);
    execute format(
      'create policy %I on n500.%I for select to anon, authenticated using (true)',
      t || '_read', t
    );
    execute format(
      'create policy %I on n500.%I for all to anon, authenticated '
      'using (true) with check (true)', t || '_write', t
    );
  end loop;
end $$;

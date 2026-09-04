-- =============================================================
-- Make the holdings private again, without putting the screener behind a login.
--
-- 0011 opened everything to the anon key so the app could be shared without a
-- sign-in. That was the right call for market data — prices and scores are
-- public information — and the wrong one for `positions`, which says what
-- somebody actually owns, at what price, with what stop.
--
-- Why this has to happen in the database and not in the app: the anon key is
-- compiled into the JavaScript bundle and is meant to be public. Anyone who
-- opens the site can read it out of the bundle and query PostgREST directly.
-- A password prompt in the UI would not stop that for a second — it would hide
-- the holdings from someone who clicks a link, and not from anyone who looked.
-- The grant is the only thing that actually withholds a row.
--
-- Three tables carry position information. `positions` is the obvious one;
-- `watchlist` says what is being considered, which is nearly as telling; and
-- `alerts` is generated *from* the holdings, so "crossed below your stop today"
-- names both the stock and the fact that it is owned.
--
-- Everything else stays exactly as open as it was.
-- =============================================================

revoke select, insert, update, delete on n500.positions from anon;
revoke select, insert, update, delete on n500.watchlist from anon;
revoke select, insert, update, delete on n500.alerts    from anon;

-- Future tables should not inherit anonymous read. Market-data tables get it
-- explicitly above; anything new is private until somebody says otherwise,
-- which is the safer direction for the default to point.
alter default privileges in schema n500 revoke select on tables from anon;

grant select, insert, update, delete on n500.positions to authenticated;
grant select, insert, update, delete on n500.watchlist to authenticated;
grant select, insert, update, delete on n500.alerts    to authenticated;
grant usage, select on all sequences in schema n500 to authenticated;

do $$
declare t text;
begin
  foreach t in array array['positions','watchlist','alerts']
  loop
    execute format('drop policy if exists %I on n500.%I', t || '_read', t);
    execute format('drop policy if exists %I on n500.%I', t || '_write', t);
    -- `to authenticated` is what does the work: a policy written `using (true)`
    -- for anon would still hand the rows over.
    execute format(
      'create policy %I on n500.%I for select to authenticated using (true)',
      t || '_read', t
    );
    execute format(
      'create policy %I on n500.%I for all to authenticated '
      'using (true) with check (true)', t || '_write', t
    );
  end loop;
end $$;

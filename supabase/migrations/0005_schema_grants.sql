-- =============================================================
-- Role grants for the n500 schema.
--
-- Supabase wires these up automatically for `public`; a schema created by hand
-- gets nothing, so PostgREST authenticates fine and then reports "permission
-- denied for schema n500".
--
-- The privileges are deliberately narrower than the usual grant-everything.
-- The browser must not be able to write anything except its own watchlist,
-- positions and alerts, and that rule is worth enforcing twice: once here at
-- the grant level and once in the RLS policies. Either alone would hold, but a
-- policy edited carelessly in the dashboard is a plausible mistake, and a
-- missing GRANT is a much harder one to make by accident.
-- =============================================================

grant usage on schema n500 to anon, authenticated, service_role;

-- Ingestion runs as service_role and needs everything.
grant all on all tables    in schema n500 to service_role;
grant all on all sequences in schema n500 to service_role;

-- The browser reads.
grant select on all tables in schema n500 to anon, authenticated;

-- ...and writes only what belongs to the user.
grant insert, update, delete on n500.watchlist to anon, authenticated;
grant insert, update, delete on n500.positions to anon, authenticated;
grant insert, update, delete on n500.alerts    to anon, authenticated;
grant usage, select on all sequences in schema n500 to anon, authenticated;

-- Tables added by a later migration inherit the same shape.
alter default privileges in schema n500
  grant all on tables to service_role;
alter default privileges in schema n500
  grant all on sequences to service_role;
alter default privileges in schema n500
  grant select on tables to anon, authenticated;

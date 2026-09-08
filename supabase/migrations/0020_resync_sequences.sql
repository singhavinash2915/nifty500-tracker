-- =============================================================
-- Put every identity sequence back in step with its table.
--
-- Adding a holding from the browser failed with
--
--     duplicate key value violates unique constraint "positions_pkey"
--
-- because `positions` held ids 1..7 while its sequence sat at 3. The CLI
-- computed ids itself — max(existing) + 1 — and inserted them explicitly, which
-- never advances the sequence. Every row it wrote widened the gap. The browser
-- omits the id, as it should, so Postgres asked the sequence, got 4, and hit a
-- row that was already there.
--
-- The CLI is fixed to stop assigning ids at all. This repairs the damage, and
-- does it for every table with an identity column rather than only the one that
-- failed: the same trap is set wherever anything writes an explicit id, and it
-- stays silent until the day something else inserts without one.
--
-- setval with is_called = true means "the next value is one past this", so
-- passing max(id) is right and passing max(id) + 1 would skip an id. On an
-- empty table max() is null, hence the coalesce to 0 with is_called false, so
-- the first insert gets 1.
-- =============================================================

do $$
declare
  seq  text;
  tbl  text;
  col  text;
  next bigint;
begin
  for tbl, col in
    select c.table_name, c.column_name
    from information_schema.columns c
    where c.table_schema = 'n500'
      and c.column_default like 'nextval%'
  loop
    seq := pg_get_serial_sequence('n500.' || quote_ident(tbl), col);
    if seq is null then
      continue;
    end if;

    execute format('select coalesce(max(%I), 0) from n500.%I', col, tbl) into next;
    perform setval(seq, greatest(next, 1), next > 0);
    raise notice 'n500.% .% -> %', tbl, col, next;
  end loop;
end $$;

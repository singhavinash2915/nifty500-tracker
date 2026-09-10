#!/bin/bash
#
# Full logical backup of the vitalsync database.
#
# Written when the project was restricted with 402 exceed_egress_quota. That
# restriction is applied at the API gateway: PostgREST refuses every request,
# but port 5432 stays open, so pg_dump still works and is the only way to get a
# complete copy — schema, data, policies, functions and all.
#
# The password is prompted for by pg_dump itself (-W). It is never passed as an
# argument, never placed in the environment, and never written to a file, so it
# cannot end up in shell history or a process listing.
#
set -euo pipefail

PG_DUMP="${PG_DUMP:-/opt/homebrew/opt/libpq/bin/pg_dump}"
REF="${SUPABASE_REF:-vbyhumvshwsvbjtpwrmx}"
SCHEMA="${SUPABASE_SCHEMA:-n500}"
OUT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/data/backup/$(date +%Y-%m-%d)"
OUT="$OUT_DIR/vitalsync-full.dump"

mkdir -p "$OUT_DIR"

echo "Dumping schema '$SCHEMA' from $REF"
echo "Password prompt is pg_dump's own — nothing here reads or stores it."
echo

"$PG_DUMP" \
  -h "db.$REF.supabase.co" -p 5432 -U postgres -d postgres \
  -n "$SCHEMA" -Fc -W \
  -f "$OUT"

echo
echo "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
echo
echo "Verify it lists the tables you expect before deleting anything:"
echo "  ${PG_DUMP%pg_dump}pg_restore -l \"$OUT\" | grep 'TABLE DATA'"

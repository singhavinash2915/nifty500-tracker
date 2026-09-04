"""Supabase access plus the ingestion-run audit trail.

Every job opens a RunLogger. If a job dies halfway, its row stays at status
'running' with a finished_at of null, which is the signal that something broke
silently rather than the data simply being stale.

When Supabase credentials are absent the module runs in dry-run mode: writes
are serialised to data/dryrun/<table>.json instead. That keeps the whole
pipeline runnable and testable before the Supabase project exists.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from .config import DATA_DIR, settings

DRYRUN_DIR = DATA_DIR / "dryrun"


# Stable sort keys for paginated reads. PostgREST's `range()` is OFFSET/LIMIT,
# and OFFSET without ORDER BY has no defined row order: Postgres may return a
# row on two pages or on none. Reading 311,232 price rows that way lost a
# different, silent subset every time — the scored universe came out at 317,
# then 310, then 295 from identical data, and nothing reported an error.
#
# Every paginated read is ordered by something unique.
PAGE_ORDER = {
    "stocks": ("symbol",),
    "company_ratios": ("symbol",),
    "index_membership": ("index_name", "week_start", "symbol"),
    "prices_daily": ("symbol", "date"),
    "technicals_daily": ("symbol", "date"),
    "valuations_daily": ("symbol", "date"),
    "scores_daily": ("symbol", "date"),
    "ts_setups": ("symbol", "date"),
    "fundamental_scores": ("symbol", "date"),
    "fundamentals_q": ("symbol", "period_end"),
    "fundamentals_y": ("symbol", "period_end"),
    "shareholding": ("symbol", "quarter_end"),
    "index_prices": ("index_name", "date"),
    "support_zones": ("id",),
    "zone_events": ("id",),
    "watchlist": ("id",),
    "positions": ("id",),
    "alerts": ("id",),
    "ingestion_runs": ("id",),
    "backtest_runs": ("id",),
    "backtest_trades": ("id",),
    "live_quotes": ("name",),
    "portfolio": ("id",),
    "owners": ("email",),
}


def seek_filter(order_by: tuple[str, ...], cursor: dict[str, Any]) -> str:
    """The PostgREST `or` expression for "sorted strictly after `cursor`".

    Keyset paging, written out longhand because PostgREST has no row-value
    comparison. For a key of (a, b, c) the condition is

        a > A  or  (a = A and b > B)  or  (a = A and b = B and c > C)

    which is the lexicographic ordering the query is already sorted by. Any
    number of columns works, which matters because the keys in PAGE_ORDER run
    from one column to three.
    """
    clauses = []
    for depth, column in enumerate(order_by):
        equals = [f"{c}.eq.{cursor[c]}" for c in order_by[:depth]]
        greater = f"{column}.gt.{cursor[column]}"
        clauses.append(f"and({','.join([*equals, greater])})" if equals else greater)
    return ",".join(clauses)


def _seek_past(query, order_by: tuple[str, ...], cursor: dict[str, Any]):
    """Restrict a query to rows sorted after `cursor`."""
    if len(order_by) == 1:
        column = order_by[0]
        return query.gt(column, cursor[column])
    return query.or_(seek_filter(order_by, cursor))


def _collapse_duplicates(
    rows: Sequence[dict[str, Any]], on_conflict: str
) -> list[dict[str, Any]]:
    """Keep the last row per conflict key, preserving order.

    Postgres refuses an INSERT ... ON CONFLICT that proposes the same key twice
    in one statement — "cannot affect row a second time" — and rejects the
    entire batch rather than the offending pair. One company with two rows for
    the same key therefore cost all 500 companies' fundamentals.

    Collapsing here means a duplicate is a fact about the data to be noticed,
    not an outage.
    """
    keys = [k.strip() for k in on_conflict.split(",") if k.strip()]
    if not keys:
        return list(rows)

    merged: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        merged[tuple(str(row.get(k)) for k in keys)] = row
    return list(merged.values())


class Db:
    """Thin wrapper over supabase-py with a dry-run fallback."""

    def __init__(self, *, force_dry_run: bool = False) -> None:
        self.dry_run = force_dry_run or not settings.has_supabase
        self._client = None
        if not self.dry_run:
            from supabase import create_client
            from supabase.client import ClientOptions

            self._client = create_client(
                settings.supabase_url,
                settings.supabase_service_key,
                # Everything the tracker writes lives in its own schema; see
                # config.DB_SCHEMA for why.
                options=ClientOptions(schema=settings.db_schema),
            )

    # -- writes ------------------------------------------------------

    def upsert(
        self,
        table: str,
        rows: Sequence[dict[str, Any]],
        *,
        on_conflict: str | None = None,
        chunk_size: int = 500,
    ) -> int:
        """Upsert rows in chunks. Returns the number of rows sent."""
        rows = list(rows)
        if not rows:
            return 0

        if on_conflict:
            rows = _collapse_duplicates(rows, on_conflict)

        if self.dry_run:
            self._dry_run_upsert(table, rows, on_conflict=on_conflict)
            return len(rows)

        for start in range(0, len(rows), chunk_size):
            chunk = rows[start : start + chunk_size]
            query = self._client.table(table).upsert(chunk, on_conflict=on_conflict)
            query.execute()
        return len(rows)

    def _dry_run_upsert(
        self, table: str, rows: list[dict[str, Any]], *, on_conflict: str | None
    ) -> None:
        """Mirror a real upsert: replace the matching row, keep the others.

        Two lessons are baked into this. Overwriting the whole file was silent
        data loss — a job writing three `stocks` rows replaced the 500-row
        universe and the next job reported success on three symbols. But
        *merging fields within a row* was wrong in the other direction:
        PostgREST replaces the entire row, so a partial write nulls every
        column it omits. Dry-run merged them instead and hid a NOT NULL
        violation that only appeared against the real database.

        A rehearsal that is kinder than the performance is not a rehearsal.
        """
        DRYRUN_DIR.mkdir(parents=True, exist_ok=True)
        path = DRYRUN_DIR / f"{table}.json"

        existing: list[dict[str, Any]] = []
        if path.exists():
            try:
                loaded = json.loads(path.read_text())
                if isinstance(loaded, list):
                    existing = loaded
            except json.JSONDecodeError:
                existing = []

        keys = [k.strip() for k in on_conflict.split(",")] if on_conflict else None
        if not keys:
            merged = existing + rows
        else:
            def identity(row: dict[str, Any]) -> tuple:
                return tuple(str(row.get(k)) for k in keys)

            index = {identity(row): row for row in existing}
            for row in rows:
                # Replace, never merge — this is what the database does.
                index[identity(row)] = dict(row)
            merged = list(index.values())

        path.write_text(json.dumps(merged, indent=2, default=str))

    def replace(self, table: str, rows: Sequence[dict[str, Any]], *, key: str) -> int:
        """Rewrite a wholly derived table, discarding what was there before.

        Support zones are recomputed from scratch on every run and have no
        natural key — the primary key is a bigserial. Upserting them without a
        conflict target therefore *appends*: the zone count climbed from 9,045
        to 11,841 across two runs, and the stale rows included bands from
        before a clustering fix, so the chart drew a "support zone" spanning
        16% of price that the current engine would never produce.

        Deriving a table means replacing it, not adding to it.
        """
        rows = list(rows)
        if self.dry_run:
            DRYRUN_DIR.mkdir(parents=True, exist_ok=True)
            (DRYRUN_DIR / f"{table}.json").write_text(
                json.dumps(rows, indent=2, default=str)
            )
            return len(rows)

        touched = sorted({row[key] for row in rows if row.get(key) is not None})
        for start in range(0, len(touched), 200):
            chunk = touched[start : start + 200]
            self._client.table(table).delete().in_(key, chunk).execute()

        for start in range(0, len(rows), 500):
            self._client.table(table).insert(rows[start : start + 500]).execute()
        return len(rows)

    def update_where_in(
        self, table: str, values: dict[str, Any], *, column: str, matches: Sequence[Any]
    ) -> int:
        """Set a few columns on many rows, without touching the rest.

        An upsert cannot do this: PostgREST sends the whole row, so every
        column left out is written as NULL. Recording `company_type` on 500
        stocks that way nulled `company_name` and the write was rejected. An
        UPDATE changes only what it names.
        """
        matches = [m for m in matches if m is not None]
        if not matches:
            return 0

        if self.dry_run:
            path = DRYRUN_DIR / f"{table}.json"
            if not path.exists():
                return 0
            existing = json.loads(path.read_text())
            wanted = set(matches)
            touched = 0
            for row in existing:
                if row.get(column) in wanted:
                    row.update(values)
                    touched += 1
            path.write_text(json.dumps(existing, indent=2, default=str))
            return touched

        for start in range(0, len(matches), 200):
            chunk = matches[start : start + 200]
            self._client.table(table).update(values).in_(column, chunk).execute()
        return len(matches)

    def update(self, table: str, values: dict[str, Any], *, where: dict[str, Any]) -> None:
        if self.dry_run:
            return
        query = self._client.table(table).update(values)
        for column, value in where.items():
            query = query.eq(column, value)
        query.execute()

    # -- reads -------------------------------------------------------

    def select(
        self,
        table: str,
        columns: str = "*",
        *,
        where: dict[str, Any] | None = None,
        since: tuple[str, Any] | None = None,
        page_size: int = 1000,
    ) -> list[dict[str, Any]]:
        """Read a whole table, paging through PostgREST's row cap.

        PostgREST applies `max_rows` to every request — 1000 on this project —
        and returns the first page with no error and no indication that more
        exists. A single unpaged read therefore returned 1000 of 310,414 price
        rows, `compute_technicals` processed two symbols instead of 486, and
        every downstream job reported success on a fiftieth of the data. Silent
        truncation is the worst shape a bug can take, so reads page until a
        short page proves the end.

        Paging is by key rather than by offset. OFFSET makes the database walk
        every row it skips, so page 700 of the price table costs seven hundred
        thousand rows of work to return a thousand — which is how
        `export_snapshot` came to be the one step of ten that failed on a
        statement timeout once the table passed three quarters of a million
        rows. Seeking past the last key read is flat regardless of depth, and
        `PAGE_ORDER` already guarantees the key is unique.

        `since` is a `(column, value)` pair applied as `>=`. Still worth using
        where a job only wants recent rows: fewer requests beats faster ones.
        """
        if self.dry_run:
            path = DRYRUN_DIR / f"{table}.json"
            rows = json.loads(path.read_text()) if path.exists() else []
            if since is not None:
                column, value = since
                rows = [r for r in rows if str(r.get(column, "")) >= str(value)]
            return rows

        order_by = PAGE_ORDER.get(table)
        if order_by is None:
            raise KeyError(
                f"no stable page order defined for {table!r}. Paging without one "
                "silently loses rows; add it to PAGE_ORDER."
            )

        rows: list[dict[str, Any]] = []
        cursor: dict[str, Any] | None = None

        while True:
            query = self._client.table(table).select(columns)
            for column, value in (where or {}).items():
                query = query.eq(column, value)
            if since is not None:
                query = query.gte(since[0], since[1])
            for column in order_by:
                query = query.order(column)
            if cursor is not None:
                query = _seek_past(query, order_by, cursor)

            page = query.limit(page_size).execute().data
            rows.extend(page)
            if len(page) < page_size:
                return rows
            cursor = {column: page[-1][column] for column in order_by}

    # -- run audit ---------------------------------------------------

    def start_run(self, job: str) -> int | None:
        if self.dry_run:
            return None
        result = (
            self._client.table("ingestion_runs")
            .insert({"job": job, "status": "running"})
            .execute()
        )
        return result.data[0]["id"]

    def finish_run(self, run_id: int | None, **fields: Any) -> None:
        if self.dry_run or run_id is None:
            return
        fields["finished_at"] = datetime.now(timezone.utc).isoformat()
        self._client.table("ingestion_runs").update(fields).eq("id", run_id).execute()


class RunLogger:
    """Collects per-job counters and errors, and closes out ingestion_runs."""

    def __init__(self, db: Db, job: str) -> None:
        self.db = db
        self.job = job
        self.run_id: int | None = None
        self.rows_written = 0
        self.symbols_ok = 0
        self.symbols_failed = 0
        self.errors: list[dict[str, str]] = []
        self.notes: str | None = None

    def error(self, symbol: str, message: str) -> None:
        self.symbols_failed += 1
        self.errors.append({"symbol": symbol, "error": message[:500]})

    @property
    def status(self) -> str:
        if self.symbols_failed and not self.symbols_ok:
            return "failed"
        if self.symbols_failed:
            return "partial"
        return "ok"


@contextmanager
def run(job: str, *, db: Db | None = None) -> Iterator[RunLogger]:
    """Wrap a job so that it always writes an ingestion_runs row."""
    db = db or Db()
    logger = RunLogger(db, job)
    logger.run_id = db.start_run(job)
    try:
        yield logger
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised
        db.finish_run(
            logger.run_id,
            status="failed",
            rows_written=logger.rows_written,
            symbols_ok=logger.symbols_ok,
            symbols_failed=logger.symbols_failed,
            errors=logger.errors + [{"symbol": "*", "error": repr(exc)[:500]}],
            notes=logger.notes,
        )
        raise
    else:
        db.finish_run(
            logger.run_id,
            status=logger.status,
            rows_written=logger.rows_written,
            symbols_ok=logger.symbols_ok,
            symbols_failed=logger.symbols_failed,
            errors=logger.errors,
            notes=logger.notes,
        )

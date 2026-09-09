"""A local mirror of `prices_daily`, so the pipeline stops re-downloading it.

The problem this solves is billing, not speed. Three jobs each read the whole
price table every night — `compute_technicals`, `compute_zones` and
`compute_fundamental_scores` — and the backtest reads it again on every panel
rebuild. At roughly 250MB a pass over 778,000 rows that is about 760MB a night,
16GB a month, against a free-tier egress allowance of 5GB. The allowance was
exhausted and the project started serving 402s.

Nothing about the data justifies the traffic. Prices are append-only: a session
adds one bar per symbol, so a night of new data is about 750 rows, and the other
777,000 are re-sent unchanged. This keeps them on disk and asks the database only
for what is new.

Why SQLite
----------
Stdlib, so it adds no dependency to a 954MB server that had to be given swap to
survive the old pipeline. It also indexes by symbol, which matters more than the
egress saving on that box: `frame(symbol)` reads one company's bars without
materialising the other 756, the same per-symbol discipline that took
`compute_technicals` from a peak of 1.4GB to 133MB.

Correctness over cleverness
---------------------------
A cache that silently drifts from its source is worse than no cache — every
downstream number would be wrong and nothing would say so. Two guards:

  * every sync re-reads an overlapping window rather than starting after the
    last cached date, so a bar that was revised after we first stored it (a
    late adjustment, a corrected close) is picked up rather than frozen;
  * every sync compares the local row count with `db.count`, which is one
    request that transfers no rows, and rebuilds from scratch if they disagree.
    Deletions upstream — the technicals truncate, a purge of delisted names —
    are invisible to an append-only merge, and this is what notices.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

__all__ = ["path", "sync", "frame", "load", "symbols", "row_count"]

TABLE = "prices_daily"
COLUMNS = ("symbol", "date", "open", "high", "low", "close", "adj_close", "volume")

# How far back to re-read on every sync. Cheap insurance: a week of bars for the
# whole universe is a few thousand rows, and it is the difference between a
# revised close being corrected and being wrong forever.
OVERLAP_DAYS = 7


def path() -> Path:
    """Where the mirror lives. Overridable, because the box and the laptop differ."""
    env = os.environ.get("N500_PRICE_CACHE")
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[2] / "data" / "cache" / "prices.sqlite"


def _connect() -> sqlite3.Connection:
    target = path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target)
    conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {TABLE} (
              symbol TEXT NOT NULL,
              date   TEXT NOT NULL,
              open REAL, high REAL, low REAL, close REAL, adj_close REAL, volume REAL,
              PRIMARY KEY (symbol, date)
            )"""
    )
    conn.execute(f"CREATE INDEX IF NOT EXISTS {TABLE}_date ON {TABLE}(date)")
    return conn


def row_count() -> int:
    with closing(_connect()) as conn:
        return int(conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0])


def _max_date(conn: sqlite3.Connection) -> date | None:
    value = conn.execute(f"SELECT max(date) FROM {TABLE}").fetchone()[0]
    return date.fromisoformat(value) if value else None


def _write(conn: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    placeholders = ",".join("?" * len(COLUMNS))
    conn.executemany(
        f"INSERT OR REPLACE INTO {TABLE} ({','.join(COLUMNS)}) VALUES ({placeholders})",
        [tuple(r.get(c) for c in COLUMNS) for r in rows],
    )
    conn.commit()
    return len(rows)


def sync(db, *, overlap_days: int = OVERLAP_DAYS, verbose: bool = True) -> int:
    """Bring the mirror level with the database. Returns rows fetched.

    The first call pays for a full copy. Every call after it fetches the
    overlap window and nothing else, which is where the whole saving is.
    """
    if getattr(db, "dry_run", False):
        # Dry runs read fixtures, which are already local and already cheap.
        return 0

    with closing(_connect()) as conn:
        since = _max_date(conn)
        if since is None:
            fetched = _write(conn, db.select(TABLE, ",".join(COLUMNS)))
            reason = "first build"
        else:
            window = since - timedelta(days=overlap_days)
            fetched = _write(
                conn,
                db.select(TABLE, ",".join(COLUMNS), since=("date", window.isoformat())),
            )
            reason = f"since {window}"

        local = int(conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0])

    remote = db.count(TABLE)
    if local != remote:
        # Rows vanished upstream, or an earlier sync was interrupted part-way.
        # An append-only merge cannot repair either, so start again.
        if verbose:
            print(f"[pricecache] {local} local vs {remote} remote — rebuilding")
        with closing(_connect()) as conn:
            conn.execute(f"DELETE FROM {TABLE}")
            conn.commit()
            fetched += _write(conn, db.select(TABLE, ",".join(COLUMNS)))
        reason += ", rebuilt"

    if verbose:
        print(f"[pricecache] {fetched} rows fetched ({reason}), {row_count()} cached")
    return fetched


def symbols() -> list[str]:
    with closing(_connect()) as conn:
        return [r[0] for r in conn.execute(f"SELECT DISTINCT symbol FROM {TABLE} ORDER BY symbol")]


def frame(symbol: str, *, tail: int | None = None) -> pd.DataFrame:
    """One symbol's bars, oldest first, in the shape `db.select` would return.

    `tail` bounds the read for callers that only need recent history. It is a
    row limit rather than a date filter on purpose — a symbol that stopped
    trading should still return its last N bars rather than nothing.
    """
    with closing(_connect()) as conn:
        if tail:
            rows = conn.execute(
                f"SELECT {','.join(COLUMNS)} FROM {TABLE} WHERE symbol = ? "
                "ORDER BY date DESC LIMIT ?",
                (symbol, int(tail)),
            ).fetchall()
            rows = rows[::-1]
        else:
            rows = conn.execute(
                f"SELECT {','.join(COLUMNS)} FROM {TABLE} WHERE symbol = ? ORDER BY date",
                (symbol,),
            ).fetchall()
    return pd.DataFrame(rows, columns=list(COLUMNS))


def load(columns: str = "*", *, since: str | None = None) -> pd.DataFrame:
    """The whole mirror, for callers that genuinely want every symbol at once."""
    cols = list(COLUMNS) if columns == "*" else [c.strip() for c in columns.split(",")]
    sql = f"SELECT {','.join(cols)} FROM {TABLE}"
    params: tuple = ()
    if since:
        sql += " WHERE date >= ?"
        params = (since,)
    sql += " ORDER BY symbol, date"
    with closing(_connect()) as conn:
        return pd.DataFrame(conn.execute(sql, params).fetchall(), columns=cols)

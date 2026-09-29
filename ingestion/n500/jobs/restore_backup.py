"""Job: load a backup into whichever database .env points at.

    python -m n500.jobs.restore_backup
    python -m n500.jobs.restore_backup --backup-dir data/backup/2026-09-10

Written when the hosted project was deleted and the tracker had to be rebuilt,
first on a local Supabase stack and later on a new hosted project. Both go
through the same API, so one tool serves both.

Order matters because every market table references `stocks`. Run
`load_universe` and `load_index_membership` first: between them they recreate
the current constituents, the ETFs, and the companies that have left the index,
and a price row for a symbol `stocks` does not know is rejected.

Prices come from the local mirror when it exists. It is the same data as the
backup file, verified identical to the database before it was lost, and it is
already on disk in the shape the table wants.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

from ..config import DATA_DIR
from ..db import Db, run
from .. import pricecache

JOB = "restore_backup"

# table -> conflict key. Upserts, so a restore can be re-run after a failure.
TABLES = {
    "fundamentals_y": "symbol,period_end",
    "fundamentals_q": "symbol,period_end",
    "shareholding": "symbol,quarter_end",
    "company_ratios": "symbol",
    "index_prices": "index_name,date",
}
POSITION_COLUMNS = ("symbol", "entry_date", "entry_price", "quantity", "stop_price",
                    "target_price", "thesis", "setup")


def _latest_backup() -> Path:
    found = sorted((DATA_DIR / "backup").glob("20*"))
    if not found:
        raise SystemExit(f"[{JOB}] no backup under {DATA_DIR / 'backup'}")
    return found[-1]


def _read(path: Path) -> list[dict]:
    with gzip.open(path, "rt") if path.suffix == ".gz" else open(path) as f:
        return json.load(f)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Restore a backup")
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    source = args.backup_dir or _latest_backup()
    db = Db(force_dry_run=args.dry_run)
    known = {r["symbol"] for r in db.select("stocks", "symbol")}
    if not known:
        raise SystemExit(f"[{JOB}] stocks is empty: run load_universe and "
                         "load_index_membership first")

    done: dict[str, int] = {}
    with run(JOB, db=db) as log:
        for table, key in TABLES.items():
            rows = _read(source / f"{table}.json.gz")
            if "symbol" in key:
                rows = [r for r in rows if r.get("symbol") in known]
            done[table] = db.upsert(table, rows, on_conflict=key)

        # Prices, a symbol at a time so memory stays flat.
        if pricecache.path().exists():
            symbols = [s for s in pricecache.symbols() if s in known]
            skipped = len(pricecache.symbols()) - len(symbols)
            total = 0
            for symbol in symbols:
                rows = pricecache.frame(symbol).to_dict("records")
                # SQLite stores volume as REAL; the table's column is bigint and
                # rejects "73219.0" outright.
                for r in rows:
                    v = r["volume"]
                    r["volume"] = None if v is None or v != v else int(v)
                total += db.upsert("prices_daily", rows,
                                   on_conflict="symbol,date", chunk_size=1000)
            done["prices_daily"] = total
            if skipped:
                log.error("prices_daily", f"{skipped} symbols not in stocks, skipped")
        else:
            rows = [r for r in _read(source / "prices_daily.json.gz") if r["symbol"] in known]
            done["prices_daily"] = db.upsert("prices_daily", rows,
                                             on_conflict="symbol,date", chunk_size=1000)

        # Positions: inserted only into an empty table, so a re-run cannot
        # duplicate them. The database assigns ids.
        positions = _read(source / "positions-current.json")["positions"]
        if db.select("positions", "id"):
            log.error("positions", "table not empty, left as it is")
        else:
            done["positions"] = db.insert(
                "positions", [{k: p.get(k) for k in POSITION_COLUMNS} for p in positions])

        portfolio = source / "portfolio-reconstructed.json"
        if portfolio.exists():
            p = json.loads(portfolio.read_text())
            done["portfolio"] = db.upsert(
                "portfolio", [{"id": 1, "total_capital": p["total_capital"],
                               "risk_pct": p["risk_pct"]}], on_conflict="id")

        log.rows_written = sum(done.values())
        log.symbols_ok = len(done)
        log.notes = ", ".join(f"{t} {n:,}" for t, n in done.items())
        summary = log.notes

    print(f"[{JOB}] from {source.name}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

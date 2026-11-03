"""Tables that change once a week, kept on disk between the nights they do not.

`fundamentals_y`, `fundamentals_q`, `shareholding` and `company_ratios` are
written only by `load_fundamentals`, on Sundays, and were read in full every
night: 6MB a night for data that had not changed.

A copy is good while the latest `load_fundamentals` run and the table's row
count both match what it was saved against. Any attempt invalidates it, even a
failed one, since a failed run may have written part of what it fetched. The
count catches writes from elsewhere, such as a restore.
"""

from __future__ import annotations

import gzip
import json
import os
from typing import Any

from .config import DATA_DIR

CACHE_DIR = DATA_DIR / "cache" / "tables"


def weekly_table(db, table: str, *, writer: str = "load_fundamentals") -> list[dict[str, Any]]:
    """`db.select(table)`, from disk when nothing can have changed it."""
    if getattr(db, "dry_run", False):
        return db.select(table)

    key = f"{db.last_started(writer)}|{db.count(table)}"
    path = CACHE_DIR / f"{table}.json.gz"
    try:
        with gzip.open(path, "rt") as f:
            saved = json.load(f)
        if saved["key"] == key:
            return saved["rows"]
    except (OSError, ValueError, KeyError):
        pass        # absent or damaged: a cache that fails is just a read

    rows = db.select(table)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    with gzip.open(temporary, "wt") as f:
        json.dump({"key": key, "rows": rows}, f, default=str)
    temporary.replace(path)     # a crash mid-write cannot leave half a table
    return rows

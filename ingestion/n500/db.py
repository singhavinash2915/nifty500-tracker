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


class Db:
    """Thin wrapper over supabase-py with a dry-run fallback."""

    def __init__(self, *, force_dry_run: bool = False) -> None:
        self.dry_run = force_dry_run or not settings.has_supabase
        self._client = None
        if not self.dry_run:
            from supabase import create_client

            self._client = create_client(
                settings.supabase_url, settings.supabase_service_key
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

        if self.dry_run:
            DRYRUN_DIR.mkdir(parents=True, exist_ok=True)
            path = DRYRUN_DIR / f"{table}.json"
            path.write_text(json.dumps(rows, indent=2, default=str))
            return len(rows)

        for start in range(0, len(rows), chunk_size):
            chunk = rows[start : start + chunk_size]
            query = self._client.table(table).upsert(chunk, on_conflict=on_conflict)
            query.execute()
        return len(rows)

    def update(self, table: str, values: dict[str, Any], *, where: dict[str, Any]) -> None:
        if self.dry_run:
            return
        query = self._client.table(table).update(values)
        for column, value in where.items():
            query = query.eq(column, value)
        query.execute()

    # -- reads -------------------------------------------------------

    def select(
        self, table: str, columns: str = "*", *, where: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        if self.dry_run:
            path = DRYRUN_DIR / f"{table}.json"
            return json.loads(path.read_text()) if path.exists() else []
        query = self._client.table(table).select(columns)
        for column, value in (where or {}).items():
            query = query.eq(column, value)
        return query.execute().data

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

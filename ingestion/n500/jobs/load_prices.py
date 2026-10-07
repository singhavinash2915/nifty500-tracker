"""Job: backfill and update daily prices from the NSE bhavcopy archive.

    python -m n500.jobs.load_prices --days 500          # backfill
    python -m n500.jobs.load_prices --days 5            # nightly top-up
    python -m n500.jobs.load_prices --days 500 --dry-run

Bhavcopy files are immutable once published, so every fetch is cached under
data/cache/bhavcopy/. Re-running a backfill after a failure costs no network at
all — which matters, because a 500-day sweep is 500 requests and we would
rather not repeat it to recover from one bad day.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

import httpx

from ..db import Db, run
from .. import pricecache
from .compute_technicals import MIN_BARS
from ..sources import bhavcopy
from ..sources.bhavcopy import BhavcopyError, BhavcopyUnavailable, Quote

JOB = "load_prices"


def trading_day_candidates(days: int, *, end: date | None = None) -> list[date]:
    """Every calendar day going back from `end`; non-trading days 404 and skip.

    Weekends are deliberately included. NSE holds a special live session on
    Budget day, 1 February, even when it falls on a Saturday or Sunday — and
    skipping it does not merely lose one bar. The next session's PrvsClsgPric
    then disagrees with our stored close, which the corporate-action detector
    reads as a 2-4% split. That produced 119 spurious adjustments before this
    was fixed. Misses are cached, so the extra probes cost one run.
    """
    end = end or date.today()
    return sorted(end - timedelta(days=offset) for offset in range(days))


def needs_backfill(
    stats: dict[str, tuple[int, date]],
    active: set[str],
    *,
    today: date,
    window_days: int,
    min_bars: int,
) -> set[str]:
    """Current members whose whole history is the nightly window, and too short.

    The nightly loads `window_days` of prices for every symbol it tracks. A
    company that joins the index is tracked from that night, so it only ever
    gets the window: 28 joined in the September 2026 rebalance and every one
    sat unscored until its history was loaded by hand.

    `stats` is {symbol: (bars, first_date)}. A symbol qualifies when it is
    active, has fewer than `min_bars` bars, and its first bar falls inside the
    nightly window (with a week's margin), meaning nothing older was ever
    loaded. A recent listing qualifies for a few nights and then stops, once
    its first bar ages out of the window: its history is short because it is
    new, not because it was never fetched.
    """
    horizon = today - timedelta(days=window_days + 7)
    out = set()
    for symbol in active:
        bars, first = stats.get(symbol, (0, None))
        if bars >= min_bars:
            continue
        if first is None or first >= horizon:
            out.add(symbol)
    return out


def collect(client, days: int, symbols: set[str], log, *, pause: float):
    """Every bar for `symbols` over `days`, adjusted. One file per session.

    Returns (rows, sessions, holidays, actions, symbols_seen). Files are cached
    on disk forever, so a long backfill over days already fetched costs no
    requests at all.
    """
    per_symbol: dict[str, list[Quote]] = defaultdict(list)
    sessions = missing = 0
    for day in trading_day_candidates(days):
        cached = bhavcopy._cache_path(day).exists()
        try:
            quotes = bhavcopy.fetch(client, day)
        except BhavcopyUnavailable:
            missing += 1
            continue
        except (BhavcopyError, httpx.HTTPError) as exc:
            log.error(day.isoformat(), str(exc))
            continue

        for symbol in symbols & quotes.keys():
            per_symbol[symbol].append(quotes[symbol])
        sessions += 1

        if not cached:
            time.sleep(pause + random.uniform(0, 0.15))

    rows: list[dict] = []
    actions = 0
    for symbol, quotes in per_symbol.items():
        rows.extend(bhavcopy.adjust(quotes))
        actions += len(bhavcopy.corporate_actions(quotes))
    return rows, sessions, missing, actions, set(per_symbol)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load daily prices from NSE bhavcopy")
    parser.add_argument(
        "--days", type=int, default=760,
        help="calendar days back to cover (760 ~ 2 years of sessions)",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--pause", type=float, default=0.35, help="seconds between fetches")
    parser.add_argument(
        "--symbols", help="comma-separated subset, for testing; defaults to the universe"
    )
    args = parser.parse_args(argv)

    db = Db(force_dry_run=args.dry_run)

    if args.symbols:
        universe = {s.strip().upper() for s in args.symbols.split(",")}
        active = universe
    else:
        stocks = db.select("stocks", "symbol,is_active")
        active = {r["symbol"] for r in stocks if r.get("is_active", True)}
        # Companies that have left the index are loaded too. The bhavcopy is
        # the whole market on each day, so their history costs no extra
        # requests — only rows — and without it the backtest can only ever see
        # the survivors, which is the bias this is here to remove.
        universe = {r["symbol"] for r in stocks}
    if not universe:
        print(f"[{JOB}] universe is empty — run load_universe first", file=sys.stderr)
        return 1

    client = bhavcopy.make_client()

    with run(JOB, db=db) as log:
        rows, sessions, missing, actions, seen = collect(
            client, args.days, universe, log, pause=args.pause)
        log.symbols_ok += len(seen)

        for missed in sorted(active - seen):
            # A *current* Nifty 500 name absent from every session is a
            # symbol-mapping problem, not a market event. A delisted one is
            # simply gone, which is the whole point of keeping it, so only the
            # active list is checked.
            log.error(missed, "not present in any bhavcopy session")

        log.rows_written = db.upsert("prices_daily", rows, on_conflict="symbol,date")

        # New index members get their history now rather than never. Not for
        # a hand-picked --symbols run, which is already a backfill, nor a dry
        # run, whose prices are fixtures.
        backfilled: set[str] = set()
        if not args.symbols and not db.dry_run:
            pricecache.sync(db)
            stats = pricecache.bar_stats()
            thin = needs_backfill(stats, active, today=date.today(),
                                  window_days=args.days, min_bars=MIN_BARS)
            if thin:
                # As deep as everyone else's history, so a new member is
                # measured over the same span as the rest of the index.
                oldest = min((f for _, f in stats.values()), default=date.today())
                depth = max((date.today() - oldest).days, args.days)
                more, _, _, more_actions, backfilled = collect(
                    client, depth, thin, log, pause=args.pause)
                log.rows_written += db.upsert("prices_daily", more, on_conflict="symbol,date")
                # Mirror them too, or the next sync rebuilds it from scratch.
                pricecache.add(more)
                actions += more_actions
                rows += more

        gone = len(universe) - len(active)
        log.notes = (
            f"{sessions} sessions ({missing} holidays skipped), "
            f"{len(seen)} symbols ({gone} no longer in the index), "
            f"{actions} corporate actions adjusted"
            + (f"; history backfilled for {len(backfilled)} new members: "
               f"{', '.join(sorted(backfilled))}" if backfilled else "")
        )
        summary = log.notes

    mode = "dry run" if db.dry_run else "Supabase"
    print(f"[{JOB}] {len(rows)} price rows written ({mode}) — {summary}")
    if not args.dry_run:
        print(f"[{JOB}] cache: {bhavcopy.CACHE_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

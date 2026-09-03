# CLAUDE.md — Nifty 500 Conviction Tracker

A screener over the Nifty 500 that scores every constituent on quality-growth,
value, momentum and reversal-from-support, for a ~6 month holding horizon.

**Not investment advice.** The app organises research; it does not recommend.

## Commands

```bash
# ingestion (python 3.13 in .venv)
./.venv/bin/python -m n500.jobs.load_universe --dry-run   # run from ingestion/
./.venv/bin/python -m pytest ingestion/tests -q

# web
npm run dev   --prefix web
npm run build --prefix web      # tsc -b && vite build
```

## Layout

```
ingestion/n500/
  config.py            env-driven settings (.env at repo root)
  db.py                Supabase wrapper + RunLogger; dry-run writes data/dryrun/*.json
  sources/             one module per external data source, each with a parser
  jobs/                runnable entry points (python -m n500.jobs.<name>)
supabase/migrations/   SQL run manually in the Supabase SQL Editor
web/                   React 19 + Vite + Tailwind v4 (note: v4, unlike the SCC app's v3)
data/dryrun/           gitignored job output when Supabase is unconfigured
```

## Rules that the design depends on

1. **Never read fundamentals by `period_end`.** Scoring and backtests may only
   use rows where `filed_on <= as_of_date`. Q2 results are filed in November,
   not September; using period_end is look-ahead bias and it silently invents
   profitable strategies that lose money live.
2. **Percentile-rank within sector**, not across the whole index. A 14x P/E
   means different things for a bank and an FMCG name. Sectors with fewer than
   10 members are pooled into an all-stocks ranking instead — currently
   Textiles (5), Media Entertainment & Publication (5), Diversified (3).
3. **Never delete history.** Index dropouts get `is_active=false`; broken
   support zones get `invalidated_on` set. Backtests reconstruct the
   point-in-time universe from `index_membership`.
4. **Every job writes an `ingestion_runs` row.** A job that dies leaves
   `status='running'` with a null `finished_at` — that is the signal a scraper
   broke, as opposed to the data merely being stale.
5. **Parsers assert their shape.** A layout change upstream must fail loudly,
   never write 500 rows of nulls.
6. **The browser never writes** anything except watchlist/positions/alerts. The
   service-role key lives only in the Python side.

## Scoring model

Four scores, 0–100 each. `Q` quality-growth, `V` value, `T-M` momentum,
`T-S` support reversal. Blend uses `max(T-M, T-S)` and records which won.

`T-S` is gated: only computed when `Q >= 60` with no active red flag; capped at
55 without a reversal confirmation (status `watching`), capped at 45 while the
stock is making lower highs after a change-of-character. Red flags (promoter
pledge > 20%, promoter holding down > 3pp over 4 quarters, 3y CFO < 50% of 3y
PAT, auditor qualification) exclude rather than deduct.

## Phase status

- [x] **1 — Skeleton.** Schema, universe loader, web shell.
- [ ] 2 — Prices + technicals + T-M
- [ ] 3 — Zone engine + T-S (port `structural_poc.pine` SPL/SPH to Python)
- [ ] 4 — Fundamentals scrapers + Q and V
- [ ] 5 — Screener and stock detail UI
- [ ] 6 — Backtest engine
- [ ] 7 — Positions, alerts, scheduling

# CLAUDE.md — Nifty 500 Conviction Tracker

A screener over the Nifty 500 that scores every constituent on quality-growth,
value, momentum and reversal-from-support, for a ~6 month holding horizon.

**Not investment advice.** The app organises research; it does not recommend.

## Commands

```bash
# ingestion — run job modules from ingestion/, pytest from the repo root
cd ingestion
../.venv/bin/python -m n500.jobs.load_universe --dry-run
../.venv/bin/python -m n500.jobs.load_prices --days 760 --dry-run   # ~10 min cold
../.venv/bin/python -m n500.jobs.load_index  --days 760 --dry-run
../.venv/bin/python -m n500.jobs.compute_technicals --dry-run --tail 5
../.venv/bin/python -m n500.jobs.load_fundamentals --dry-run        # ~70 min cold
../.venv/bin/python -m n500.jobs.compute_fundamental_scores --dry-run
../.venv/bin/python -m n500.jobs.compute_zones --dry-run
../.venv/bin/python -m n500.jobs.compute_scores --dry-run
../.venv/bin/python -m n500.jobs.export_snapshot --dry-run   # writes web/public/

./.venv/bin/python -m pytest ingestion/tests -q    # from the repo root

# web
npm run dev   --prefix web
npm run build --prefix web      # tsc -b && vite build
```

Nightly the same jobs run in order with `--days 10` and no `--dry-run`.

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
7. **Corporate actions are detected on the OPEN, never the close.** An ex-date
   opens already at the adjusted price; a crash is capped at the 10% circuit on
   the open and does its falling intraday. Detecting on the close would have
   adjusted away four real crashes (IEX, INDUSINDBK, CYIENT, ADANIENT) and
   erased genuine 25%+ drawdowns. `PrvsClsgPric` is *not* adjusted by NSE and
   must not be used for this.
8. **Price-day candidates include weekends.** NSE runs a special live session on
   Budget day, 1 February, even on a Saturday. Skipping it leaves a hole that
   makes the next session look like a 2-4% corporate action.
9. **A pivot is knowable only when it is confirmed.** An SPL sits at an earlier
   bar than the Bar2 that proved it; a fractal lags by its span. Every pivot
   carries `confirmed_index`, and historical queries filter on that, never on
   the pivot's own date. Same trap as `filed_on`.
10. **Weekly bars are stamped with the last session that actually traded.**
    Pandas labels a `W-FRI` bin by its Friday even mid-week, which dated a
    partial bar two days past the latest real session.
11. **Coerce numpy booleans to `bool` before they reach JSONB.** A `np.bool_`
    serialises to the *string* `"False"`, which is truthy — the UI then shows a
    reversal confirmation that never fired.
12. **Score lenders on a different question set.** A bank funds itself with
    deposits, so debt/equity and interest cover describe its model rather than
    its risk, and Screener publishes no ROCE or operating margin for one.
    HDFCBANK's reported "OPM" of -12% is a Financing Margin. Applying the
    general set would push all 101 Financial Services names to the bottom of
    the index for asking the wrong questions.
13. **A check that could not run is `unknown`, never `pass`.** Screener carries
    no pledge figure, so the promoter-pledge gate is permanently unevaluable
    from this source. Reporting it clear would give false comfort about the
    commonest way an Indian mid-cap goes wrong.
14. **A missing Promoters row is a fact, not a gap.** ITC, HDFC Bank and
    Infosys have no promoter, so promoter-selling is `not_applicable`.
15. **Dry-run upsert merges by key.** Overwriting the file was silent data
    loss: a job writing three `stocks` rows replaced the 500-row universe, and
    the next job ran on three symbols and reported success.
16. **A wholly derived table is replaced, never upserted.** `support_zones`
    has a bigserial key, so upserting appended: the count climbed 9,045 ->
    11,841 across two runs and the chart drew stale geometry. Use `db.replace`.
17. **Cluster against the band floor, not the last member.** Single-linkage
    chaining walked a "support zone" 16% up the chart on ULTRACEMCO.
18. **Assertions catch layout changes, not unusual companies.** The first
    sweep discarded 49 legitimate names — recent listings with short
    histories, and loss-makers whose margins really are -450%.
19. **Charts subscribe to the theme.** Reading `matchMedia` once at render
    leaves the SVG painting light ink on a dark surface; CSS follows the theme
    instantly and JavaScript-coloured marks do not.

## Scoring model

Four scores, 0–100 each. `Q` quality-growth, `V` value, `T-M` momentum,
`T-S` support reversal. Blend uses `max(T-M, T-S)` and records which won.

`T-S` is gated: only computed when `Q >= 60` with no active red flag; capped at
55 without a reversal confirmation (status `watching`), capped at 45 while the
stock is making lower highs after a change-of-character. Red flags (promoter
pledge > 20%, promoter holding down > 3pp over 4 quarters, 3y CFO < 50% of 3y
PAT, auditor qualification) exclude rather than deduct.

## Data sources

| What | Source | Shape |
|---|---|---|
| Universe | `niftyindices.com/IndexConstituent/ind_nifty500list.csv` | one file, 500 rows |
| Prices | NSE bhavcopy (`nsearchives.nseindia.com/content/cm/...`) | one file per session, whole market |
| Benchmark | NSE index archive (`.../content/indices/ind_close_all_*.csv`) | one file per session, all indices |

Bhavcopy beats the Yahoo chart endpoint structurally: one request covers every
symbol for a day, where Yahoo is one request per symbol and starts returning
429 within seconds of a burst. Both archives are immutable once published, so
every fetch — and every 404 — is cached under `data/cache/`, which makes a
re-run of a 760-day backfill cost no network at all.

`sec_bhavdata_full_*.csv` additionally carries delivery percentage, a useful
accumulation signal not yet wired in.

## Phase status

- [x] **1 — Skeleton.** Schema, universe loader, web shell.
- [x] **2 — Prices + technicals + T-M.** 247k price rows over 517 sessions,
      split adjustment, 19 indicators, momentum score, screener table.
- [x] **3 — Zone engine + T-S.** SPL/SPH ported from `structural_poc.pine`,
      fractal swings, volume shelves, ~9k zones, reversal confirmation, gates.
- [x] **4 — Fundamentals + Q and V.** Screener.in scraper, red-flag gates,
      quality gate live on T-S, blended Q 45 / V 20 / technical 35.
- [x] **5 — Screener and stock detail UI.** Sortable screener with live blend
      weights and a gate funnel; stock detail with price/zones/MA chart,
      financial trends and shareholding.
- [ ] 6 — Backtest engine
- [ ] 7 — Positions, alerts, scheduling

## Zone engine notes

`find_pivots` is a faithful port of the Pine except for one documented
departure: it re-anchors after 60 bars without a confirmed pivot. The original
anchors at a user-chosen recent time on a 1-hour chart; pointed at two years of
daily bars it stalls, producing 0-6 pivots over 517 sessions (AMBUJACEM: none).
Clustering therefore draws on both SPL/SPH and conventional fractals — the
first says which lows the current structure rests on, the second gives the
dense coverage a long scan needs.

`next_resistance` uses *major* resistance: swing highs with span 10, clustered
within 0.6 ATR, requiring two members. The nearest minor swing high is
typically 5-12% away, so testing headroom against it would reject the whole
market.

The stop is never closer than 1.5 ATR from entry. A stop at the floor of a
tight band can sit 2% away, which produces a headline 23:1 reward-to-risk that
ordinary noise stops out within days.

## Fundamentals notes

Screener.in prohibits automated access in its terms. The route was chosen
knowingly; the module is built to be a considerate guest — one request every
2.5-4 seconds, pages cached 25 days (financials change quarterly, so refetching
daily is scrape volume for no information), and everything behind one interface
so a paid feed is a one-file swap.

`filed_on` is an **estimate**: Screener publishes the period but not the filing
date, so rows use the SEBI LODR deadline (45 days after a quarter, 60 after a
year) and carry `filed_on_is_estimated`. Erring late is the safe direction — it
can only make a backtest pessimistic, whereas erring early is look-ahead bias.

Odd reporting periods are annualised. A company changing its year-end files a
15-month year, which Screener labels `Mar 202315m`; left alone that reads as a
25% growth spurt followed by a collapse.

**Known gap:** promoter pledge is not available from Screener and the gate
reports `unknown`. Trendlyne carries it but is keyed by an internal numeric id
(HDFCBANK is 1024) that would need per-symbol discovery — a second scraper.

# Nifty 500 Conviction Tracker

Scores all 500 Nifty constituents on quality-growth, value, momentum and
reversal-from-support, for a six-month holding horizon — then measures, from
history, how often a stock scoring like this actually returned 25% in six months.

For personal research use. Not investment advice.

## Setup

```bash
python3.13 -m venv .venv
./.venv/bin/pip install -r ingestion/requirements.txt
npm install --prefix web
```

Create a Supabase project, run `supabase/migrations/0001_init.sql` in its SQL
Editor, then copy `.env.example` to `.env` (service-role key, for ingestion) and
`web/.env.example` to `web/.env.local` (anon key, for the browser).

Without credentials everything still runs: jobs write JSON to `data/dryrun/` and
the web app falls back to a bundled snapshot.

## Load the universe

```bash
cd ingestion && ../.venv/bin/python -m n500.jobs.load_universe
```

Writes 500 rows to `stocks` and this week's snapshot to `index_membership`.

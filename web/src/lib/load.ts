import { supabase } from './supabase'
import type { Bars, PeriodRecord, ScreenerRow, ScreenerSnapshot, StockDetail, Zone } from '../types'

// Two years of daily bars are shown, but the moving averages are computed over
// a longer window so the 200DMA has a value on the very first visible bar
// instead of 200 bars of nothing. Both fit inside PostgREST's 1,000-row cap.
const VISIBLE_BARS = 520
const FETCH_BARS = 760

/**
 * Where the screener's rows come from.
 *
 * Supabase when it is configured, the exported snapshot otherwise — and the
 * snapshot is also the fallback when a live read fails, because a stale
 * screener is far better than an empty one, and a deploy to GitHub Pages has
 * no database at all.
 *
 * Note PostgREST caps responses at `max_rows` (1000 on this project). The
 * universe is 500, so a single request covers it; anything that outgrows that
 * needs the same range-based paging the Python side does, ordered, or it will
 * silently return a prefix.
 */
export async function loadScreener(): Promise<{
  snapshot: ScreenerSnapshot
  source: 'supabase' | 'snapshot'
  error: string | null
}> {
  if (supabase) {
    try {
      const { data, error } = await supabase
        .from('scores_daily')
        .select('*, stocks(company_name, sector, company_type)')
        .order('date', { ascending: false })
        .order('blended', { ascending: false, nullsFirst: false })
        .limit(1000)

      if (error) throw new Error(error.message)
      if (data?.length) {
        const latest = data[0].date as string
        const rows = data
          .filter((r) => r.date === latest)
          .map(toScreenerRow)
        return { snapshot: { as_of: latest, rows }, source: 'supabase', error: null }
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e)
      const snapshot = await loadSnapshotFile()
      return { snapshot, source: 'snapshot', error: message }
    }
  }

  return { snapshot: await loadSnapshotFile(), source: 'snapshot', error: null }
}

async function loadSnapshotFile(): Promise<ScreenerSnapshot> {
  const res = await fetch(`${import.meta.env.BASE_URL}scores-sample.json`)
  return (await res.json()) as ScreenerSnapshot
}

/** Live rows carry the joined stock, and none of the derived chart fields. */
function toScreenerRow(r: Record<string, any>): ScreenerRow {
  const stock = r.stocks ?? {}
  return {
    symbol: r.symbol,
    company_name: stock.company_name ?? r.symbol,
    sector: stock.sector ?? null,
    company_type: stock.company_type ?? null,
    close: null,
    quality_score: r.quality_score,
    value_score: r.value_score,
    tm_score: r.tm_score,
    ts_score: r.ts_score,
    blended: r.blended,
    winning_setup: r.winning_setup ?? 'none',
    setup_status: r.setup_status ?? 'none',
    decile: r.decile,
    flags: r.flags ?? [],
    pe: null,
    roe: null,
    mom_12_1: null,
    rs_vs_index: null,
    dist_52w_high: null,
    rsi14: null,
    above_200dma: null,
    stop_price: r.stop_price ?? null,
    target_price: r.target_price ?? null,
    reward_risk: r.reward_risk ?? null,
    headroom: null,
    zone_floor: null,
    zone_ceil: null,
    confirmations: [],
    caps: [],
    reason: null,
  }
}


/**
 * One stock's chart, zones and financials.
 *
 * Reads from Supabase, falling back to the exported JSON. Serving these from
 * the database rather than 486 static files is what makes a static deploy
 * viable at all: the files are 23MB of nightly-regenerated data that would
 * otherwise have to be committed to git or rebuilt in CI, and they would go
 * stale between deploys while the screener beside them stayed live.
 */
export async function loadDetail(symbol: string): Promise<StockDetail | null> {
  if (supabase) {
    try {
      const [bars, zones, annual, quarterly, holding] = await Promise.all([
        supabase
          .from('prices_daily')
          .select('date,open,high,low,close,adj_close,volume')
          .eq('symbol', symbol)
          .order('date', { ascending: false })
          .limit(FETCH_BARS),
        supabase.from('support_zones').select('*').eq('symbol', symbol),
        supabase
          .from('fundamentals_y')
          .select('period_end,revenue,ebitda,pat,eps,cfo,roce,roe,debt_equity,debtor_days')
          .eq('symbol', symbol)
          .order('period_end'),
        supabase
          .from('fundamentals_q')
          .select('period_end,revenue,pat,opm,eps')
          .eq('symbol', symbol)
          .order('period_end'),
        supabase
          .from('shareholding')
          .select('quarter_end,promoter_pct,fii_pct,dii_pct,public_pct')
          .eq('symbol', symbol)
          .order('quarter_end'),
      ])

      if (bars.error) throw new Error(bars.error.message)
      if (!bars.data?.length) throw new Error('no price history')

      // Fetched newest-first so the limit takes the most recent window.
      const rows = [...bars.data].reverse()
      return {
        symbol,
        ...framesFrom(rows),
        zones: (zones.data ?? []).map(toZone),
        annual: (annual.data ?? []).map((r) => rename(r, 'period_end')),
        quarterly: (quarterly.data ?? []).map((r) => rename(r, 'period_end')),
        shareholding: (holding.data ?? []).map((r) => rename(r, 'quarter_end')),
      }
    } catch {
      // fall through to the static snapshot
    }
  }

  try {
    const res = await fetch(`${import.meta.env.BASE_URL}stocks/${symbol}.json`)
    return res.ok ? ((await res.json()) as StockDetail) : null
  } catch {
    return null
  }
}

/** Split-adjusted OHLCV plus the two moving averages the chart draws. */
function framesFrom(rows: Record<string, any>[]): {
  bars: Bars
  overlays: { sma50?: (number | null)[]; sma200?: (number | null)[] }
} {
  // adj_close / close is the cumulative corporate-action factor; applying it to
  // the other legs keeps the candle consistent with the line.
  const factor = rows.map((r) =>
    r.close && r.adj_close ? Number(r.adj_close) / Number(r.close) : 1,
  )
  const adjusted = rows.map((r) => Number(r.adj_close))

  const sma50 = movingAverage(adjusted, 50)
  const sma200 = movingAverage(adjusted, 200)
  const from = Math.max(rows.length - VISIBLE_BARS, 0)

  return {
    bars: {
      t: rows.slice(from).map((r) => String(r.date)),
      o: rows.slice(from).map((r, i) => num(r.open) * factor[from + i]),
      h: rows.slice(from).map((r, i) => num(r.high) * factor[from + i]),
      l: rows.slice(from).map((r, i) => num(r.low) * factor[from + i]),
      c: adjusted.slice(from),
      v: rows.slice(from).map((r) => num(r.volume)),
    },
    overlays: { sma50: sma50.slice(from), sma200: sma200.slice(from) },
  }
}

function movingAverage(values: number[], window: number): (number | null)[] {
  const out: (number | null)[] = []
  let sum = 0
  for (let i = 0; i < values.length; i++) {
    sum += values[i]
    if (i >= window) sum -= values[i - window]
    out.push(i >= window - 1 ? sum / window : null)
  }
  return out
}

const num = (v: unknown) => (v === null || v === undefined ? 0 : Number(v))

function toZone(z: Record<string, any>): Zone {
  return {
    timeframe: z.timeframe,
    source: z.source,
    floor: Number(z.floor_price),
    ceil: Number(z.ceil_price),
    touches: Number(z.touch_count ?? 0),
    strength: z.strength === null ? null : Number(z.strength),
    formed_on: z.formed_on ?? null,
    invalidated_on: z.invalidated_on ?? null,
  }
}

/** The charts key every series off `period`, whatever the column was called. */
function rename(row: Record<string, any>, dateColumn: string): PeriodRecord {
  const { [dateColumn]: period, ...rest } = row
  const out: PeriodRecord = { period: String(period) }
  for (const [k, v] of Object.entries(rest)) out[k] = v === null ? null : Number(v)
  return out
}

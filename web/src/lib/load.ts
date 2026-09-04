import { supabase } from './supabase'
import type { AlertRow, Bars, PeriodRecord, ScreenerRow, ScreenerSnapshot, StockDetail, Zone } from '../types'

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
    close: r.close ?? null,
    quality_score: r.quality_score,
    value_score: r.value_score,
    revision_score: r.revision_score ?? null,
    ownership_score: r.ownership_score ?? null,
    tm_score: r.tm_score,
    ts_score: r.ts_score,
    blended: r.blended,
    conviction: r.conviction ?? null,
    conviction_decile: r.conviction_decile ?? null,
    winning_setup: r.winning_setup ?? 'none',
    setup_status: r.setup_status ?? 'none',
    decile: r.decile,
    flags: r.flags ?? [],
    pe: null,
    roe: null,
    mom_12_1: r.mom_12_1 ?? null,
    rs_vs_index: r.rs_vs_index ?? null,
    dist_52w_high: r.dist_52w_high ?? null,
    rsi14: r.rsi14 ?? null,
    above_200dma: r.above_200dma ?? null,
    stop_price: r.stop_price ?? null,
    target_price: r.target_price ?? null,
    reward_risk: r.reward_risk ?? null,
    resistance_floor: null,
    resistance_ceil: null,
    resistance_strength: null,
    false_breakout: null,
    rejected_at_resistance: false,
    turnover_60d_cr: r.turnover_60d_cr ?? null,
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
    kind: z.kind ?? 'support',
    source: z.source,
    floor: Number(z.floor_price),
    ceil: Number(z.ceil_price),
    touches: Number(z.touch_count ?? 0),
    strength: z.strength === null ? null : Number(z.strength),
    respect: z.respect === null || z.respect === undefined ? null : Number(z.respect),
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


/**
 * Open holdings, marked to market and joined to what the screener thinks now.
 *
 * Live only. There used to be a fallback to a `positions.json` served beside
 * the app, which was a hole rather than a convenience: everything under
 * web/public is published to GitHub Pages, so the fallback handed out exactly
 * what the database was withholding. Signed out, this returns nothing — which
 * is the correct answer, not a degraded one.
 */
export interface PortfolioSettings {
  total_capital: number
  risk_pct: number
}

export async function loadPortfolio(): Promise<{
  positions: PositionView[]
  settings: PortfolioSettings | null
  source: 'supabase' | 'snapshot'
}> {
  if (supabase) {
    try {
      const { data, error } = await supabase
        .from('positions')
        .select('*')
        .is('exit_date', null)
        .order('entry_date')
      if (error) throw new Error(error.message)

      const rows = data ?? []
      if (!rows.length) return { positions: [], settings: null, source: 'supabase' }

      const symbols = [...new Set(rows.map((r) => r.symbol as string))]
      const [scores, prices, setups] = await Promise.all([
        supabase.from('scores_daily').select('*').in('symbol', symbols)
          .order('date', { ascending: false }),
        supabase.from('prices_daily').select('symbol,date,adj_close').in('symbol', symbols)
          .order('date', { ascending: false }).limit(symbols.length * 5),
        supabase.from('ts_setups').select('*')
          .in('symbol', symbols).order('date', { ascending: false }),
      ])

      const firstBy = <T extends { symbol: string }>(list: T[] | null) => {
        const out = new Map<string, T>()
        for (const r of list ?? []) if (!out.has(r.symbol)) out.set(r.symbol, r)
        return out
      }
      const score = firstBy(scores.data as any[])
      const close = firstBy(prices.data as any[])
      const setup = firstBy(setups.data as any[])

      const marked = rows.map((p) =>
        mark(p, Number(close.get(p.symbol)?.adj_close ?? NaN),
             score.get(p.symbol), setup.get(p.symbol)),
      )
      const { data: settingsRow } = await supabase
        .from('portfolio').select('total_capital,risk_pct').limit(1).maybeSingle()
      const settings: PortfolioSettings | null = settingsRow
        ? {
            total_capital: Number(settingsRow.total_capital),
            risk_pct: Number(settingsRow.risk_pct),
          }
        : null
      return {
        positions: withRiskShares(marked, settings),
        settings,
        source: 'supabase',
      }
    } catch {
      // A signed-out read is a 401 from PostgREST, not an exception worth
      // showing: the page asks for a password instead.
    }
  }

  return { positions: [], settings: null, source: 'supabase' }
}

/**
 * Tonight's alerts. Private for the same reason the positions are — an alert
 * is generated from the holdings, so "crossed below your stop" names both the
 * stock and the fact that it is owned.
 */
export async function loadAlerts(): Promise<AlertRow[]> {
  if (!supabase) return []
  const { data, error } = await supabase
    .from('alerts')
    .select('*')
    .order('date', { ascending: false })
    .limit(50)
  if (error || !data) return []
  const latest = data[0]?.date
  return data.filter((a) => a.date === latest) as AlertRow[]
}

export interface PositionView {
  id: number | null
  symbol: string
  entry_date: string | null
  entry_price: number
  quantity: number
  stop_price: number | null
  target_price: number | null
  thesis: string | null
  close: number | null
  return_pct: number | null
  pnl: number | null
  value: number | null
  stop_distance_pct: number | null
  risk_remaining: number | null
  to_target_pct: number | null
  decile: number | null
  blended: number | null
  failed_gates: string[]
  thesis_intact: boolean | null
  zone_floor: number | null
  /** Suggested stop for any stock, not only one with a live setup. */
  plan_stop: number | null
  plan_stop_basis: string | null
  plan_target: number | null
  plan_reward_risk: number | null
  /** Profit measured in units of the risk taken at entry. */
  r_multiple: number | null
  /** Set once the position is up 1R and the stop is still below cost. */
  move_stop_to: number | null
  days_held: number | null
  sector: string | null
  /** Capital at risk here as a share of total capital. Zero once the stop is
   *  above cost, because what is left to lose is profit rather than capital. */
  risk_share: number | null
  /** Everything between here and the stop, profit included. */
  give_back_share: number | null
  /** This position's market value as a share of the book. */
  weight: number | null
  /** Short sentences describing what needs attention, most urgent first. */
  insights: { tone: 'bad' | 'warn' | 'good'; text: string }[]
}

const n = (v: unknown) => (v === null || v === undefined ? null : Number(v))

function mark(p: any, close: number, score: any, setup: any): PositionView {
  const entry = Number(p.entry_price)
  const qty = Number(p.quantity)
  const stop = p.stop_price === null ? null : Number(p.stop_price)
  const target = p.target_price === null ? null : Number(p.target_price)
  const has = Number.isFinite(close) && entry > 0

  const failed = ((score?.flags ?? []) as any[])
    .filter((f) => f?.verdict === 'fail')
    .map((f) => String(f.name))
  const decile = score?.decile ?? null
  const view: PositionView = {
    id: p.id ?? null,
    symbol: p.symbol,
    entry_date: p.entry_date ?? null,
    entry_price: entry,
    quantity: qty,
    stop_price: stop,
    target_price: target,
    thesis: p.thesis ?? null,
    close: has ? close : null,
    return_pct: has ? close / entry - 1 : null,
    pnl: has ? (close - entry) * qty : null,
    value: has ? close * qty : null,
    stop_distance_pct: has && stop ? close / stop - 1 : null,
    risk_remaining: has && stop ? (close - stop) * qty : null,
    to_target_pct: has && target ? target / close - 1 : null,
    decile,
    blended: score?.blended ?? null,
    failed_gates: failed,
    thesis_intact: score ? failed.length === 0 : null,
    zone_floor: setup?.zone_floor ?? null,
    plan_stop: n(setup?.plan_stop),
    plan_stop_basis: setup?.plan_stop_basis ?? null,
    plan_target: n(setup?.plan_target),
    plan_reward_risk: n(setup?.plan_reward_risk),
    r_multiple: null,
    move_stop_to: null,
    days_held: p.entry_date
      ? Math.round((Date.now() - new Date(p.entry_date).getTime()) / 86_400_000)
      : null,
    sector: null,
    risk_share: null,
    give_back_share: null,
    weight: null,
    insights: [],
  }

  // R multiple against whichever stop is actually governing the position: the
  // one recorded at entry if there is one, the suggested one otherwise. Without
  // this a holding with no stop recorded has no risk unit and cannot be
  // compared with the rest of the book at all.
  const governing = view.stop_price ?? view.plan_stop
  if (governing !== null && has && entry > governing) {
    view.r_multiple = Number(((close - entry) / (entry - governing)).toFixed(2))
    if (view.r_multiple >= 1 && governing < entry) view.move_stop_to = entry
  }
  view.sector = score?.stocks?.sector ?? null

  view.insights = insightsFor(view, setup)
  return view
}

/**
 * Risk and weight as shares of capital, and the insights that depend on them.
 *
 * The denominator is the whole point and it was wrong. Risk was divided by the
 * market value of the *recorded* positions, so with two of seven holdings in
 * the database a position risking 0.33% of capital was reported at 5.4% and
 * flagged as oversized. The rupees were right; the denominator was whatever
 * happened to have been entered.
 *
 * Capital cannot be derived — the cash beside the positions is invisible to a
 * tracker — so it comes from the portfolio row. Without it this falls back to
 * book value and the page says so, rather than quietly reporting a number that
 * looks like an answer.
 */
function withRiskShares(
  positions: PositionView[],
  settings: PortfolioSettings | null,
): PositionView[] {
  const book = positions.reduce((sum, p) => sum + (p.value ?? 0), 0)
  const base = settings?.total_capital ?? book
  if (base <= 0) return positions

  for (const p of positions) {
    p.weight = (p.value ?? 0) / base
    // A stop above the entry means the money between here and it is open
    // profit, not capital. Reporting that as risk is what made a position that
    // cannot lose money look like the largest bet on the book.
    const exposed =
      p.stop_price !== null && p.stop_price >= p.entry_price
        ? 0
        : Math.max(p.risk_remaining ?? 0, 0)
    p.risk_share = p.risk_remaining === null ? null : exposed / base
    p.give_back_share =
      p.risk_remaining === null ? null : Math.max(p.risk_remaining, 0) / base
  }

  // Recomputed rather than appended, so the risk sentences sit in the same
  // priority order as everything else rather than always landing last.
  for (const p of positions) p.insights = insightsFor(p, null, p.insights)
  return positions
}

const pctText = (v: number | null) =>
  v === null ? 'no change' : `${(v * 100).toFixed(1)}%`

/** Ordered by what should worry you most, not by what is easiest to compute. */
const RISK_UNIT_LIMIT = 0.02

function insightsFor(
  v: PositionView,
  setup: any,
  existing?: PositionView['insights'],
): PositionView['insights'] {
  // On the second pass the position-level sentences are already written; only
  // the book-level ones are missing, and re-deriving the first set would need
  // the setup row again.
  const out: PositionView['insights'] = existing
    ? existing.filter((i) => i.text !== 'Nothing needs attention today.')
    : []

  if (v.risk_share !== null && v.risk_share > RISK_UNIT_LIMIT) {
    out.push({
      tone: 'warn',
      text: `${(v.risk_share * 100).toFixed(1)}% of capital is at stake between here and this stop — a risk unit is normally 1%.`,
    })
  } else if (
    v.risk_share === 0 &&
    v.give_back_share !== null &&
    v.give_back_share > RISK_UNIT_LIMIT
  ) {
    out.push({
      tone: 'good',
      text: `The stop is above cost, so none of this is capital at risk — but ${(v.give_back_share * 100).toFixed(1)}% of capital in open profit sits between here and it.`,
    })
  }
  if (existing) {
    if (!out.length) out.push({ tone: 'good', text: 'Nothing needs attention today.' })
    return out
  }

  if (v.stop_price === null && v.plan_stop !== null) {
    out.push({
      tone: 'warn',
      text: `No stop recorded. The engine suggests ${v.plan_stop.toFixed(2)} (${v.plan_stop_basis}) — until one is set, the position has no defined risk and cannot be sized against the rest of the book.`,
    })
  }
  if (v.move_stop_to !== null) {
    out.push({
      tone: 'good',
      text: `Up more than 1R. Moving the stop to ${v.move_stop_to.toFixed(2)} makes this position unable to lose money.`,
    })
  }
  if (v.days_held !== null && v.days_held > 180 && (v.return_pct ?? 0) < 0.05) {
    out.push({
      tone: 'warn',
      text: `Held ${Math.round(v.days_held / 30)} months for ${pctText(v.return_pct)}. The thesis was a six-month one; dead money costs more than losses in a strategy that needs its winners.`,
    })
  }
  if (v.failed_gates.length) {
    out.push({
      tone: 'bad',
      text: `Now fails ${v.failed_gates.map((f) => f.replace(/_/g, ' ')).join(' and ')} — the reason you bought it no longer holds.`,
    })
  }
  if (v.stop_distance_pct !== null && v.stop_distance_pct <= 0) {
    out.push({ tone: 'bad', text: 'Trading below your stop. The decision was made when you set it.' })
  } else if (v.stop_distance_pct !== null && v.stop_distance_pct < 0.03) {
    out.push({ tone: 'warn', text: `Only ${(v.stop_distance_pct * 100).toFixed(1)}% above the stop.` })
  }
  if (v.decile !== null && v.decile <= 3) {
    out.push({ tone: 'warn', text: `Scores in decile ${v.decile} — near the bottom of the tracked universe.` })
  } else if (v.decile !== null && v.decile >= 9) {
    out.push({ tone: 'good', text: `Still decile ${v.decile}; the case that got you in is intact.` })
  }
  if (v.to_target_pct !== null && v.to_target_pct <= 0) {
    out.push({ tone: 'good', text: 'Target reached. Nothing in the plan says hold beyond it.' })
  }
  if (setup?.setup_status === 'triggered') {
    out.push({ tone: 'good', text: 'A fresh reversal has confirmed at support.' })
  }
  if (v.return_pct !== null && v.risk_remaining !== null && v.risk_remaining < 0 && v.return_pct > 0) {
    out.push({ tone: 'good', text: 'Above entry with the stop below cost — this position can no longer lose money.' })
  }
  if (!out.length) {
    out.push({ tone: 'good', text: 'Nothing needs attention today.' })
  }
  return out
}


/** The overhead read for one symbol: nearest resistance and how price behaved at it. */
export interface Overhead {
  floor: number | null
  ceil: number | null
  strength: number | null
  false_breakout: {
    broke_on: string
    bars_held: number
    peak: number
    back_below: number
  } | null
  rejected: boolean
}

/** The support side of the same row: the zone, the plan, and what confirmed it. */
export interface Setup {
  status: string
  zone_floor: number | null
  zone_ceil: number | null
  zone_timeframe: string | null
  stop_price: number | null
  target_price: number | null
  reward_risk: number | null
  headroom: number | null
  confirmations: string[]
  caps: string[]
}

/**
 * The whole latest `ts_setups` row for one symbol, split into its two halves.
 *
 * `scores_daily` carries only the handful of setup columns the screener sorts
 * on, so a detail page built from the screener row showed a triggered setup
 * with an empty zone, stop and target — the plan missing from the panel whose
 * entire job is to state the plan. Everything the panel needs lives on
 * `ts_setups`, so read it directly rather than widening the screener payload
 * by ten columns for the one page in five hundred that displays them.
 */
export async function loadSetup(
  symbol: string,
): Promise<{ setup: Setup | null; overhead: Overhead | null }> {
  if (!supabase) return { setup: null, overhead: null }
  const { data, error } = await supabase
    .from('ts_setups')
    .select('*')
    .eq('symbol', symbol)
    .order('date', { ascending: false })
    .limit(1)
  if (error || !data?.length) return { setup: null, overhead: null }

  const r = data[0] as Record<string, any>
  const n = (v: unknown) => (v === null || v === undefined ? null : Number(v))

  const confirmation = (r.confirmation ?? {}) as Record<string, boolean>
  const setup: Setup | null =
    r.setup_status && r.setup_status !== 'none'
      ? {
          status: String(r.setup_status),
          zone_floor: n(r.zone_floor),
          zone_ceil: n(r.zone_ceil),
          zone_timeframe: r.zone_timeframe ?? null,
          stop_price: n(r.stop_price),
          target_price: n(r.target_price),
          reward_risk: n(r.reward_risk),
          headroom: n(r.headroom),
          confirmations: Object.entries(confirmation)
            .filter(([, on]) => on)
            .map(([name]) => name),
          caps: (r.caps ?? []) as string[],
        }
      : null

  const overhead: Overhead | null =
    r.resistance_floor === null || r.resistance_floor === undefined
      ? null
      : {
          floor: n(r.resistance_floor),
          ceil: n(r.resistance_ceil),
          strength: n(r.resistance_strength),
          false_breakout: r.false_breakout ?? null,
          rejected: Boolean(r.rejected_at_resistance),
        }

  return { setup, overhead }
}

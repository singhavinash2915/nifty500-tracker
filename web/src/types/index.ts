export type Setup = 'momentum' | 'support' | 'none'
export type SetupStatus = 'watching' | 'triggered' | 'none'

export interface Flag {
  name: string
  verdict: 'fail' | 'unknown' | 'pass' | 'not_applicable'
  detail: string | null
}

/** One row of the screener: the score plus the inputs that produced it. */
export interface ScreenerRow {
  symbol: string
  company_name: string
  sector: string | null
  company_type: 'financial' | 'general' | null
  close: number | null
  quality_score: number | null
  value_score: number | null
  /** Earnings surprise and acceleration — what is changing, not what is. */
  revision_score: number | null
  /** Promoter and institutional accumulation over the last four quarters. */
  ownership_score: number | null
  tm_score: number | null
  ts_score: number | null
  /** max(T-M, T-S) blended with Q, V, R and O. */
  blended: number | null
  winning_setup: Setup
  setup_status: SetupStatus
  decile: number | null
  flags: Flag[]
  pe: number | null
  roe: number | null
  mom_12_1: number | null
  rs_vs_index: number | null
  dist_52w_high: number | null
  rsi14: number | null
  above_200dma: boolean | null
  stop_price: number | null
  target_price: number | null
  reward_risk: number | null
  headroom: number | null
  zone_floor: number | null
  zone_ceil: number | null
  confirmations: string[]
  caps: string[]
  reason: string | null
  resistance_floor: number | null
  resistance_ceil: number | null
  resistance_strength: number | null
  /** Set when price closed above resistance and then closed back under it. */
  false_breakout: {
    broke_on: string
    bars_held: number
    peak: number
    back_below: number
  } | null
  rejected_at_resistance: boolean
  /** Median daily traded value over 60 sessions, in rupees crore. */
  turnover_60d_cr: number | null
}

export interface ScreenerSnapshot {
  as_of: string
  rows: ScreenerRow[]
}

export interface Bars {
  t: string[]
  o: (number | null)[]
  h: (number | null)[]
  l: (number | null)[]
  c: (number | null)[]
  v: (number | null)[]
}

export interface Zone {
  timeframe: 'daily' | 'weekly'
  kind: 'support' | 'resistance'
  source: string
  floor: number
  ceil: number
  touches: number
  strength: number | null
  /** Rejections as a share of decisive tests. Null when never decisively tested. */
  respect: number | null
  formed_on: string | null
  invalidated_on: string | null
}

export interface PeriodRecord {
  period: string
  [key: string]: number | string | null
}

export interface StockDetail {
  symbol: string
  bars: Bars
  overlays: { sma50?: (number | null)[]; sma200?: (number | null)[] }
  zones: Zone[]
  annual: PeriodRecord[]
  quarterly: PeriodRecord[]
  shareholding: PeriodRecord[]
}

/** Blend weights the user can move. */
export interface Weights {
  quality: number
  value: number
  technical: number
}

/**
 * Mirrors config.DEFAULT_BLEND_WEIGHTS on the Python side — keep them in step.
 *
 * Moved off the plan's 45/20/35 by the weight sweep, whose one significant
 * result was that the quality pillar predicted the next six months negatively
 * (IC -0.041, t = -2.48). This sits between the old weighting and the grid's
 * winner: enough of a cut to stop paying for a pillar the evidence is against,
 * not so much as to adopt a peak fitted to one regime.
 */
export const DEFAULT_WEIGHTS: Weights = { quality: 25, value: 35, technical: 40 }

export interface Position {
  id: number | null
  symbol: string
  entry_date: string | null
  entry_price: number
  quantity: number
  stop_price: number | null
  target_price: number | null
  thesis: string | null
  setup: string | null
  close: number | null
  return_pct?: number
  pnl?: number
  value?: number
  /** Negative means the stop is already breached. */
  stop_distance_pct?: number
  /** What this position can still lose from here. */
  risk_remaining?: number
  to_target_pct?: number
  blended?: number | null
  decile?: number | null
  failed_gates?: string[]
  thesis_intact?: boolean
}

export interface PositionsFile {
  positions: Position[]
  totals: {
    positions?: number
    invested?: number
    value?: number
    pnl?: number
    return_pct?: number | null
    risk_remaining?: number
    thesis_broken?: number
  }
}

export type Severity = 'critical' | 'action' | 'info'

export interface AlertRow {
  symbol: string
  date: string
  rule: string
  message: string
  payload: { severity?: Severity; [key: string]: unknown }
  seen: boolean
}

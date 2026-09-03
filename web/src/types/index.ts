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
  tm_score: number | null
  ts_score: number | null
  /** max(T-M, T-S) blended with Q and V. */
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
  source: string
  floor: number
  ceil: number
  touches: number
  strength: number | null
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

/** Blend weights the user can move. Defaults mirror the Python side. */
export interface Weights {
  quality: number
  value: number
  technical: number
}

export const DEFAULT_WEIGHTS: Weights = { quality: 45, value: 20, technical: 35 }

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

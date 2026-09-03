export interface Stock {
  symbol: string
  company_name: string
  sector: string | null
  industry: string | null
  isin: string | null
  series: string | null
  mcap_cr: number | null
  mcap_band: 'large' | 'mid' | 'small' | null
  is_active: boolean
}

export type Setup = 'momentum' | 'support' | 'none'
export type SetupStatus = 'watching' | 'triggered' | 'none'

/** One row of the screener: the score plus the inputs that produced it. */
export interface ScreenerRow {
  symbol: string
  company_name: string
  sector: string | null
  close: number | null
  /** Momentum / breakout setup. */
  tm_score: number | null
  /** Support-reversal setup. */
  ts_score: number | null
  /** max(T-M, T-S) — the two are alternatives, not components. */
  blended: number | null
  winning_setup: Setup
  setup_status: SetupStatus
  quality_score?: number | null
  value_score?: number | null
  decile: number | null
  mom_12_1: number | null
  rs_vs_index: number | null
  dist_52w_high: number | null
  rsi14: number | null
  above_200dma: boolean | null
  stop_price: number | null
  target_price: number | null
  reward_risk: number | null
  /** Fraction to the next major resistance. */
  headroom: number | null
  zone_floor: number | null
  zone_ceil: number | null
  confirmations: string[]
  caps: string[]
  /** Why T-S was not scored at all. */
  reason: string | null
}

export interface ScreenerSnapshot {
  as_of: string
  rows: ScreenerRow[]
}

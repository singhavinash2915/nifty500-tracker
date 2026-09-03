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
  tm_score: number | null
  ts_score?: number | null
  quality_score?: number | null
  value_score?: number | null
  decile: number | null
  mom_12_1: number | null
  rs_vs_index: number | null
  dist_52w_high: number | null
  rsi14: number | null
  adx14: number | null
  above_200dma: boolean | null
}

export interface ScreenerSnapshot {
  as_of: string
  rows: ScreenerRow[]
}

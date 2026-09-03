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

export interface ScoreRow {
  symbol: string
  date: string
  quality_score: number | null
  value_score: number | null
  tm_score: number | null
  ts_score: number | null
  blended: number | null
  winning_setup: Setup | null
  setup_status: SetupStatus | null
  reward_risk: number | null
  decile: number | null
  flags: string[]
}

import { supabase } from './supabase'
import type { ScreenerRow, ScreenerSnapshot } from '../types'

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

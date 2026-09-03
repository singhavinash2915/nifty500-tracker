import type { ScreenerRow, Weights } from '../types'

/**
 * Re-blend a row against user-chosen weights.
 *
 * Mirrors compute_scores.py exactly: the technical input is max(T-M, T-S),
 * because the two setups are alternatives rather than components, and the
 * weights renormalise over whichever pillars actually exist so a stock with no
 * fundamentals yet is not silently pushed to the bottom.
 *
 * A row excluded by a red flag has no scores at all and stays unscored — it is
 * off the list, not at the bottom of it.
 */
export function reblend(row: ScreenerRow, weights: Weights): number | null {
  const technical = pickTechnical(row)
  const parts: [number | null, number][] = [
    [row.quality_score, weights.quality],
    [row.value_score, weights.value],
    [technical, weights.technical],
  ]

  let weighted = 0
  let available = 0
  for (const [value, weight] of parts) {
    if (value === null || weight <= 0) continue
    weighted += value * weight
    available += weight
  }
  return available > 0 ? weighted / available : null
}

export function pickTechnical(row: ScreenerRow): number | null {
  if (row.tm_score === null) return row.ts_score
  if (row.ts_score === null) return row.tm_score
  return Math.max(row.tm_score, row.ts_score)
}

export function winningSetup(row: ScreenerRow): 'momentum' | 'support' | 'none' {
  if (row.ts_score !== null && (row.tm_score === null || row.ts_score > row.tm_score))
    return 'support'
  if (row.tm_score !== null) return 'momentum'
  return 'none'
}

export function isExcluded(row: ScreenerRow): boolean {
  return row.flags.some((f) => f.verdict === 'fail')
}

/** The gate chain, as counts. Explains why a short list is short. */
export function funnel(rows: ScreenerRow[]) {
  const excluded = rows.filter(isExcluded).length
  const withQuality = rows.filter((r) => r.quality_score !== null).length
  const clearedGate = rows.filter((r) => (r.quality_score ?? 0) >= 60).length
  const atZone = rows.filter((r) => r.setup_status !== 'none').length
  const triggered = rows.filter((r) => r.setup_status === 'triggered').length
  return [
    { label: 'Nifty 500 constituents', value: rows.length },
    { label: 'Survived the red flags', value: rows.length - excluded },
    { label: 'Have a quality score', value: withQuality },
    { label: 'Quality 60 or better', value: clearedGate },
    { label: 'At a live support zone', value: atZone },
    { label: 'Reversal confirmed', value: triggered },
  ]
}

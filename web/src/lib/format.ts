/** A change, signed. "+6.9%" is a return; use `share` for a proportion. */
export const pct = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`

/**
 * A proportion, unsigned.
 *
 * "Deployed +26%" reads as though deployment went up by a quarter. The sign
 * belongs on things that moved, not on the fraction of an account that is
 * invested — which cannot be negative and has no direction.
 */
export const share = (v: number | null | undefined, digits = 1) =>
  v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`

export const num = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined ? '—' : v.toFixed(digits)

/** Indian crore formatting — the unit every Indian financial statement uses. */
export const crore = (v: number | null | undefined) => {
  if (v === null || v === undefined) return '—'
  const abs = Math.abs(v)
  if (abs >= 100000) return `₹${(v / 100000).toFixed(2)}L Cr`
  if (abs >= 1000) return `₹${(v / 1000).toFixed(1)}K Cr`
  return `₹${v.toFixed(0)} Cr`
}

export const shortDate = (iso: string) => {
  const [y, m] = iso.split('-')
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${months[Number(m) - 1]} ${y.slice(2)}`
}

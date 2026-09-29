/**
 * Plain names for the conviction features, for explaining a rank.
 *
 * Describes what was measured and nothing more. Several read as bearish
 * (a shooting star at resistance) yet lift the score: out of sample, stocks
 * showing them went on to outperform, most likely because a stock pressing
 * against resistance is a strong one. The labels do not editorialise that.
 */
export const FEATURE_LABELS: Record<string, string> = {
  false_breakout: 'Failed breakout',
  headroom: 'Close under resistance',
  hanging_man_at_resistance: 'Hanging man at resistance',
  rejected_at_resistance: 'Rejected at resistance',
  shooting_star_at_resistance: 'Shooting star at resistance',
  bearish_engulfing_at_resistance: 'Bearish engulfing at resistance',
  doji_at_resistance: 'Doji at resistance',
  ownership_score: 'Ownership (contrarian)',
  resistance_strength: 'Strong resistance overhead',
  tm_score: 'Momentum',
  zone_respect: 'Support zone respected',
  margin_revision: 'Margin revision (contrarian)',
  value_score: 'Value',
}

/**
 * Signals that went flat in weak markets. Mirrors FADING_IN_WEAK in
 * ingestion/n500/scoring/regime.py, which records the evidence; keep the two
 * in step. The share itself is computed there, nightly.
 */
export const FADING_IN_WEAK = new Set(['tm_score', 'value_score', 'margin_revision'])

/** A share of the lift at or above this is flagged in a weak market. */
export const FADING_FLAG_AT = 0.5

export const featureLabel = (name: string) => FEATURE_LABELS[name] ?? name.replace(/_/g, ' ')

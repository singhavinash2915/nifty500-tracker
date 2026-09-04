import type { ScreenerRow } from '../types'
import type { PortfolioSettings, PositionView } from './load'

/**
 * The step between a ranked list and a decision.
 *
 * A screener sorted by a number is not actionable. Acting on it means knowing
 * how many shares, at what stop, risking what — and 494 rows do not answer
 * that, so the answer gets worked out by hand for the first name that catches
 * the eye, which is how position sizes end up set by attention rather than by
 * risk.
 *
 * Everything here is arithmetic on facts the tracker already holds. No new
 * signal, no new opinion about which stock is good: the ranking is conviction's
 * job and this only turns it into share counts.
 */

/** Above this share of capital in one sector, adding more concentrates rather than diversifies. */
export const SECTOR_CAP = 0.25

/**
 * No single position gets more of the account than this, whatever the stop says.
 *
 * Risk-unit sizing has a failure mode that only shows up once you run it over a
 * ranked list. Shares = unit / (price - stop), so a stop 4% away buys a position
 * worth 25% of capital for the same 1% of risk. Run down a shortlist and the
 * first three names consume the entire account.
 *
 * The arithmetic is right and the conclusion is wrong, because it assumes the
 * stop is the only way to lose. A gap through it on bad news does not respect
 * the level, and a 25% position gapping 15% is a 3.75% loss of capital from a
 * trade that was sized to risk 1%. So the position is capped and the risk taken
 * falls below a full unit — which is the correct trade-off and is reported
 * rather than hidden.
 */
export const MAX_POSITION_PCT = 0.10

export interface Candidate {
  row: ScreenerRow
  stop: number
  stop_basis: string | null
  target: number | null
  reward_risk: number | null
  /** Shares: one risk unit, or the position cap, whichever binds first. */
  shares: number
  cost: number
  weight: number
  risk: number
  /** Fraction of a full risk unit this actually takes. Below 1 when capped. */
  risk_units: number
  capped: boolean
  /** Distance to the next resistance band, as a fraction of the price. */
  headroom: number | null
  stop_pct: number
  warnings: string[]
}

export interface ShortlistInput {
  rows: ScreenerRow[]
  plans: Map<string, { stop: number | null; target: number | null; reward_risk: number | null; basis: string | null }>
  held: PositionView[]
  settings: PortfolioSettings | null
  limit?: number
}

/**
 * Candidates you could act on today, sized.
 *
 * Ordering is conviction and nothing else. The filters below remove names that
 * cannot be acted on rather than names that score badly — a stock already held
 * is not a new idea, and one with no stop cannot be sized at all.
 */
export function buildShortlist({
  rows, plans, held, settings, limit = 12,
}: ShortlistInput): Candidate[] {
  if (!settings) return []

  const owned = new Set(held.map((p) => p.symbol))
  const capital = settings.total_capital
  const unit = capital * settings.risk_pct

  // Sector exposure already carried, so a candidate can be told what adding it
  // would do rather than only what the book looks like now.
  const bySector = new Map<string, number>()
  for (const p of held) {
    const sector = p.sector ?? 'Unknown'
    bySector.set(sector, (bySector.get(sector) ?? 0) + (p.value ?? 0))
  }

  const out: Candidate[] = []
  for (const row of rows) {
    if (out.length >= limit) break
    if (owned.has(row.symbol)) continue
    if (row.conviction === null) continue
    if (row.flags.some((f) => f.verdict === 'fail')) continue

    const plan = plans.get(row.symbol)
    const price = row.close
    if (!plan?.stop || !price || plan.stop >= price) continue

    const riskPerShare = price - plan.stop
    const byRisk = Math.floor(unit / riskPerShare)
    const byCap = Math.floor((capital * MAX_POSITION_PCT) / price)
    const shares = Math.min(byRisk, byCap)
    if (shares < 1) continue      // one share breaks both limits

    const capped = byCap < byRisk
    const cost = shares * price
    const sector = row.sector ?? 'Unknown'
    const sectorAfter = ((bySector.get(sector) ?? 0) + cost) / capital

    const risk = shares * riskPerShare
    const warnings: string[] = []
    if (sectorAfter > SECTOR_CAP) {
      warnings.push(
        `${sector} would reach ${(sectorAfter * 100).toFixed(0)}% of capital — above the ${SECTOR_CAP * 100}% cap`,
      )
    }
    if (capped) {
      warnings.push(
        `The stop is ${((riskPerShare / price) * 100).toFixed(1)}% away, so a full risk unit would need ` +
        `${((byRisk * price) / capital * 100).toFixed(0)}% of capital. Capped at ` +
        `${MAX_POSITION_PCT * 100}%, which takes ${(risk / unit).toFixed(2)} of a unit instead.`,
      )
    }
    if (row.above_200dma === false) {
      warnings.push('Below the 200-day average.')
    }

    out.push({
      row,
      stop: plan.stop,
      stop_basis: plan.basis,
      target: plan.target,
      reward_risk: plan.reward_risk,
      shares,
      cost,
      weight: cost / capital,
      risk,
      risk_units: risk / unit,
      capped,
      // Reported as distance rather than as reward-to-risk on purpose. A ratio
      // implies the level is where you sell, and conviction ranks stocks
      // *pressed under* resistance highly — `headroom` is a negative-signed
      // feature — so a near level is the setup working, not a poor trade. The
      // held-out evidence says these get exceeded more often than they cap.
      headroom: plan.target && price ? plan.target / price - 1 : null,
      stop_pct: riskPerShare / price,
      warnings,
    })
  }
  return out
}

/**
 * What the book looks like now, so the shortlist can be read against it.
 *
 * Deployment is the number this whole page exists to make visible: seven
 * positions at a fraction of a risk unit each, with three quarters of the
 * account in cash, is a different problem from a badly ranked screener and
 * nothing in the app was saying so.
 */
export function capacity(held: PositionView[], settings: PortfolioSettings | null) {
  if (!settings) return null
  const deployed = held.reduce((sum, p) => sum + (p.value ?? 0), 0)
  const risked = held.reduce(
    (sum, p) => sum + (p.risk_share ?? 0) * settings.total_capital, 0,
  )
  return {
    capital: settings.total_capital,
    unit: settings.total_capital * settings.risk_pct,
    deployed,
    free: settings.total_capital - deployed,
    deployed_pct: deployed / settings.total_capital,
    risked,
    risked_pct: risked / settings.total_capital,
    /** Risk units still unused, if the book is meant to carry one per position. */
    units_free: Math.max(
      0, Math.floor((settings.total_capital * 0.06 - risked) / (settings.total_capital * settings.risk_pct)),
    ),
  }
}

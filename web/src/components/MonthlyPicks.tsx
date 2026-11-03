import { Link } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import type { MonthlyPick, ScreenerRow } from '../types'
import type { PortfolioSettings, PositionView } from '../lib/load'
import { basketEffect, sizePick } from '../lib/shortlist'
import { pct, share, shortDate } from '../lib/format'

const rupees = (v: number) => `₹${Math.round(v).toLocaleString('en-IN')}`

/**
 * The month's three stocks, locked on its first scored session.
 *
 * The buy list below moves every night by design, so it cannot answer "which
 * three this month?" and still give the same answer tomorrow. These do not move.
 * The share counts do: they are worked out at the latest close, because that is
 * nearer what you would pay than the close on the day of the lock.
 */
export function MonthlyPicks({
  picks, rows, held, settings,
}: {
  picks: MonthlyPick[]
  rows: ScreenerRow[]
  held: PositionView[]
  settings: PortfolioSettings | null
}) {
  if (!picks.length) return null

  const owned = new Set(held.map((p) => p.symbol))
  const closes = new Map(rows.map((r) => [r.symbol, r.close]))
  const sized = picks.map((pick) => ({
    pick,
    bought: owned.has(pick.symbol),
    ...sizePick(pick, closes.get(pick.symbol) ?? pick.close),
  }))

  // Only what is still to buy and can still be bought counts against the book.
  const toBuy = sized.filter((s) => !s.bought && !s.belowStop && s.shares > 0)
  const effect = basketEffect(
    toBuy.map((s) => ({ cost: s.cost, risk: s.risk, row: { sector: s.pick.sector } })),
    held,
    settings,
  )

  return (
    <section className="mb-6 rounded-md border border-slate-300 bg-white p-4 dark:border-slate-700 dark:bg-slate-900">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-semibold">This month's picks</h2>
        <span className="font-mono text-[11px] text-slate-500">
          {shortDate(picks[0].month)} · locked from {picks[0].as_of}
        </span>
      </div>
      <p className="mb-3 max-w-3xl text-sm text-slate-600 dark:text-slate-400">
        The top 3 by conviction on the month's first session, one per sector, skipping what you
        already hold. They stay fixed until next month. Each position is limited to{' '}
        {picks[0].position_budget !== null && rupees(picks[0].position_budget)} and{' '}
        {picks[0].risk_budget !== null && rupees(picks[0].risk_budget)} of risk, so 3 a month held
        for 6 months stays inside your capital and your 6% risk budget.
      </p>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead>
            <tr className="text-left font-mono text-[10px] uppercase tracking-wider text-slate-500">
              <th className="pb-2 pr-4 font-normal">Stock</th>
              <th className="pb-2 pr-4 font-normal">Buy</th>
              <th className="pb-2 pr-4 font-normal">Cost</th>
              <th className="pb-2 pr-4 font-normal">Stop</th>
              <th className="pb-2 pr-4 font-normal">Risk if stopped</th>
              <th className="pb-2 font-normal">Since lock</th>
            </tr>
          </thead>
          <tbody>
            {sized.map(({ pick, bought, belowStop, shares, close, cost, risk }) => (
              <tr key={pick.symbol} className="border-t border-slate-100 dark:border-slate-800">
                <td className="py-2 pr-4">
                  <Link to={`/stock/${pick.symbol}`} className="font-mono font-semibold hover:underline">
                    {pick.symbol}
                  </Link>
                  <span className="block text-xs text-slate-500">
                    {pick.sector} · conviction {pick.conviction.toFixed(1)}
                  </span>
                </td>
                {bought ? (
                  <td colSpan={2} className="py-2 pr-4 text-slate-500">Held</td>
                ) : belowStop ? (
                  <td colSpan={2} className="py-2 pr-4">
                    <span className="flex items-start gap-1.5 text-amber-900 dark:text-amber-300">
                      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                      Below its stop, so it no longer qualifies
                    </span>
                  </td>
                ) : (
                  <>
                    <td className="py-2 pr-4 font-mono tabular-nums">
                      {shares > 0 ? `${shares.toLocaleString('en-IN')} @ ${close.toFixed(2)}` : '—'}
                    </td>
                    <td className="py-2 pr-4 font-mono tabular-nums">{rupees(cost)}</td>
                  </>
                )}
                <td className="py-2 pr-4 font-mono tabular-nums">
                  {pick.stop.toFixed(2)}
                  <span className="block text-xs text-slate-500">
                    {pick.stop_basis ?? ''} {close > pick.stop ? `· ${share((close - pick.stop) / close, 1)} away` : ''}
                  </span>
                </td>
                <td className="py-2 pr-4 font-mono tabular-nums">
                  {bought || belowStop ? '—' : rupees(risk)}
                </td>
                <td className="py-2 font-mono tabular-nums">{pct(close / pick.close - 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {effect && toBuy.length > 0 && (
        <p className="mt-3 text-sm text-slate-700 dark:text-slate-300">
          Buying {toBuy.length === sized.length ? 'all of them' : `the ${toBuy.length} left`} costs{' '}
          <strong className="font-mono tabular-nums">{rupees(effect.cost)}</strong> and takes open
          risk to <strong className="font-mono tabular-nums">{share(effect.risked_after, 2)}</strong>{' '}
          of capital, with {share(effect.deployed_after, 0)} deployed.
        </p>
      )}
      {effect && effect.warnings.length > 0 && (
        <ul className="mt-2 grid gap-1 text-sm">
          {effect.warnings.map((w) => (
            <li key={w} className="flex items-start gap-2 text-amber-900 dark:text-amber-300">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{w}</span>
            </li>
          ))}
        </ul>
      )}

      <p className="mt-3 max-w-3xl text-xs text-slate-500">
        Tested over 38 months with no costs or stops: +22.3% over six months against +11.0% for
        the average stock, but only 39% of picks reached +25%, and in about 1 month in 5 the three
        lost money together. A rule to apply consistently, not a recommendation to buy.
      </p>
    </section>
  )
}

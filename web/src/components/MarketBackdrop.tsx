import { useEffect, useState } from 'react'
import type { MarketBackdrop as Backdrop } from '../types'
import { loadBackdrop } from '../lib/load'
import { pct, share } from '../lib/format'

/**
 * How much weight the list deserves in today's market.
 *
 * "The market is down, should I trust the screener?" deserves a number. This
 * puts today's backdrop beside what the conviction ranking earned in past
 * months with the same one. It says nothing about where the market goes next:
 * the history is about stock selection, and the study found no reliable
 * timing signal. See ingestion/n500/scoring/regime.py.
 */
export function MarketBackdrop({
  compact = false, data,
}: {
  compact?: boolean
  /** Already loaded by the page; skips the fetch. */
  data?: Backdrop | null
}) {
  const [loaded, setLoaded] = useState<Backdrop | null>(null)

  useEffect(() => {
    if (data !== undefined) return
    let cancelled = false
    loadBackdrop().then((row) => {
      if (!cancelled) setLoaded(row)
    })
    return () => {
      cancelled = true
    }
  }, [data])

  const b = data !== undefined ? data : loaded

  if (!b) return null
  const weak = b.regime === 'weak'
  const edge = b.hist_top !== null && b.hist_avg !== null ? b.hist_top - b.hist_avg : null

  return (
    <section className="mb-6 rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-mono text-[11px] uppercase tracking-wider text-slate-500">
          Market backdrop
        </h2>
        <span className="font-mono text-[11px] text-slate-500">as of {b.date}</span>
      </div>

      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <span className={`rounded px-2.5 py-1 text-sm font-semibold ${
          weak
            ? 'bg-amber-100 text-amber-900 dark:bg-amber-950/60 dark:text-amber-200'
            : 'bg-emerald-100 text-emerald-900 dark:bg-emerald-950/60 dark:text-emerald-200'
        }`}>
          {weak ? 'Weak market' : 'Strong market'}
        </span>
        <Stat value={share(b.breadth, 0)} label="of stocks above their 200-day average" />
        {/* Phones get the verdict and the history; the rest is a screen of
            preamble before the list, as with the screener's other panels. */}
        {!compact && (
          <span className="hidden sm:contents">
            <Stat value={pct(b.drawdown)} label="off the 52-week high" />
            <Stat value={pct(b.vs_200dma)} label="vs the 200-day average" />
            <Stat value={pct(b.return_3m)} label="over 3 months" />
          </span>
        )}
      </div>

      {b.hist_months !== null && edge !== null && (
        <p className="mt-3 max-w-3xl text-sm text-slate-700 dark:text-slate-300">
          In the {b.hist_months} past months like this, the top 10% by conviction returned{' '}
          <strong className="font-mono tabular-nums">{pct(b.hist_top)}</strong> over the next
          six months, against <span className="font-mono tabular-nums">{pct(b.hist_avg)}</span>{' '}
          for the average stock: an edge of{' '}
          <strong className="font-mono tabular-nums">{pct(edge)}</strong>. The ranking helped in{' '}
          {share(b.hist_ic_positive, 0)} of them.
          {weak && ' In weak markets momentum and value stopped working and the price-structure signals carried the ranking, so buy-list cards flag names that lean on the first two.'}
        </p>
      )}

      {!compact && (
        <p className="mt-2 hidden max-w-3xl text-xs text-slate-500 sm:block">
          Breadth and index figures are equal-weighted across every stock with a price, as in
          the study ({b.study}).
          {weak && b.hist_episodes ? ` Those weak months come from about ${b.hist_episodes} separate sell-offs, so read this` : ' Read this'}
          {' '}as how much weight the list has deserved in markets like this, not as a forecast
          for the market.
        </p>
      )}
    </section>
  )
}

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <span className="text-sm text-slate-600 dark:text-slate-400">
      <span className="mr-1 font-mono text-base font-semibold tabular-nums text-slate-900 dark:text-slate-100">
        {value}
      </span>
      {label}
    </span>
  )
}

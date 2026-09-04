import { useEffect, useState } from 'react'
import { supabase } from '../lib/supabase'

/**
 * Live index levels across the top of the screener.
 *
 * Indices only. NSE's per-stock quote endpoint returns 403 to this connection
 * and Yahoo rate-limits it, so live prices for the 500 constituents would need
 * a broker API. The index level is arguably the more useful half regardless:
 * it tells you whether a stock is falling on its own or with everything else.
 *
 * The exchange's own timestamp is shown rather than "live", because the poller
 * runs every five minutes and can miss one — a number that says how old it is
 * beats a badge that claims to be current.
 */
interface Quote {
  name: string
  last: number | null
  pct_change: number | null
  as_of: string | null
  fetched_at: string
}

const ORDER = [
  'NIFTY 50', 'NIFTY BANK', 'NIFTY 500', 'NIFTY MIDCAP 150',
  'NIFTY SMALLCAP 250', 'NIFTY IT', 'INDIA VIX',
]

export function MarketStrip() {
  const [quotes, setQuotes] = useState<Quote[]>([])

  useEffect(() => {
    if (!supabase) return
    let cancelled = false

    const client = supabase
    const load = async () => {
      const { data } = await client.from('live_quotes').select('*')
      if (!cancelled && data) {
        const by = new Map(data.map((q: Quote) => [q.name, q]))
        setQuotes(ORDER.map((n) => by.get(n)).filter(Boolean) as Quote[])
      }
    }
    load()
    // Matches the poller's cadence; polling faster only re-reads the same row.
    const timer = setInterval(load, 60_000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  if (!quotes.length) return null
  const stamp = quotes[0]?.as_of

  return (
    <section className="mb-6 rounded-md border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-2 flex items-baseline justify-between">
        <h2 className="font-mono text-[11px] uppercase tracking-wider text-slate-500">
          Market
        </h2>
        {stamp && (
          <span className="font-mono text-[11px] text-slate-400">NSE {stamp}</span>
        )}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2">
        {quotes.map((q) => {
          const up = (q.pct_change ?? 0) >= 0
          // VIX rising is the market getting more frightened, so the usual
          // green-is-good colouring would read backwards here.
          const inverted = q.name === 'INDIA VIX'
          const good = inverted ? !up : up
          return (
            <div key={q.name} className="min-w-[7rem]">
              <p className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
                {q.name.replace('NIFTY ', '')}
              </p>
              <p className="font-mono text-sm tabular-nums">
                {q.last?.toLocaleString('en-IN', { maximumFractionDigits: 1 }) ?? '—'}
              </p>
              <p className={`font-mono text-xs tabular-nums ${
                good ? 'text-emerald-700 dark:text-emerald-400'
                     : 'text-red-700 dark:text-red-400'}`}>
                {up ? '+' : ''}{q.pct_change?.toFixed(2)}%
              </p>
            </div>
          )
        })}
      </div>
    </section>
  )
}

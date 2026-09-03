import { useEffect, useMemo, useState } from 'react'
import { Database, Search, TriangleAlert } from 'lucide-react'
import { isConfigured, supabase } from './lib/supabase'
import type { Stock } from './types'

// Sector-relative percentile ranking needs enough peers to be meaningful.
// Sectors below this size get pooled into an all-stocks ranking instead.
const MIN_PEERS = 10

type Source = 'supabase' | 'sample'

export default function App() {
  const [stocks, setStocks] = useState<Stock[]>([])
  const [source, setSource] = useState<Source>('sample')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')

  useEffect(() => {
    let cancelled = false

    async function load() {
      if (supabase) {
        const { data, error } = await supabase
          .from('stocks')
          .select('*')
          .eq('is_active', true)
          .order('symbol')
        if (!cancelled && !error && data) {
          setStocks(data as Stock[])
          setSource('supabase')
          setLoading(false)
          return
        }
        if (!cancelled && error) setError(error.message)
      }
      // Falls back to the dry-run snapshot so the shell renders before the
      // Supabase project exists.
      const res = await fetch(`${import.meta.env.BASE_URL}universe-sample.json`)
      const data = (await res.json()) as Stock[]
      if (!cancelled) {
        setStocks(data)
        setSource('sample')
        setLoading(false)
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [])

  const sectors = useMemo(() => {
    const counts = new Map<string, number>()
    for (const s of stocks) {
      const key = s.sector ?? 'Unknown'
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [stocks])

  const thinSectors = sectors.filter(([, n]) => n < MIN_PEERS)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return stocks
    return stocks.filter(
      (s) =>
        s.symbol.toLowerCase().includes(q) ||
        s.company_name.toLowerCase().includes(q) ||
        (s.sector ?? '').toLowerCase().includes(q),
    )
  }, [stocks, query])

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-8">
        <p className="font-mono text-xs uppercase tracking-[0.15em] text-slate-500">
          Phase 1 &middot; universe
        </p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight">
          Nifty 500 Conviction Tracker
        </h1>
        <p className="mt-2 max-w-2xl text-slate-600 dark:text-slate-400">
          The skeleton. Scores, zones and the screener arrive in later phases &mdash;
          this page confirms the universe loads and the peer groups are sane.
        </p>
      </header>

      {!isConfigured && (
        <div className="mb-6 flex gap-3 rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-semibold">Supabase not configured</p>
            <p className="mt-1">
              Showing the dry-run snapshot from <code className="font-mono">data/dryrun/</code>.
              Add <code className="font-mono">VITE_SUPABASE_URL</code> and{' '}
              <code className="font-mono">VITE_SUPABASE_ANON_KEY</code> to{' '}
              <code className="font-mono">web/.env.local</code> to read live data.
            </p>
          </div>
        </div>
      )}

      {error && (
        <p className="mb-6 rounded-md border border-red-300 bg-red-50 p-4 text-sm text-red-800 dark:border-red-900 dark:bg-red-950/40 dark:text-red-200">
          Supabase read failed: {error}
        </p>
      )}

      <div className="mb-8 grid gap-4 sm:grid-cols-3">
        <Stat label="Constituents" value={loading ? '—' : String(stocks.length)} />
        <Stat label="Sectors" value={loading ? '—' : String(sectors.length)} />
        <Stat
          label="Source"
          value={source === 'supabase' ? 'Supabase' : 'Dry-run snapshot'}
          icon={<Database className="h-4 w-4" />}
        />
      </div>

      {thinSectors.length > 0 && (
        <div className="mb-8 rounded-md border border-slate-200 bg-white p-4 text-sm dark:border-slate-800 dark:bg-slate-900">
          <p className="font-semibold">
            {thinSectors.length} sector{thinSectors.length === 1 ? '' : 's'} below{' '}
            {MIN_PEERS} members
          </p>
          <p className="mt-1 text-slate-600 dark:text-slate-400">
            Percentile ranking inside these is not meaningful, so the scoring engine
            will pool them into an all-stocks ranking:{' '}
            {thinSectors.map(([name, n]) => `${name} (${n})`).join(', ')}.
          </p>
        </div>
      )}

      <div className="mb-3 flex items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-2 dark:border-slate-700 dark:bg-slate-900">
        <Search className="h-4 w-4 text-slate-400" />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filter by symbol, company or sector"
          className="w-full bg-transparent text-sm outline-none placeholder:text-slate-400"
        />
      </div>

      <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-800">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-slate-100 text-left font-mono text-[11px] uppercase tracking-wider text-slate-500 dark:bg-slate-900">
              <th className="px-4 py-2">Symbol</th>
              <th className="px-4 py-2">Company</th>
              <th className="px-4 py-2">Sector</th>
              <th className="px-4 py-2">ISIN</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 100).map((s) => (
              <tr
                key={s.symbol}
                className="border-t border-slate-200 dark:border-slate-800"
              >
                <td className="px-4 py-2 font-mono font-medium">{s.symbol}</td>
                <td className="px-4 py-2 text-slate-600 dark:text-slate-300">
                  {s.company_name}
                </td>
                <td className="px-4 py-2 text-slate-600 dark:text-slate-400">
                  {s.sector}
                </td>
                <td className="px-4 py-2 font-mono text-xs text-slate-500">{s.isin}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {filtered.length > 100 && (
        <p className="mt-3 text-sm text-slate-500">
          Showing 100 of {filtered.length}. Paging and sorting land with the screener.
        </p>
      )}
    </div>
  )
}

function Stat({
  label,
  value,
  icon,
}: {
  label: string
  value: string
  icon?: React.ReactNode
}) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <p className="flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wider text-slate-500">
        {icon}
        {label}
      </p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
    </div>
  )
}

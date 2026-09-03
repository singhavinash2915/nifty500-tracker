import { useEffect, useMemo, useState } from 'react'
import { Database, Search, TriangleAlert } from 'lucide-react'
import { isConfigured, supabase } from './lib/supabase'
import type { ScreenerRow, ScreenerSnapshot } from './types'

// A stock below its 200DMA is capped at this score, however well it ranks on
// everything else. Mirrors momentum.BELOW_200DMA_CAP on the Python side.
const BELOW_200DMA_CAP = 40

type Source = 'supabase' | 'sample'
type Filter = 'all' | 'leaders' | 'above200' | 'laggards'

export default function App() {
  const [rows, setRows] = useState<ScreenerRow[]>([])
  const [asOf, setAsOf] = useState<string>('')
  const [source, setSource] = useState<Source>('sample')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')

  useEffect(() => {
    let cancelled = false

    async function load() {
      if (supabase) {
        const { data, error } = await supabase
          .from('scores_daily')
          .select('*, stocks(company_name, sector)')
          .order('blended', { ascending: false })
        if (!cancelled && !error && data?.length) {
          setSource('supabase')
          setLoading(false)
          return
        }
        if (!cancelled && error) setError(error.message)
      }
      const res = await fetch(`${import.meta.env.BASE_URL}scores-sample.json`)
      const snapshot = (await res.json()) as ScreenerSnapshot
      if (!cancelled) {
        setRows(snapshot.rows)
        setAsOf(snapshot.as_of)
        setSource('sample')
        setLoading(false)
      }
    }

    load()
    return () => {
      cancelled = true
    }
  }, [])

  const breadth = useMemo(() => {
    const known = rows.filter((r) => r.above_200dma !== null)
    if (!known.length) return null
    return known.filter((r) => r.above_200dma).length / known.length
  }, [rows])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return rows.filter((r) => {
      if (filter === 'leaders' && (r.decile ?? 0) < 9) return false
      if (filter === 'above200' && !r.above_200dma) return false
      if (filter === 'laggards' && (r.decile ?? 99) > 2) return false
      if (!q) return true
      return (
        r.symbol.toLowerCase().includes(q) ||
        r.company_name.toLowerCase().includes(q) ||
        (r.sector ?? '').toLowerCase().includes(q)
      )
    })
  }, [rows, query, filter])

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-8">
        <p className="font-mono text-xs uppercase tracking-[0.15em] text-slate-500">
          Phase 2 &middot; momentum
        </p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight">
          Nifty 500 Conviction Tracker
        </h1>
        <p className="mt-2 max-w-2xl text-slate-600 dark:text-slate-400">
          T-M scores every constituent on trend, relative strength and accumulation.
          Quality, value and the support-reversal setup arrive in later phases.
          {asOf && <> Prices as of <span className="font-mono">{asOf}</span>.</>}
        </p>
      </header>

      {!isConfigured && (
        <div className="mb-6 flex gap-3 rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <div>
            <p className="font-semibold">Supabase not configured</p>
            <p className="mt-1">
              Showing the dry-run snapshot. Add{' '}
              <code className="font-mono">VITE_SUPABASE_URL</code> and{' '}
              <code className="font-mono">VITE_SUPABASE_ANON_KEY</code> to{' '}
              <code className="font-mono">web/.env.local</code> for live data.
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
        <Stat label="Scored" value={loading ? '—' : String(rows.length)} />
        <Stat
          label="Above 200DMA"
          value={breadth === null ? '—' : `${Math.round(breadth * 100)}%`}
          hint="market breadth"
        />
        <Stat
          label="Source"
          value={source === 'supabase' ? 'Supabase' : 'Dry-run snapshot'}
          icon={<Database className="h-4 w-4" />}
        />
      </div>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {(
          [
            ['all', 'All'],
            ['leaders', 'Top 2 deciles'],
            ['above200', 'Above 200DMA'],
            ['laggards', 'Bottom 2 deciles'],
          ] as [Filter, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`rounded-md border px-3 py-1.5 text-sm transition ${
              filter === key
                ? 'border-slate-900 bg-slate-900 text-white dark:border-slate-100 dark:bg-slate-100 dark:text-slate-900'
                : 'border-slate-300 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800'
            }`}
          >
            {label}
          </button>
        ))}
        <div className="ml-auto flex min-w-56 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-1.5 dark:border-slate-700 dark:bg-slate-900">
          <Search className="h-4 w-4 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Symbol, company or sector"
            className="w-full bg-transparent text-sm outline-none placeholder:text-slate-400"
          />
        </div>
      </div>

      <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-800">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-slate-100 text-left font-mono text-[11px] uppercase tracking-wider text-slate-500 dark:bg-slate-900">
              <th className="px-3 py-2">Symbol</th>
              <th className="px-3 py-2">Sector</th>
              <th className="px-3 py-2 text-right">Close</th>
              <th className="px-3 py-2 text-right">T-M</th>
              <th className="px-3 py-2 text-right">12-1 mom</th>
              <th className="px-3 py-2 text-right">RS</th>
              <th className="px-3 py-2 text-right">Off 52wH</th>
              <th className="px-3 py-2 text-right">RSI</th>
              <th className="px-3 py-2 text-right">ADX</th>
            </tr>
          </thead>
          <tbody>
            {filtered.slice(0, 100).map((r) => (
              <tr key={r.symbol} className="border-t border-slate-200 dark:border-slate-800">
                <td className="px-3 py-2">
                  <span className="font-mono font-medium">{r.symbol}</span>
                  <span className="ml-2 text-xs text-slate-500">{r.company_name}</span>
                </td>
                <td className="px-3 py-2 text-xs text-slate-500">{r.sector}</td>
                <td className="px-3 py-2 text-right font-mono tabular-nums">
                  {r.close?.toFixed(2) ?? '—'}
                </td>
                <td className="px-3 py-2 text-right">
                  <ScoreChip value={r.tm_score} capped={r.above_200dma === false} />
                </td>
                <td className="px-3 py-2 text-right"><Pct value={r.mom_12_1} /></td>
                <td className="px-3 py-2 text-right"><Pct value={r.rs_vs_index} /></td>
                <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500">
                  {r.dist_52w_high === null ? '—' : `${(r.dist_52w_high * 100).toFixed(1)}%`}
                </td>
                <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500">
                  {r.rsi14?.toFixed(0) ?? '—'}
                </td>
                <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500">
                  {r.adx14?.toFixed(0) ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {filtered.length > 100 && (
        <p className="mt-3 text-sm text-slate-500">
          Showing 100 of {filtered.length}. Sorting and paging land with the full screener.
        </p>
      )}
      <p className="mt-6 text-xs text-slate-500">
        A stock below its 200DMA is capped at {BELOW_200DMA_CAP} however well it ranks
        elsewhere — marked with a dot. For personal research. Not investment advice.
      </p>
    </div>
  )
}

function ScoreChip({ value, capped }: { value: number | null; capped: boolean }) {
  if (value === null) return <span className="text-slate-400">—</span>
  const tone =
    value >= 70
      ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300'
      : value >= 45
        ? 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300'
        : 'bg-red-100 text-red-800 dark:bg-red-950/60 dark:text-red-300'
  return (
    <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 font-mono text-xs font-semibold tabular-nums ${tone}`}>
      {capped && <span title="below its 200DMA — score capped">&bull;</span>}
      {value.toFixed(1)}
    </span>
  )
}

function Pct({ value }: { value: number | null }) {
  if (value === null) return <span className="text-slate-400">—</span>
  const tone = value >= 0 ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-700 dark:text-red-400'
  return (
    <span className={`font-mono tabular-nums ${tone}`}>
      {value >= 0 ? '+' : ''}
      {(value * 100).toFixed(1)}%
    </span>
  )
}

function Stat({
  label,
  value,
  icon,
  hint,
}: {
  label: string
  value: string
  icon?: React.ReactNode
  hint?: string
}) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <p className="flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wider text-slate-500">
        {icon}
        {label}
      </p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

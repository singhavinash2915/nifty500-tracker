import { useEffect, useState } from 'react'
import { HashRouter, Link, Route, Routes } from 'react-router-dom'
import { TriangleAlert } from 'lucide-react'
import type { ScreenerRow } from './types'
import { loadScreener } from './lib/load'
import { Portfolio } from './pages/Portfolio'
import { Screener } from './pages/Screener'
import { StockDetail } from './pages/StockDetail'

export default function App() {
  const [rows, setRows] = useState<ScreenerRow[]>([])
  const [asOf, setAsOf] = useState('')
  const [source, setSource] = useState<'supabase' | 'snapshot'>('snapshot')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    loadScreener().then(({ snapshot, source, error }) => {
      if (cancelled) return
      setRows(snapshot.rows)
      setAsOf(snapshot.as_of)
      setSource(source)
      setError(error)
      setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    // Hash routing so deep links survive GitHub Pages, which has no rewrite rule.
    <HashRouter>
      <div className="mx-auto max-w-6xl px-6 py-10">
        <header className="mb-8">
          <p className="font-mono text-xs uppercase tracking-[0.15em] text-slate-500">
            Phase 7 &middot; complete
          </p>
          <div className="mt-2 flex flex-wrap items-baseline gap-x-4">
            <Link to="/" className="text-3xl font-bold tracking-tight hover:opacity-80">
              Nifty 500 Conviction Tracker
            </Link>
            <Link
              to="/portfolio"
              className="text-sm text-slate-500 underline underline-offset-4 hover:text-slate-900 dark:hover:text-slate-100"
            >
              Positions &amp; alerts
            </Link>
          </div>
          <p className="mt-2 max-w-2xl text-slate-600 dark:text-slate-400">
            Four scores. <strong>Q</strong> and <strong>V</strong> judge the business;{' '}
            <strong>T-M</strong> rewards a stock making new highs and <strong>T-S</strong>{' '}
            one reversing off a tested support zone. A business failing a hard gate is
            excluded outright rather than scored poorly.
          </p>
        </header>

        {source === 'snapshot' && (
          <div className="mb-6 flex gap-3 rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <p className="font-semibold">
                {error ? 'Live read failed — showing the exported snapshot' : 'Reading the exported snapshot'}
              </p>
              <p className="mt-1">
                {error ??
                  'Add VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY to web/.env.local to read live.'}
              </p>
            </div>
          </div>
        )}
        {source === 'supabase' && (
          <p className="mb-6 font-mono text-[11px] uppercase tracking-wider text-slate-500">
            live from Supabase &middot; {rows.length} scored
          </p>
        )}

        {loading ? (
          <p className="text-sm text-slate-500">Loading the snapshot…</p>
        ) : (
          <Routes>
            <Route path="/" element={<Screener rows={rows} asOf={asOf} />} />
            <Route path="/stock/:symbol" element={<StockDetail rows={rows} />} />
            <Route path="/portfolio" element={<Portfolio />} />
          </Routes>
        )}
      </div>
    </HashRouter>
  )
}

import { useEffect, useMemo, useState } from 'react'
import { Database, Search, TriangleAlert } from 'lucide-react'
import { isConfigured, supabase } from './lib/supabase'
import type { ScreenerRow, ScreenerSnapshot } from './types'

// Mirrors momentum.BELOW_200DMA_CAP on the Python side.
const BELOW_200DMA_CAP = 40

type View = 'all' | 'momentum' | 'support'

const CONFIRMATION_LABELS: Record<string, string> = {
  bullish_candle: 'reversal candle',
  rsi_divergence: 'RSI divergence',
  macd_turning_up: 'MACD turning up',
  reclaimed_short_ma: 'reclaimed 20DMA',
  volume_pattern: 'volume dry-up then expansion',
}

export default function App() {
  const [rows, setRows] = useState<ScreenerRow[]>([])
  const [asOf, setAsOf] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [view, setView] = useState<View>('all')

  useEffect(() => {
    let cancelled = false

    async function load() {
      if (supabase) {
        const { error } = await supabase.from('scores_daily').select('symbol').limit(1)
        if (!cancelled && error) setError(error.message)
      }
      const res = await fetch(`${import.meta.env.BASE_URL}scores-sample.json`)
      const snapshot = (await res.json()) as ScreenerSnapshot
      if (!cancelled) {
        setRows(snapshot.rows)
        setAsOf(snapshot.as_of)
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
    return known.length ? known.filter((r) => r.above_200dma).length / known.length : null
  }, [rows])

  const atSupport = useMemo(
    () => rows.filter((r) => r.setup_status !== 'none'),
    [rows],
  )

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    const base =
      view === 'support'
        ? atSupport
        : view === 'momentum'
          ? rows.filter((r) => r.winning_setup === 'momentum')
          : rows
    if (!q) return base
    return base.filter(
      (r) =>
        r.symbol.toLowerCase().includes(q) ||
        r.company_name.toLowerCase().includes(q) ||
        (r.sector ?? '').toLowerCase().includes(q),
    )
  }, [rows, atSupport, query, view])

  return (
    <div className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-8">
        <p className="font-mono text-xs uppercase tracking-[0.15em] text-slate-500">
          Phase 3 &middot; momentum + support
        </p>
        <h1 className="mt-2 text-3xl font-bold tracking-tight">
          Nifty 500 Conviction Tracker
        </h1>
        <p className="mt-2 max-w-2xl text-slate-600 dark:text-slate-400">
          Two setups, scored separately. <strong>T-M</strong> rewards a stock making new
          highs; <strong>T-S</strong> rewards one reversing off a tested support zone.
          The blend is whichever is stronger, so a good reversal is never penalised for
          not being a breakout. Quality and value gates arrive in phase 4.
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
          label="At a support zone"
          value={String(atSupport.length)}
          hint={`${atSupport.filter((r) => r.setup_status === 'triggered').length} triggered`}
          icon={<Database className="h-4 w-4" />}
        />
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {(
          [
            ['all', 'All'],
            ['momentum', 'Momentum'],
            ['support', 'At support'],
          ] as [View, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            onClick={() => setView(key)}
            className={`rounded-md border px-3 py-1.5 text-sm transition ${
              view === key
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

      {view === 'support' ? (
        <SupportList rows={filtered} />
      ) : (
        <ScreenerTable rows={filtered} />
      )}

      <p className="mt-6 text-xs text-slate-500">
        A stock below its 200DMA is capped at {BELOW_200DMA_CAP} on T-M however well it
        ranks elsewhere. A support setup with no reversal confirmation is capped at 55 and
        can never reach the top decile. For personal research. Not investment advice.
      </p>
    </div>
  )
}

function SupportList({ rows }: { rows: ScreenerRow[] }) {
  if (!rows.length) {
    return (
      <p className="rounded-md border border-slate-200 p-6 text-sm text-slate-500 dark:border-slate-800">
        Nothing at a live support zone right now. That is a normal reading — most of the
        market is not at support on any given day.
      </p>
    )
  }
  return (
    <div className="grid gap-3">
      {rows.map((r) => (
        <div
          key={r.symbol}
          className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900"
        >
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="font-mono text-base font-semibold">{r.symbol}</span>
            <span className="text-sm text-slate-500">{r.company_name}</span>
            <StatusChip status={r.setup_status} />
            <span className="ml-auto font-mono text-lg font-semibold tabular-nums">
              {r.ts_score?.toFixed(1)}
            </span>
          </div>

          <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
            <Field label="Close" value={r.close?.toFixed(2)} />
            <Field
              label="Zone"
              value={
                r.zone_floor && r.zone_ceil
                  ? `${r.zone_floor.toFixed(1)}–${r.zone_ceil.toFixed(1)}`
                  : '—'
              }
            />
            <Field label="Stop" value={r.stop_price?.toFixed(2)} />
            <Field
              label="Target"
              value={
                r.target_price
                  ? `${r.target_price.toFixed(2)} (${((r.headroom ?? 0) * 100).toFixed(0)}%)`
                  : '—'
              }
            />
          </dl>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="font-mono text-slate-500">
              R:R {r.reward_risk?.toFixed(1) ?? '—'}
            </span>
            {r.confirmations.map((c) => (
              <span
                key={c}
                className="rounded bg-emerald-100 px-2 py-0.5 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
              >
                {CONFIRMATION_LABELS[c] ?? c}
              </span>
            ))}
            {r.caps.map((c) => (
              <span
                key={c}
                className="rounded bg-amber-100 px-2 py-0.5 text-amber-900 dark:bg-amber-950/60 dark:text-amber-300"
              >
                {c}
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function ScreenerTable({ rows }: { rows: ScreenerRow[] }) {
  return (
    <>
      <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-800">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-slate-100 text-left font-mono text-[11px] uppercase tracking-wider text-slate-500 dark:bg-slate-900">
              <th className="px-3 py-2">Symbol</th>
              <th className="px-3 py-2">Sector</th>
              <th className="px-3 py-2 text-right">Close</th>
              <th className="px-3 py-2 text-right">Blend</th>
              <th className="px-3 py-2">Setup</th>
              <th className="px-3 py-2 text-right">T-M</th>
              <th className="px-3 py-2 text-right">T-S</th>
              <th className="px-3 py-2 text-right">12-1 mom</th>
              <th className="px-3 py-2 text-right">RSI</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 100).map((r) => (
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
                  <ScoreChip value={r.blended} capped={r.above_200dma === false} />
                </td>
                <td className="px-3 py-2 text-xs">
                  {r.winning_setup === 'support' ? (
                    <span className="rounded bg-sky-100 px-1.5 py-0.5 text-sky-800 dark:bg-sky-950 dark:text-sky-300">
                      support
                    </span>
                  ) : (
                    <span className="text-slate-400">momentum</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500">
                  {r.tm_score?.toFixed(1) ?? '—'}
                </td>
                <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500">
                  {r.ts_score?.toFixed(1) ?? '—'}
                </td>
                <td className="px-3 py-2 text-right"><Pct value={r.mom_12_1} /></td>
                <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500">
                  {r.rsi14?.toFixed(0) ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > 100 && (
        <p className="mt-3 text-sm text-slate-500">
          Showing 100 of {rows.length}. Sorting and paging land with the full screener.
        </p>
      )}
    </>
  )
}

function StatusChip({ status }: { status: string }) {
  if (status === 'triggered')
    return (
      <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
        triggered
      </span>
    )
  if (status === 'watching')
    return (
      <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-900 dark:bg-amber-950/60 dark:text-amber-300">
        watching
      </span>
    )
  return null
}

function Field({ label, value }: { label: string; value?: string }) {
  return (
    <div>
      <dt className="font-mono text-[10px] uppercase tracking-wider text-slate-500">
        {label}
      </dt>
      <dd className="font-mono tabular-nums">{value ?? '—'}</dd>
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
      {capped && <span title="below its 200DMA — T-M capped">&bull;</span>}
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

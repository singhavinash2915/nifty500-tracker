import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, CircleAlert, Info } from 'lucide-react'
import type { AlertRow, PositionsFile, Severity } from '../types'
import { pct } from '../lib/format'

const SEVERITY_STYLE: Record<Severity, { chip: string; icon: React.ReactNode; label: string }> = {
  critical: {
    chip: 'bg-red-100 text-red-800 dark:bg-red-950/60 dark:text-red-300',
    icon: <CircleAlert className="h-4 w-4" />,
    label: 'money at risk',
  },
  action: {
    chip: 'bg-amber-100 text-amber-900 dark:bg-amber-950/60 dark:text-amber-300',
    icon: <AlertTriangle className="h-4 w-4" />,
    label: 'decision available',
  },
  info: {
    chip: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
    icon: <Info className="h-4 w-4" />,
    label: 'worth knowing',
  },
}

export function Portfolio() {
  const [data, setData] = useState<PositionsFile | null>(null)
  const [alerts, setAlerts] = useState<AlertRow[]>([])

  useEffect(() => {
    let cancelled = false
    const base = import.meta.env.BASE_URL
    fetch(`${base}positions.json`)
      .then((r) => (r.ok ? r.json() : { positions: [], totals: {} }))
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setData({ positions: [], totals: {} }))
    fetch(`${base}alerts.json`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => !cancelled && setAlerts(Array.isArray(d) ? d : []))
      .catch(() => !cancelled && setAlerts([]))
    return () => {
      cancelled = true
    }
  }, [])

  const totals = data?.totals ?? {}
  const positions = data?.positions ?? []

  return (
    <>
      <Link to="/" className="mb-4 inline-block text-sm text-slate-500 hover:text-slate-900 dark:hover:text-slate-100">
        &larr; Screener
      </Link>

      <h1 className="mb-1 text-2xl font-bold tracking-tight">Positions and alerts</h1>
      <p className="mb-6 max-w-2xl text-slate-600 dark:text-slate-400">
        Alerts are transitions, never states — "crossed below the stop today", not "is
        below the stop", so a standing condition does not announce itself every night.
      </p>

      {alerts.length > 0 ? (
        <section className="mb-8">
          <h2 className="mb-2 font-mono text-[11px] uppercase tracking-wider text-slate-500">
            Tonight
          </h2>
          <ul className="grid gap-2">
            {alerts.map((a, i) => {
              const style = SEVERITY_STYLE[a.payload?.severity ?? 'info']
              return (
                <li
                  key={`${a.symbol}-${a.rule}-${i}`}
                  className="flex items-start gap-3 rounded-md border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900"
                >
                  <span className={`mt-0.5 rounded p-1 ${style.chip}`}>{style.icon}</span>
                  <div className="min-w-0">
                    <Link to={`/stock/${a.symbol}`} className="font-mono font-medium hover:underline">
                      {a.symbol}
                    </Link>
                    <span className="ml-2 text-sm text-slate-600 dark:text-slate-300">
                      {a.message.replace(new RegExp(`^${a.symbol} `), '')}
                    </span>
                    <p className="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-slate-400">
                      {a.rule.replace(/_/g, ' ')} &middot; {style.label}
                    </p>
                  </div>
                </li>
              )
            })}
          </ul>
        </section>
      ) : (
        <p className="mb-8 rounded-md border border-slate-200 p-4 text-sm text-slate-500 dark:border-slate-800">
          Nothing worth interrupting for. A quiet alert feed is the system working.
        </p>
      )}

      {positions.length > 0 && (
        <>
          <div className="mb-4 grid gap-3 sm:grid-cols-4">
            <Tile label="Invested" value={`₹${(totals.invested ?? 0).toLocaleString('en-IN')}`} />
            <Tile
              label="Value"
              value={`₹${(totals.value ?? 0).toLocaleString('en-IN')}`}
              hint={pct(totals.return_pct ?? null)}
              tone={(totals.pnl ?? 0) >= 0 ? 'good' : 'bad'}
            />
            <Tile
              label="Still at risk"
              value={`₹${(totals.risk_remaining ?? 0).toLocaleString('en-IN')}`}
              hint="to the stops from here"
            />
            <Tile
              label="Thesis broken"
              value={String(totals.thesis_broken ?? 0)}
              hint="now failing a hard gate"
              tone={(totals.thesis_broken ?? 0) > 0 ? 'bad' : undefined}
            />
          </div>

          <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-800">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="bg-slate-100 text-left font-mono text-[11px] uppercase tracking-wider text-slate-500 dark:bg-slate-900">
                  <th className="px-3 py-2">Symbol</th>
                  <th className="px-3 py-2 text-right">Qty</th>
                  <th className="px-3 py-2 text-right">Entry</th>
                  <th className="px-3 py-2 text-right">Close</th>
                  <th className="px-3 py-2 text-right">Return</th>
                  <th className="px-3 py-2 text-right">To stop</th>
                  <th className="px-3 py-2 text-right">Decile</th>
                  <th className="px-3 py-2">Thesis</th>
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={`${p.symbol}-${p.id}`} className="border-t border-slate-200 dark:border-slate-800">
                    <td className="px-3 py-2">
                      <Link to={`/stock/${p.symbol}`} className="font-mono font-medium hover:underline">
                        {p.symbol}
                      </Link>
                    </td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums">{p.quantity}</td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums">{p.entry_price.toFixed(2)}</td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums">{p.close?.toFixed(2) ?? '—'}</td>
                    <td className={`px-3 py-2 text-right font-mono tabular-nums ${
                      (p.return_pct ?? 0) >= 0 ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-700 dark:text-red-400'
                    }`}>
                      {pct(p.return_pct ?? null)}
                    </td>
                    <td className={`px-3 py-2 text-right font-mono tabular-nums ${
                      (p.stop_distance_pct ?? 1) <= 0 ? 'text-red-700 dark:text-red-400' : 'text-slate-500'
                    }`}>
                      {pct(p.stop_distance_pct ?? null, 0)}
                    </td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500">
                      {p.decile ?? '—'}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {p.thesis_intact === false ? (
                        <span className="rounded bg-red-100 px-2 py-0.5 text-red-800 dark:bg-red-950/60 dark:text-red-300">
                          broken: {(p.failed_gates ?? []).join(', ')}
                        </span>
                      ) : (
                        <span className="text-slate-400">{p.thesis ?? 'intact'}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {positions.length === 0 && (
        <p className="rounded-md border border-slate-200 p-6 text-sm text-slate-500 dark:border-slate-800">
          No open positions. Record one with{' '}
          <code className="font-mono text-xs">
            python -m n500.jobs.manage_position open SYMBOL --qty N --price P --stop S
          </code>
          . A stop is required — the alert engine cannot measure risk without one.
        </p>
      )}
    </>
  )
}

function Tile({
  label,
  value,
  hint,
  tone,
}: {
  label: string
  value: string
  hint?: string
  tone?: 'good' | 'bad'
}) {
  const toneClass =
    tone === 'good' ? 'text-emerald-700 dark:text-emerald-400'
      : tone === 'bad' ? 'text-red-700 dark:text-red-400'
        : ''
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <p className="font-mono text-[11px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`mt-1 text-xl font-semibold tabular-nums ${toneClass}`}>{value}</p>
      {hint && <p className={`text-xs ${toneClass || 'text-slate-500'}`}>{hint}</p>}
    </div>
  )
}

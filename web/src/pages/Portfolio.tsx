import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, CircleAlert, Info } from 'lucide-react'
import type { AlertRow, Severity } from '../types'
import { pct } from '../lib/format'
import { loadAlerts, loadPortfolio, loadScreener,
         type PortfolioSettings, type PositionView } from '../lib/load'
import { AddHolding } from '../components/AddHolding'
import { EditHolding } from '../components/EditHolding'

const rupees = (v: number | null | undefined) =>
  `₹${Math.round(v ?? 0).toLocaleString('en-IN')}`

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
  const [positions, setPositions] = useState<PositionView[]>([])
  const [settings, setSettings] = useState<PortfolioSettings | null>(null)
  const [universe, setUniverse] = useState<any[]>([])
  const [alerts, setAlerts] = useState<AlertRow[]>([])
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let cancelled = false
    loadPortfolio().then(({ positions, settings }) => {
      if (cancelled) return
      setPositions(positions)
      setSettings(settings)
    })
    loadAlerts().then((rows) => !cancelled && setAlerts(rows))
    return () => {
      cancelled = true
    }
  }, [reload])

  useEffect(() => {
    loadScreener().then(({ snapshot }) => setUniverse(snapshot.rows))
  }, [])

  const invested = positions.reduce((s, p) => s + p.entry_price * p.quantity, 0)
  const value = positions.reduce((s, p) => s + (p.value ?? 0), 0)
  const totals = {
    invested,
    value,
    pnl: value - invested,
    return_pct: invested ? value / invested - 1 : null,
    risk_remaining: positions.reduce((s, p) => s + (p.risk_remaining ?? 0), 0),
    capital_at_risk: positions.reduce(
      (s, p) => s + (p.risk_share ?? 0) * (settings?.total_capital ?? 0), 0,
    ),
    thesis_broken: positions.filter((p) => p.thesis_intact === false).length,
  }

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

      <div className="mb-6 flex flex-wrap items-center gap-3">
        <AddHolding rows={universe} onAdded={() => setReload((n) => n + 1)} />
        {settings ? (
          <span className="font-mono text-xs text-slate-500">
            capital ₹{(settings.total_capital / 100000).toFixed(1)}L &middot;{' '}
            ₹{(settings.total_capital * settings.risk_pct).toLocaleString('en-IN')} a risk unit
            {invested > 0 && <> &middot; {((invested / settings.total_capital) * 100).toFixed(0)}% deployed</>}
          </span>
        ) : (
          <span className="font-mono text-xs text-amber-700 dark:text-amber-400">
            no capital set — risk shown against book value, which understates the
            denominator
          </span>
        )}
      </div>

      {positions.length > 0 && (
        <>
          <div className="mb-4 grid gap-3 sm:grid-cols-4">
            <Tile label="Invested" value={rupees(totals.invested)} />
            <Tile
              label="Value"
              value={rupees(totals.value)}
              hint={pct(totals.return_pct ?? null)}
              tone={(totals.pnl ?? 0) >= 0 ? 'good' : 'bad'}
            />
            <Tile
              label="Capital at risk"
              value={rupees(totals.capital_at_risk)}
              hint={
                settings
                  ? `${((totals.capital_at_risk ?? 0) / settings.total_capital * 100).toFixed(2)}% of ₹${(settings.total_capital / 100000).toFixed(1)}L`
                  : 'no capital set — percentages use book value'
              }
            />
            <Tile
              label="Thesis broken"
              value={String(totals.thesis_broken ?? 0)}
              hint="now failing a hard gate"
              tone={(totals.thesis_broken ?? 0) > 0 ? 'bad' : undefined}
            />
          </div>

          <div className="grid gap-3">
            {positions.map((p) => (
              <div key={`${p.symbol}-${p.id}`}
                   className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
                <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                  <Link to={`/stock/${p.symbol}`} className="font-mono text-base font-semibold hover:underline">
                    {p.symbol}
                  </Link>
                  <span className="text-sm text-slate-500">
                    {p.quantity} @ {p.entry_price.toFixed(2)}
                    {p.entry_date && <> since {p.entry_date}</>}
                  </span>
                  <EditHolding position={p} onSaved={() => setReload((n) => n + 1)} />
                  <span className={`ml-auto font-mono text-lg tabular-nums ${
                    (p.return_pct ?? 0) >= 0 ? 'text-emerald-700 dark:text-emerald-400'
                                             : 'text-red-700 dark:text-red-400'}`}>
                    {pct(p.return_pct)}
                  </span>
                </div>

                <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-5">
                  <Cell label="Close" value={p.close?.toFixed(2) ?? '—'}
                        hint={p.weight === null ? undefined : `${(p.weight * 100).toFixed(0)}% weight`} />
                  <Cell label="P&L" value={p.pnl === null ? '—' : rupees(p.pnl)} />
                  <Cell label="Stop"
                        value={(p.stop_price ?? p.plan_stop)?.toFixed(2) ?? '—'}
                        hint={
                          p.stop_price === null && p.plan_stop !== null
                            ? `suggested · ${p.plan_stop_basis}`
                            : p.stop_distance_pct === null
                              ? undefined
                              : `${pct(p.stop_distance_pct, 0)} away`
                        } />
                  <Cell label="Still at risk"
                        value={p.risk_remaining === null ? '—' : rupees(Math.max(p.risk_remaining, 0))}
                        hint={p.risk_share === null ? undefined : `${(p.risk_share * 100).toFixed(1)}% of the book`} />
                  <Cell label="Score" value={p.blended?.toFixed(0) ?? '—'}
                        hint={p.decile === null ? undefined : `decile ${p.decile}`} />
                </dl>

                {/* The plan, separate from the marks above: what to do rather
                    than what happened. Shown for every holding, since the
                    engine now has a stop and a target for every symbol. */}
                <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 border-t border-slate-200 pt-3 text-sm sm:grid-cols-4 dark:border-slate-800">
                  <Cell
                    label="Next resistance"
                    value={p.plan_target?.toFixed(2) ?? '—'}
                    hint={
                      p.plan_target && p.close
                        ? `${pct(p.plan_target / p.close - 1, 0)} up · scale, don't exit`
                        : undefined
                    }
                  />
                  <Cell label="Reward : risk" value={
                    p.plan_reward_risk ? `${p.plan_reward_risk.toFixed(1)}:1` : '—'
                  } />
                  {/* A stop above the entry leaves no risk to divide the gain
                      by, so the R multiple is undefined — and that is the best
                      state a position can be in, not a missing number. */}
                  <Cell
                    label="R multiple"
                    value={
                      p.r_multiple !== null
                        ? `${p.r_multiple > 0 ? '+' : ''}${p.r_multiple.toFixed(1)}R`
                        : p.stop_price !== null && p.stop_price >= p.entry_price
                          ? 'risk-free'
                          : '—'
                    }
                    hint={
                      p.r_multiple !== null
                        ? 'profit in units of risk taken'
                        : p.stop_price !== null && p.stop_price >= p.entry_price
                          ? 'stop is above cost — this cannot lose'
                          : 'profit in units of risk taken'
                    }
                  />
                  <Cell label="Held" value={
                    p.days_held === null ? '—' : `${p.days_held}d`
                  } hint={p.days_held === null ? undefined : `of ~180`} />
                </dl>

                {p.thesis && (
                  <p className="mt-3 border-l-2 border-slate-200 pl-3 text-sm italic text-slate-500 dark:border-slate-700">
                    “{p.thesis}”
                  </p>
                )}

                <ul className="mt-3 grid gap-1 text-sm">
                  {p.insights.map((ins, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <span className={`mt-1.5 inline-block h-1.5 w-1.5 shrink-0 rounded-full ${
                        ins.tone === 'bad' ? 'bg-red-500'
                          : ins.tone === 'warn' ? 'bg-amber-400' : 'bg-emerald-500'}`} />
                      <span className={ins.tone === 'bad' ? 'text-red-800 dark:text-red-300'
                        : ins.tone === 'warn' ? 'text-amber-900 dark:text-amber-300'
                        : 'text-slate-600 dark:text-slate-400'}>{ins.text}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </>
      )}

      {positions.length === 0 && (
        <p className="rounded-md border border-slate-200 p-6 text-sm text-slate-500 dark:border-slate-800">
          No open holdings recorded yet. Add the stocks you already own above and the
          tracker will score them alongside everything else, and tell you when the
          reason you bought one stops being true.
        </p>
      )}
    </>
  )
}

function Cell({
  label, value, hint,
}: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <dt className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</dt>
      <dd className="font-mono tabular-nums">{value}</dd>
      {hint && <dd className="text-xs text-slate-400">{hint}</dd>}
    </div>
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

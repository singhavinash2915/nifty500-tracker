import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle } from 'lucide-react'
import type { ScreenerRow } from '../types'
import { pct } from '../lib/format'
import { loadPlans, loadPortfolio, type PortfolioSettings, type PositionView } from '../lib/load'
import { buildShortlist, capacity, type Candidate } from '../lib/shortlist'

const rupees = (v: number | null | undefined) =>
  `₹${Math.round(v ?? 0).toLocaleString('en-IN')}`

/**
 * What you could buy today, already sized.
 *
 * The screener ranks; this decides. Everything on the page is arithmetic on
 * facts the tracker already holds — the ranking is conviction's, the stop is
 * the plan's, and the share count is whatever risks exactly one unit.
 */
export function Shortlist({ rows }: { rows: ScreenerRow[] }) {
  const [held, setHeld] = useState<PositionView[]>([])
  const [settings, setSettings] = useState<PortfolioSettings | null>(null)
  const [plans, setPlans] = useState<Awaited<ReturnType<typeof loadPlans>>>(new Map())
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    Promise.all([loadPortfolio(), loadPlans()]).then(([portfolio, planMap]) => {
      if (cancelled) return
      setHeld(portfolio.positions)
      setSettings(portfolio.settings)
      setPlans(planMap)
      setLoading(false)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const ranked = [...rows].sort((a, b) => (b.conviction ?? -1) - (a.conviction ?? -1))
  const candidates = buildShortlist({ rows: ranked, plans, held, settings })
  const room = capacity(held, settings)

  return (
    <>
      <Link to="/" className="mb-4 inline-block text-sm text-slate-500 hover:text-slate-900 dark:hover:text-slate-100">
        &larr; Screener
      </Link>

      <h1 className="mb-1 text-2xl font-bold tracking-tight">What you could buy today</h1>
      <p className="mb-6 max-w-2xl text-slate-600 dark:text-slate-400">
        Ranked by conviction — the composite fitted on 2023-24 and scored once on
        2025-26 — then sized so that being wrong costs one risk unit. Names you
        already own and businesses failing a hard gate are left out.
      </p>

      {loading ? (
        <p className="text-sm text-slate-500">Loading…</p>
      ) : !settings ? (
        <p className="rounded-md border border-amber-300 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          No capital is set, so nothing here can be sized. Set it from the command
          line with <code className="font-mono">manage_position capital --set …</code>.
        </p>
      ) : (
        <>
          {room && (
            <div className="mb-6 grid gap-3 sm:grid-cols-4">
              <Tile label="Capital" value={`₹${(room.capital / 100000).toFixed(1)}L`}
                    hint={`${rupees(room.unit)} a risk unit`} />
              <Tile label="Deployed" value={pct(room.deployed_pct, 0)}
                    hint={`${rupees(room.free)} not invested`} />
              <Tile label="Risk in use" value={pct(room.risked_pct, 2)}
                    hint="of capital, across every open position" />
              <Tile label="Units free" value={String(room.units_free)}
                    hint="before the book carries 6% of risk" />
            </div>
          )}

          {candidates.length === 0 ? (
            <p className="rounded-md border border-slate-200 p-6 text-sm text-slate-500 dark:border-slate-800">
              Nothing passes today. That is a result rather than a failure — the
              gates remove businesses for stated reasons, and a stock with no
              support beneath it cannot be sized.
            </p>
          ) : (
            <div className="grid gap-3">
              {candidates.map((c) => (
                <CandidateCard key={c.row.symbol} candidate={c} />
              ))}
            </div>
          )}

          <p className="mt-6 max-w-2xl text-xs text-slate-500">
            Sizing assumes one risk unit per position. An information coefficient
            of 0.168 is a tilt rather than a forecast — it ranks correctly somewhat
            more often than not, and only pays across many positions held with the
            same discipline. Nothing here is a recommendation to buy.
          </p>
        </>
      )}
    </>
  )
}

function CandidateCard({ candidate: c }: { candidate: Candidate }) {
  const { row } = c
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Link to={`/stock/${row.symbol}`} className="font-mono text-base font-semibold hover:underline">
          {row.symbol}
        </Link>
        <span className="truncate text-sm text-slate-500">{row.company_name}</span>
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
          {row.sector}
        </span>
        <span className="ml-auto font-mono text-lg tabular-nums">
          {row.conviction?.toFixed(0)}
          <span className="ml-1 text-xs text-slate-500">conviction</span>
        </span>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-5">
        <Cell label="Buy" value={`${c.shares.toLocaleString('en-IN')} sh`}
              hint={`${rupees(c.cost)} · ${(c.weight * 100).toFixed(1)}% of capital`} />
        <Cell label="At" value={row.close?.toFixed(2) ?? '—'} />
        <Cell label="Stop" value={c.stop.toFixed(2)}
              hint={c.stop_basis ?? undefined} />
        {/* Distance, not a ratio. Conviction ranks stocks pressed *under*
            resistance highly, so a near level is the setup working — calling
            it "0.2:1" would flag the signal as a bad trade. */}
        <Cell label="Resistance" value={c.target?.toFixed(2) ?? '—'}
              hint={c.headroom === null ? 'none overhead' : `${pct(c.headroom, 1)} up · scale, don't exit`} />
        <Cell label="Risking" value={rupees(c.risk)}
              hint={`${c.risk_units.toFixed(2)} of a unit · stop ${pct(c.stop_pct, 1)} away`} />
      </dl>

      {c.warnings.length > 0 && (
        <ul className="mt-3 grid gap-1 text-sm">
          {c.warnings.map((w) => (
            <li key={w} className="flex items-start gap-2 text-amber-900 dark:text-amber-300">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{w}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Cell({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <dt className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</dt>
      <dd className="font-mono tabular-nums">{value}</dd>
      {hint && <dd className="text-xs text-slate-400">{hint}</dd>}
    </div>
  )
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <p className="font-mono text-[11px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-semibold tabular-nums">{value}</p>
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

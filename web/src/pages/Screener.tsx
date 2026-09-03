import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpDown, Search } from 'lucide-react'
import type { ScreenerRow, Weights } from '../types'
import { DEFAULT_WEIGHTS } from '../types'
import { funnel, isExcluded, pickTechnical, reblend, winningSetup } from '../lib/scoring'
import { pct } from '../lib/format'

type SortKey = 'blended' | 'quality_score' | 'value_score' | 'technical' | 'mom_12_1' | 'symbol'
type View = 'all' | 'support' | 'excluded'

export function Screener({ rows, asOf }: { rows: ScreenerRow[]; asOf: string }) {
  const [weights, setWeights] = useState<Weights>(DEFAULT_WEIGHTS)
  const [sort, setSort] = useState<SortKey>('blended')
  const [asc, setAsc] = useState(false)
  const [query, setQuery] = useState('')
  const [sector, setSector] = useState('all')
  const [view, setView] = useState<View>('all')

  const sectors = useMemo(
    () => [...new Set(rows.map((r) => r.sector).filter(Boolean))].sort() as string[],
    [rows],
  )

  const scored = useMemo(
    () =>
      rows.map((r) => ({
        row: r,
        blended: reblend(r, weights),
        technical: pickTechnical(r),
        setup: winningSetup(r),
        excluded: isExcluded(r),
      })),
    [rows, weights],
  )

  const steps = useMemo(() => funnel(rows), [rows])

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    let list = scored
    if (view === 'support') list = list.filter((s) => s.row.setup_status !== 'none')
    else if (view === 'excluded') list = list.filter((s) => s.excluded)
    else list = list.filter((s) => !s.excluded)

    if (sector !== 'all') list = list.filter((s) => s.row.sector === sector)
    if (q)
      list = list.filter(
        (s) =>
          s.row.symbol.toLowerCase().includes(q) ||
          s.row.company_name.toLowerCase().includes(q),
      )

    const value = (s: (typeof scored)[number]) => {
      if (sort === 'symbol') return s.row.symbol
      if (sort === 'blended') return s.blended
      if (sort === 'technical') return s.technical
      return s.row[sort]
    }

    return [...list].sort((a, b) => {
      const va = value(a)
      const vb = value(b)
      if (typeof va === 'string' || typeof vb === 'string')
        return (asc ? 1 : -1) * String(va).localeCompare(String(vb))
      if (va === null || va === undefined) return 1
      if (vb === null || vb === undefined) return -1
      return (asc ? 1 : -1) * (va - vb)
    })
  }, [scored, view, sector, query, sort, asc])

  function toggleSort(key: SortKey) {
    if (key === sort) setAsc(!asc)
    else {
      setSort(key)
      setAsc(key === 'symbol')
    }
  }

  return (
    <>
      <section className="mb-8 grid gap-4 lg:grid-cols-[1fr_320px]">
        <Funnel steps={steps} />
        <WeightSliders weights={weights} onChange={setWeights} />
      </section>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        {(
          [
            ['all', `All (${scored.filter((s) => !s.excluded).length})`],
            ['support', `At support (${steps[4].value})`],
            ['excluded', `Red flags (${scored.filter((s) => s.excluded).length})`],
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

        <select
          value={sector}
          onChange={(e) => setSector(e.target.value)}
          className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm dark:border-slate-700 dark:bg-slate-900"
        >
          <option value="all">All sectors</option>
          {sectors.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <div className="ml-auto flex min-w-52 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 py-1.5 dark:border-slate-700 dark:bg-slate-900">
          <Search className="h-4 w-4 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Symbol or company"
            className="w-full bg-transparent text-sm outline-none placeholder:text-slate-400"
          />
        </div>
      </div>

      <div className="overflow-x-auto rounded-md border border-slate-200 dark:border-slate-800">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-slate-100 text-left font-mono text-[11px] uppercase tracking-wider text-slate-500 dark:bg-slate-900">
              <Th onClick={() => toggleSort('symbol')} active={sort === 'symbol'}>Symbol</Th>
              <th className="px-3 py-2">Sector</th>
              <th className="px-3 py-2 text-right">Close</th>
              <Th right onClick={() => toggleSort('blended')} active={sort === 'blended'}>Blend</Th>
              <Th right onClick={() => toggleSort('quality_score')} active={sort === 'quality_score'}>Q</Th>
              <Th right onClick={() => toggleSort('value_score')} active={sort === 'value_score'}>V</Th>
              <Th right onClick={() => toggleSort('technical')} active={sort === 'technical'}>Tech</Th>
              <th className="px-3 py-2">Setup</th>
              <Th right onClick={() => toggleSort('mom_12_1')} active={sort === 'mom_12_1'}>12-1 mom</Th>
            </tr>
          </thead>
          <tbody>
            {visible.slice(0, 150).map(({ row, blended, technical, setup, excluded }) => (
              <tr key={row.symbol} className="border-t border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-900/60">
                <td className="px-3 py-2">
                  <Link to={`/stock/${row.symbol}`} className="font-mono font-medium hover:underline">
                    {row.symbol}
                  </Link>
                  <span className="ml-2 text-xs text-slate-500">{row.company_name}</span>
                </td>
                <td className="px-3 py-2 text-xs text-slate-500">{row.sector}</td>
                <td className="px-3 py-2 text-right font-mono tabular-nums">
                  {row.close?.toFixed(2) ?? '—'}
                </td>
                <td className="px-3 py-2 text-right">
                  {excluded ? (
                    <span className="rounded bg-red-100 px-2 py-0.5 font-mono text-xs font-semibold text-red-800 dark:bg-red-950/60 dark:text-red-300">
                      excluded
                    </span>
                  ) : (
                    <ScoreChip value={blended} capped={row.above_200dma === false} />
                  )}
                </td>
                <Num value={row.quality_score} />
                <Num value={row.value_score} />
                <Num value={technical} />
                <td className="px-3 py-2 text-xs">
                  {setup === 'support' ? (
                    <span className="rounded bg-sky-100 px-1.5 py-0.5 text-sky-800 dark:bg-sky-950 dark:text-sky-300">
                      support{row.setup_status === 'watching' ? ' · watching' : ''}
                    </span>
                  ) : (
                    <span className="text-slate-400">momentum</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right font-mono tabular-nums">
                  <span className={row.mom_12_1 !== null && row.mom_12_1 >= 0
                    ? 'text-emerald-700 dark:text-emerald-400'
                    : 'text-red-700 dark:text-red-400'}>
                    {pct(row.mom_12_1)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {visible.length > 150 && (
        <p className="mt-3 text-sm text-slate-500">Showing 150 of {visible.length}.</p>
      )}
      <p className="mt-6 text-xs text-slate-500">
        Prices as of <span className="font-mono">{asOf}</span>. Sliders re-rank locally
        using the same rule as the pipeline: the technical input is max(T-M, T-S), and
        weights renormalise over whichever pillars exist. For personal research. Not
        investment advice.
      </p>
    </>
  )
}

function Funnel({ steps }: { steps: { label: string; value: number }[] }) {
  const top = steps[0].value || 1
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="font-mono text-[11px] uppercase tracking-wider text-slate-500">
        Why the shortlist is short
      </h2>
      <ul className="mt-3 grid gap-1.5">
        {steps.map((s) => (
          <li key={s.label} className="grid grid-cols-[1fr_auto] items-center gap-3">
            <div className="relative h-6 overflow-hidden rounded bg-slate-100 dark:bg-slate-800">
              <div
                className="h-full rounded bg-slate-300 dark:bg-slate-700"
                style={{ width: `${Math.max((s.value / top) * 100, 1.5)}%` }}
              />
              <span className="absolute inset-y-0 left-2 flex items-center text-xs text-slate-700 dark:text-slate-200">
                {s.label}
              </span>
            </div>
            <span className="font-mono text-sm tabular-nums">{s.value}</span>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-slate-500">
        Each gate removes names for a stated reason rather than scoring them low. A short
        final list is the design working, not a bug.
      </p>
    </div>
  )
}

function WeightSliders({
  weights,
  onChange,
}: {
  weights: Weights
  onChange: (w: Weights) => void
}) {
  const total = weights.quality + weights.value + weights.technical
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <h2 className="font-mono text-[11px] uppercase tracking-wider text-slate-500">
        Blend weights
      </h2>
      {(
        [
          ['quality', 'Quality & growth'],
          ['value', 'Value'],
          ['technical', 'Technical'],
        ] as [keyof Weights, string][]
      ).map(([key, label]) => (
        <label key={key} className="mt-3 block">
          <span className="flex justify-between text-sm">
            {label}
            <span className="font-mono tabular-nums text-slate-500">
              {Math.round((weights[key] / total) * 100)}%
            </span>
          </span>
          <input
            type="range" min={0} max={100} value={weights[key]}
            onChange={(e) => onChange({ ...weights, [key]: Number(e.target.value) })}
            className="mt-1 w-full accent-slate-900 dark:accent-slate-100"
          />
        </label>
      ))}
      <button
        onClick={() => onChange(DEFAULT_WEIGHTS)}
        className="mt-3 text-xs text-slate-500 underline underline-offset-2 hover:text-slate-800 dark:hover:text-slate-200"
      >
        Reset to 45 / 20 / 35
      </button>
      <p className="mt-2 text-xs text-slate-500">
        A starting suggestion, not a conclusion — the backtest should set these.
      </p>
    </div>
  )
}

function Th({
  children,
  onClick,
  active,
  right,
}: {
  children: React.ReactNode
  onClick: () => void
  active: boolean
  right?: boolean
}) {
  return (
    <th className={`px-3 py-2 ${right ? 'text-right' : ''}`}>
      <button
        onClick={onClick}
        className={`inline-flex items-center gap-1 uppercase tracking-wider hover:text-slate-900 dark:hover:text-slate-100 ${
          active ? 'text-slate-900 dark:text-slate-100' : ''
        }`}
      >
        {children}
        <ArrowUpDown className="h-3 w-3" />
      </button>
    </th>
  )
}

function Num({ value }: { value: number | null }) {
  return (
    <td className="px-3 py-2 text-right font-mono tabular-nums text-slate-500">
      {value?.toFixed(0) ?? '—'}
    </td>
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

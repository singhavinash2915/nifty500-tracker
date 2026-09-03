import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import type { ScreenerRow, StockDetail as Detail } from '../types'
import { PriceChart } from '../components/PriceChart'
import { ShareholdingTrend, TrendBars } from '../components/Charts'
import { crore, num, pct } from '../lib/format'
import { useDarkMode } from '../lib/palette'

const CONFIRMATION_LABELS: Record<string, string> = {
  bullish_candle: 'reversal candle at the zone',
  rsi_divergence: 'bullish RSI divergence',
  macd_turning_up: 'MACD histogram turning up',
  reclaimed_short_ma: 'reclaimed the 20DMA',
  volume_pattern: 'volume dried up, then expanded',
}

const FLAG_LABELS: Record<string, string> = {
  promoter_pledge: 'Promoter pledge',
  promoter_selling: 'Promoter selling',
  cash_conversion: 'Profit becoming cash',
  receivable_bloat: 'Receivables',
  loss_making: 'Loss-making years',
}

export function StockDetail({ rows }: { rows: ScreenerRow[] }) {
  const { symbol = '' } = useParams()
  const [detail, setDetail] = useState<Detail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const dark = useDarkMode()

  const row = rows.find((r) => r.symbol === symbol)

  useEffect(() => {
    let cancelled = false
    setDetail(null)
    setError(null)
    fetch(`${import.meta.env.BASE_URL}stocks/${symbol}.json`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then((d: Detail) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setError('No detail data published for this symbol yet.'))
    return () => {
      cancelled = true
    }
  }, [symbol])

  if (!row) {
    return (
      <p className="rounded-md border border-slate-200 p-6 text-sm dark:border-slate-800">
        {symbol} is not in the current snapshot. <Link to="/" className="underline">Back to the screener</Link>.
      </p>
    )
  }

  const liveZones = detail?.zones.filter((z) => !z.invalidated_on) ?? []

  return (
    <>
      <Link to="/" className="mb-4 inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-900 dark:hover:text-slate-100">
        <ArrowLeft className="h-4 w-4" /> All stocks
      </Link>

      <header className="mb-6 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <h1 className="font-mono text-2xl font-bold">{row.symbol}</h1>
        <p className="text-lg text-slate-600 dark:text-slate-400">{row.company_name}</p>
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
          {row.sector}
        </span>
        {row.company_type === 'financial' && (
          <span
            className="rounded bg-violet-100 px-2 py-0.5 text-xs text-violet-800 dark:bg-violet-950 dark:text-violet-300"
            title="Scored on the lender question set: no debt/equity, no cash-conversion gate"
          >
            lender
          </span>
        )}
        <span className="ml-auto font-mono text-2xl tabular-nums">
          {row.close?.toFixed(2) ?? '—'}
        </span>
      </header>

      <section className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <ScoreCard label="Quality" value={row.quality_score} hint="growth, returns, balance sheet, cash" />
        <ScoreCard label="Value" value={row.value_score} hint="multiples vs sector and own history" />
        <ScoreCard label="T-M momentum" value={row.tm_score} hint="trend, relative strength, volume" />
        <ScoreCard label="T-S support" value={row.ts_score} hint={row.reason ?? 'zone, confirmation, reward-to-risk'} />
      </section>

      {row.flags.length > 0 && (
        <section className="mb-6 rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
          <h2 className="font-mono text-[11px] uppercase tracking-wider text-slate-500">Gate checks</h2>
          <ul className="mt-2 grid gap-1.5 text-sm sm:grid-cols-2">
            {row.flags.map((f) => (
              <li key={f.name} className="flex items-start gap-2">
                <span
                  className={`mt-0.5 inline-block h-2 w-2 shrink-0 rounded-full ${
                    f.verdict === 'fail' ? 'bg-red-500' : 'bg-amber-400'
                  }`}
                />
                <span>
                  <span className="font-medium">{FLAG_LABELS[f.name] ?? f.name}</span>{' '}
                  <span className="text-slate-500">
                    {f.verdict === 'fail' ? '— excluded' : '— not checked'}
                    {f.detail ? ` · ${f.detail.replace(/ — not checked$/, '')}` : ''}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {row.setup_status !== 'none' && (
        <section className="mb-6 rounded-md border border-sky-200 bg-sky-50 p-4 dark:border-sky-900 dark:bg-sky-950/40">
          <h2 className="font-mono text-[11px] uppercase tracking-wider text-sky-800 dark:text-sky-300">
            Support setup &middot; {row.setup_status}
          </h2>
          <dl className="mt-2 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-4">
            <Field label="Zone" value={row.zone_floor && row.zone_ceil ? `${num(row.zone_floor)}–${num(row.zone_ceil)}` : '—'} />
            <Field label="Stop" value={num(row.stop_price)} />
            <Field label="Target" value={row.target_price ? `${num(row.target_price)} (${pct(row.headroom, 0)})` : '—'} />
            <Field label="Reward : risk" value={row.reward_risk ? `${row.reward_risk.toFixed(1)}:1` : '—'} />
          </dl>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            {row.confirmations.map((c) => (
              <span key={c} className="rounded bg-emerald-100 px-2 py-0.5 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
                {CONFIRMATION_LABELS[c] ?? c}
              </span>
            ))}
            {row.caps.map((c) => (
              <span key={c} className="rounded bg-amber-100 px-2 py-0.5 text-amber-900 dark:bg-amber-950/60 dark:text-amber-300">
                {c}
              </span>
            ))}
          </div>
        </section>
      )}

      <Panel title="Price, moving averages and support zones"
             note={detail ? `${liveZones.length} live, ${detail.zones.length - liveZones.length} broken — nearest shown` : undefined}>
        {error && <p className="py-6 text-sm text-slate-500">{error}</p>}
        {!detail && !error && <p className="py-6 text-sm text-slate-500">Loading…</p>}
        {detail && (
          <PriceChart bars={detail.bars} overlays={detail.overlays} zones={detail.zones} dark={dark} />
        )}
      </Panel>

      {detail && (
        <div className="mt-6 grid gap-6 lg:grid-cols-2">
          <Panel title="Annual revenue and profit">
            <TrendBars records={detail.annual} keys={['revenue', 'pat']}
                       labels={['Revenue', 'Net profit']} dark={dark} />
          </Panel>
          <Panel title="Quarterly revenue and profit">
            <TrendBars records={detail.quarterly} keys={['revenue', 'pat']}
                       labels={['Revenue', 'Net profit']} dark={dark} />
          </Panel>
          <Panel title="Shareholding">
            <ShareholdingTrend records={detail.shareholding} dark={dark} />
          </Panel>
          <Panel title="Operating cash flow against profit"
                 note="Profit is an opinion; cash is a fact.">
            <TrendBars records={detail.annual} keys={['cfo', 'pat']}
                       labels={['Cash from operations', 'Net profit']} dark={dark} />
          </Panel>
        </div>
      )}

      <p className="mt-8 text-xs text-slate-500">
        Fundamentals are read only where the estimated filing date has passed, so nothing
        here uses a result before it was public. For personal research. Not investment advice.
      </p>
    </>
  )
}

function Panel({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3">
        <h2 className="font-mono text-[11px] uppercase tracking-wider text-slate-500">{title}</h2>
        {note && <span className="text-xs text-slate-400">{note}</span>}
      </div>
      {children}
    </section>
  )
}

function ScoreCard({ label, value, hint }: { label: string; value: number | null; hint: string }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
      <p className="font-mono text-[11px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className="mt-1 text-3xl font-semibold tabular-nums">
        {value === null ? <span className="text-slate-400">—</span> : value.toFixed(0)}
      </p>
      <div className="mt-2 h-1.5 overflow-hidden rounded bg-slate-100 dark:bg-slate-800">
        <div
          className="h-full rounded bg-slate-700 dark:bg-slate-300"
          style={{ width: `${value ?? 0}%` }}
        />
      </div>
      <p className="mt-2 text-xs text-slate-500">{hint}</p>
    </div>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-mono text-[10px] uppercase tracking-wider text-slate-500">{label}</dt>
      <dd className="font-mono tabular-nums">{value}</dd>
    </div>
  )
}

export { crore }

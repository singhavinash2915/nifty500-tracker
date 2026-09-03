import { useSeries } from '../lib/palette'
import { crore, shortDate } from '../lib/format'
import type { PeriodRecord } from '../types'

/**
 * Two small charts for the detail page. Both carry direct labels rather than
 * relying on colour alone — the palette validator flags slots 3 and 4 below
 * 3:1 on the light surface, and the relief for that is visible labelling.
 */

export function TrendBars({
  records,
  keys,
  labels,
  dark,
  format = crore,
  height = 150,
}: {
  records: PeriodRecord[]
  keys: string[]
  labels: string[]
  dark: boolean
  format?: (v: number | null | undefined) => string
  height?: number
}) {
  const series = useSeries(dark)
  const rows = records.slice(-10)
  if (!rows.length) return <Empty />

  const values = rows.flatMap((r) => keys.map((k) => (r[k] as number) ?? 0))
  const max = Math.max(...values, 0)
  const min = Math.min(...values, 0)
  const span = max - min || 1
  const zero = (height * max) / span

  const W = 640
  const slot = W / rows.length
  const barW = Math.max((slot - 10) / keys.length - 2, 3)

  return (
    <figure className="m-0">
      <svg viewBox={`0 0 ${W} ${height + 22}`} className="w-full" role="img"
           aria-label={labels.join(' and ') + ' by period'}>
        <line x1="0" x2={W} y1={zero} y2={zero} stroke={dark ? '#2a323f' : '#e8ecf2'} strokeWidth="1" />
        {rows.map((r, i) =>
          keys.map((k, j) => {
            const v = (r[k] as number) ?? 0
            const h = (Math.abs(v) / span) * height
            const x = i * slot + 5 + j * (barW + 2)
            const y = v >= 0 ? zero - h : zero
            return (
              <rect
                key={`${i}-${k}`}
                x={x} y={y} width={barW} height={Math.max(h, 1)}
                rx="2" fill={series[j]}
              >
                <title>{`${shortDate(r.period)} · ${labels[j]}: ${format(v)}`}</title>
              </rect>
            )
          }),
        )}
        {rows.map((r, i) => (
          <text
            key={r.period} x={i * slot + slot / 2} y={height + 15}
            fontSize="9" fill={dark ? '#8b95a6' : '#6b7484'}
            textAnchor="middle" fontFamily="ui-monospace, monospace"
          >
            {shortDate(r.period)}
          </text>
        ))}
      </svg>
      <figcaption className="mt-1 flex flex-wrap gap-x-4 text-xs text-slate-500">
        {labels.map((l, j) => (
          <span key={l} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-4 rounded-[1px]" style={{ background: series[j] }} />
            {l}
            <span className="font-mono tabular-nums text-slate-400">
              {format((rows.at(-1)?.[keys[j]] as number) ?? null)}
            </span>
          </span>
        ))}
      </figcaption>
    </figure>
  )
}

export function ShareholdingTrend({
  records,
  dark,
}: {
  records: PeriodRecord[]
  dark: boolean
}) {
  const series = useSeries(dark)
  const rows = records.slice(-12)
  if (!rows.length) return <Empty />

  const keys = ['promoter_pct', 'fii_pct', 'dii_pct', 'public_pct']
  const labels = ['Promoter', 'FII', 'DII', 'Public']
  const present = keys
    .map((k, i) => ({ k, label: labels[i], slot: i }))
    .filter(({ k }) => rows.some((r) => r[k] !== null && r[k] !== undefined))

  const W = 640
  const H = 140
  const x = (i: number) => (i / Math.max(rows.length - 1, 1)) * (W - 8) + 4
  const y = (v: number) => H - (v / 100) * H

  return (
    <figure className="m-0">
      <svg viewBox={`0 0 ${W} ${H + 20}`} className="w-full" role="img"
           aria-label="Shareholding by category over time">
        {[0, 25, 50, 75].map((g) => (
          <line key={g} x1="0" x2={W} y1={y(g)} y2={y(g)} stroke={dark ? '#2a323f' : '#e8ecf2'} strokeWidth="1" />
        ))}
        {present.map(({ k, slot }) => {
          let d = ''
          let pen = false
          rows.forEach((r, i) => {
            const v = r[k] as number | null
            if (v === null || v === undefined) {
              pen = false
              return
            }
            d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`
            pen = true
          })
          return <path key={k} d={d} fill="none" stroke={series[slot]} strokeWidth="2" />
        })}
        {rows.map((r, i) =>
          i % 3 === 0 ? (
            <text key={r.period} x={x(i)} y={H + 14} fontSize="9"
                  fill={dark ? '#8b95a6' : '#6b7484'} textAnchor="middle"
                  fontFamily="ui-monospace, monospace">
              {shortDate(r.period)}
            </text>
          ) : null,
        )}
      </svg>
      <figcaption className="mt-1 flex flex-wrap gap-x-4 text-xs text-slate-500">
        {present.map(({ k, label, slot }) => (
          <span key={k} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-4 rounded-[1px]" style={{ background: series[slot] }} />
            {label}
            <span className="font-mono tabular-nums text-slate-400">
              {((rows.at(-1)?.[k] as number) ?? 0).toFixed(1)}%
            </span>
          </span>
        ))}
      </figcaption>
    </figure>
  )
}

function Empty() {
  return <p className="py-6 text-sm text-slate-500">No data published for this company.</p>
}

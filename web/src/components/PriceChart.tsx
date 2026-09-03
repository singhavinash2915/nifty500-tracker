import { useMemo, useRef, useState } from 'react'
import type { Bars, Zone } from '../types'
import { useSeries } from '../lib/palette'
import { shortDate } from '../lib/format'

/**
 * Split-adjusted close with the moving averages and the computed support
 * zones drawn as bands.
 *
 * Deliberately one axis. Volume gets its own strip underneath rather than a
 * second y-scale on the same plot: two scales on one frame let the reader
 * infer a relationship from where the marks happen to cross, which is an
 * artefact of the scaling rather than anything in the data.
 *
 * Zones are bands, not lines, because that is what they are — a region price
 * turned in. A broken zone stays on the chart, hatched and dimmed, since
 * "support that failed" is information you want when judging what is left.
 *
 * Only the relevant ones are drawn. UltraTech has 33, and painting all of them
 * produced a solid wall of colour with the moving averages invisible behind
 * it — the chart stopped saying anything. The nearest live zones below price
 * and the most recent breaks are what a decision actually turns on.
 */

const MAX_LIVE_ZONES = 3
const MAX_BROKEN_ZONES = 2

function selectZones(zones: Zone[], price: number, firstDate: string): Zone[] {
  const live = zones
    .filter((z) => !z.invalidated_on && z.floor <= price)
    .sort((a, b) => price - (a.floor + a.ceil) / 2 - (price - (b.floor + b.ceil) / 2))
    .slice(0, MAX_LIVE_ZONES)

  // A broken zone is only worth drawing if it broke inside the window on view
  // and sits near the action. One that shattered above the current price two
  // years ago is history, not a level to reason about.
  const broken = zones
    .filter(
      (z) =>
        z.invalidated_on &&
        z.invalidated_on >= firstDate &&
        z.floor <= price * 1.25 &&
        z.ceil >= price * 0.75,
    )
    .sort((a, b) => String(b.invalidated_on).localeCompare(String(a.invalidated_on)))
    .slice(0, MAX_BROKEN_ZONES)

  return [...broken, ...live]
}
export function PriceChart({
  bars,
  overlays,
  zones,
  dark,
}: {
  bars: Bars
  overlays: { sma50?: (number | null)[]; sma200?: (number | null)[] }
  zones: Zone[]
  dark: boolean
}) {
  const series = useSeries(dark)
  const [hover, setHover] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)

  const lastPrice = useMemo(() => {
    for (let i = bars.c.length - 1; i >= 0; i--) {
      const v = bars.c[i]
      if (v !== null && Number.isFinite(v)) return v
    }
    return 0
  }, [bars.c])

  const shown = useMemo(
    () => selectZones(zones, lastPrice, bars.t[0] ?? ''),
    [zones, lastPrice, bars.t],
  )

  const W = 860
  const H = 340
  const VH = 64
  const pad = { l: 8, r: 62, t: 12, b: 22 }

  const model = useMemo(() => {
    const closes = bars.c.map((v) => v ?? NaN)
    const n = closes.length
    const values = closes.filter((v) => Number.isFinite(v))
    const zoneLows = shown.filter((z) => !z.invalidated_on).map((z) => z.floor)
    let lo = Math.min(...values, ...(zoneLows.length ? zoneLows : [Infinity]))
    let hi = Math.max(...values, ...shown.map((z) => z.ceil).filter(Number.isFinite))
    if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo) {
      lo = Math.min(...values)
      hi = Math.max(...values)
    }
    const span = hi - lo || 1
    lo -= span * 0.06
    hi += span * 0.06

    const x = (i: number) => pad.l + (i / Math.max(n - 1, 1)) * (W - pad.l - pad.r)
    const y = (v: number) => pad.t + (1 - (v - lo) / (hi - lo)) * (H - pad.t - pad.b)

    const path = (arr: (number | null)[]) => {
      let d = ''
      let pen = false
      arr.forEach((v, i) => {
        if (v === null || !Number.isFinite(v)) {
          pen = false
          return
        }
        d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`
        pen = true
      })
      return d
    }

    const maxVol = Math.max(...bars.v.map((v) => v ?? 0), 1)
    return { n, lo, hi, x, y, path, maxVol }
  }, [bars, shown])

  const ticks = useMemo(() => {
    const out: { v: number; y: number }[] = []
    for (let i = 0; i <= 4; i++) {
      const v = model.lo + ((model.hi - model.lo) * i) / 4
      out.push({ v, y: model.y(v) })
    }
    return out
  }, [model])

  const dateTicks = useMemo(() => {
    const step = Math.max(1, Math.floor(model.n / 6))
    const out: { i: number; label: string }[] = []
    for (let i = 0; i < model.n; i += step) out.push({ i, label: shortDate(bars.t[i]) })
    return out
  }, [model.n, bars.t])

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return
    const px = ((e.clientX - rect.left) / rect.width) * W
    const i = Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (model.n - 1))
    setHover(i >= 0 && i < model.n ? i : null)
  }

  const grid = dark ? '#2a323f' : '#e8ecf2'
  const axisText = dark ? '#8b95a6' : '#6b7484'
  const priceInk = dark ? '#e7eaf0' : '#171b23'

  return (
    <figure className="m-0">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H + VH}`}
        className="w-full"
        role="img"
        aria-label="Adjusted close with 50 and 200 day moving averages and support zones"
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        <defs>
          <pattern id="broken" width="6" height="6" patternTransform="rotate(45)" patternUnits="userSpaceOnUse">
            <line x1="0" y1="0" x2="0" y2="6" stroke={axisText} strokeWidth="1" opacity="0.45" />
          </pattern>
        </defs>

        {ticks.map((t) => (
          <g key={t.v}>
            <line x1={pad.l} x2={W - pad.r} y1={t.y} y2={t.y} stroke={grid} strokeWidth="1" />
            <text x={W - pad.r + 6} y={t.y + 3.5} fontSize="10" fill={axisText} fontFamily="ui-monospace, monospace">
              {t.v.toFixed(0)}
            </text>
          </g>
        ))}

        {/* Zones behind the price line. */}
        {shown.map((z, k) => {
          const yTop = model.y(z.ceil)
          const yBot = model.y(z.floor)
          const broken = Boolean(z.invalidated_on)
          return (
            <g key={k}>
              <rect
                x={pad.l}
                y={Math.min(yTop, yBot)}
                width={W - pad.l - pad.r}
                height={Math.max(Math.abs(yBot - yTop), 1.5)}
                fill={broken ? 'url(#broken)' : series[2]}
                opacity={broken ? 0.4 : 0.1}
              />
              {!broken && (
                <>
                  {/* The floor is the line a stop sits under, so it is drawn
                      solid; the ceiling is dashed because entering the band is
                      gradual where losing it is decisive. */}
                  <line
                    x1={pad.l} x2={W - pad.r} y1={yBot} y2={yBot}
                    stroke={series[2]} strokeWidth="1.75"
                  />
                  <line
                    x1={pad.l} x2={W - pad.r} y1={yTop} y2={yTop}
                    stroke={series[2]} strokeWidth="1" strokeDasharray="4 3" opacity="0.7"
                  />
                </>
              )}
            </g>
          )
        })}

        {overlays.sma200 && (
          <path d={model.path(overlays.sma200)} fill="none" stroke={series[3]} strokeWidth="2" opacity="0.9" />
        )}
        {overlays.sma50 && (
          <path d={model.path(overlays.sma50)} fill="none" stroke={series[1]} strokeWidth="2" opacity="0.9" />
        )}
        <path d={model.path(bars.c)} fill="none" stroke={priceInk} strokeWidth="2" />

        {dateTicks.map((t) => (
          <text
            key={t.i} x={model.x(t.i)} y={H - 4} fontSize="10" fill={axisText}
            textAnchor="middle" fontFamily="ui-monospace, monospace"
          >
            {t.label}
          </text>
        ))}

        {/* Volume strip — its own frame, never a second axis on the price plot. */}
        <g transform={`translate(0, ${H})`}>
          {bars.v.map((v, i) => {
            const h = ((v ?? 0) / model.maxVol) * (VH - 12)
            return (
              <rect
                key={i}
                x={model.x(i)}
                y={VH - h - 2}
                width={Math.max((W - pad.l - pad.r) / model.n - 0.4, 0.6)}
                height={h}
                fill={axisText}
                opacity={hover === i ? 0.9 : 0.32}
              />
            )
          })}
        </g>

        {hover !== null && Number.isFinite(bars.c[hover] ?? NaN) && (
          <g>
            <line
              x1={model.x(hover)} x2={model.x(hover)} y1={pad.t} y2={H + VH - 2}
              stroke={axisText} strokeWidth="1" strokeDasharray="3 3"
            />
            <circle
              cx={model.x(hover)} cy={model.y(bars.c[hover] as number)} r="4"
              fill={priceInk} stroke={dark ? '#0f1319' : '#f6f7f9'} strokeWidth="2"
            />
          </g>
        )}
      </svg>

      <figcaption className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500">
        <LegendSwatch color={priceInk} label="Adjusted close" />
        <LegendSwatch color={series[1]} label="50DMA" />
        <LegendSwatch color={series[3]} label="200DMA" />
        <LegendSwatch color={series[2]} label="Live support zone" />
        <span className="inline-flex items-center gap-1.5">
          <span
            className="inline-block h-2.5 w-4 rounded-[1px] opacity-60"
            style={{
              backgroundImage: `repeating-linear-gradient(45deg, ${axisText} 0 1px, transparent 1px 5px)`,
            }}
          />
          Broken zone
        </span>
        {hover !== null && (
          <span className="ml-auto font-mono tabular-nums text-slate-700 dark:text-slate-300">
            {bars.t[hover]} &middot; {(bars.c[hover] ?? 0).toFixed(2)}
          </span>
        )}
      </figcaption>
    </figure>
  )
}

function LegendSwatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block h-2.5 w-4 rounded-[1px]" style={{ background: color }} />
      {label}
    </span>
  )
}

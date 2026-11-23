import { useState } from 'react'
import { useWidth } from './common'
import { t } from '../../i18n'

export type BedSoakChartPoint = { x: number; y: number; name?: string }

export type BedSoakChartSeries = {
  name: string
  color: string
  data: BedSoakChartPoint[]
  /** Draw a dot at every point, for one point per mesh */
  markers?: boolean
  format: (value: number) => string
}

export type BedSoakChartEvent = { /** Minutes */ x: number; label: string }

export type BedSoakChartReference = { y: number; label?: string; color?: string }

type Props = {
  title: string
  note?: string
  unit: string
  series: BedSoakChartSeries[]
  xMin: number
  xMax: number
  yMin?: number
  yMax?: number
  events?: BedSoakChartEvent[]
  references?: BedSoakChartReference[]
  band?: { from: number; to: number; color: string }
  height?: number
  timeLabel: (minutes: number) => string
  /** Minutes under the pointer, shared by the charts of one run so their crosshairs line up. */
  hoverX: number | null
  onHover: (x: number | null) => void
}

const LEFT = 44
const RIGHT = 78
const BOTTOM = 22

/**
 * The fork's bed soak line chart (ECharts there), in SVG: one value axis, minutes along the bottom, event lines
 * labelled above the plot in as many rows as they need, the latest value at the end of each line.
 */
export function BedSoakChart({ title, note, unit, series, xMin, xMax, yMin, yMax, events = [], references = [], band, height = 150, timeLabel, hoverX, onHover }: Props) {
  const [wrap, width] = useWidth<HTMLDivElement>()
  const [pointerIn, setPointerIn] = useState(false)

  const plotW = Math.max(100, width - LEFT - RIGHT)
  // Event labels sit above the plot, centred on their line, in rows that don't overlap.
  const minutesPerPixel = (xMax - xMin) / plotW
  const rowEnds: number[] = []
  const eventRows = events.map((e) => {
    const half = ((e.label.length * 6.2 + 12) / 2) * minutesPerPixel
    let row = 0
    while (rowEnds[row] != null && e.x - half < rowEnds[row]) row++
    rowEnds[row] = e.x + half
    return row
  })
  const rows = events.length ? Math.max(...eventRows) + 1 : 0
  const top = 10 + 14 * rows
  const chartH = height + (rows > 1 ? 14 * (rows - 1) : 0)
  const plotH = chartH - top - BOTTOM

  // Y range: given, or the data's with a little room, on round steps.
  const values = [...series.flatMap((s) => s.data.map((d) => d.y)), ...references.map((r) => r.y)]
  let lo = yMin ?? (values.length ? Math.min(...values) : 0)
  let hi = yMax ?? (values.length ? Math.max(...values) : 1)
  if (hi - lo < 1e-9) (lo -= 1), (hi += 1)
  const yStep = niceStep((hi - lo) / 4)
  if (yMin == null) lo = Math.floor(lo / yStep) * yStep
  if (yMax == null) hi = Math.ceil(hi / yStep) * yStep
  const sx = (x: number) => LEFT + ((x - xMin) / (xMax - xMin || 1)) * plotW
  const sy = (y: number) => top + plotH - ((y - lo) / (hi - lo || 1)) * plotH
  const xStep = niceStep((xMax - xMin) / Math.max(2, Math.floor(plotW / 70)))
  const xTicks: number[] = []
  for (let x = Math.ceil(xMin / xStep) * xStep; x <= xMax + 1e-9; x += xStep) if (x > xMin) xTicks.push(Math.round(x * 1000) / 1000)
  const yTicks: number[] = []
  for (let y = lo; y <= hi + 1e-9; y += yStep) yTicks.push(Math.round(y * 1000) / 1000)

  // End labels, nudged apart so they don't overlap.
  const ends = series
    .filter((s) => s.data.length)
    .map((s) => {
      const p = s.data[s.data.length - 1]
      return { s, x: sx(p.x), y: sy(p.y), text: s.format(p.y) }
    })
    .sort((a, b) => a.y - b.y)
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 13) ends[i].y = ends[i - 1].y + 13

  const nearest = (s: BedSoakChartSeries, x: number) => {
    let best: BedSoakChartPoint | null = null
    for (const p of s.data) if (!best || Math.abs(p.x - x) < Math.abs(best.x - x)) best = p
    // Only a point near the pointer, so a line that hasn't started yet shows nothing.
    return best && Math.abs(best.x - x) <= Math.max((xMax - xMin) / 25, 0.5) ? best : null
  }
  const hx = hoverX != null && hoverX >= xMin && hoverX <= xMax ? hoverX : null

  return (
    <div className="min-w-0">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <span className="text-[13px] font-semibold">{title}</span>
        {note && <span className="text-[12px] text-ink-3">{note}</span>}
        {series.length > 1 && (
          <span className="inline-flex flex-wrap gap-x-3 text-[12px] text-ink-2">
            {series.map((s) => (
              <span key={s.name} className="whitespace-nowrap">
                <i className="mr-1.5 inline-block h-0.5 w-3 rounded-sm align-middle" style={{ background: s.color }} />
                {s.name}
              </span>
            ))}
          </span>
        )}
        <span className="flex-1" />
        <span className="text-[12px] text-ink-3">{unit}</span>
      </div>
      <div ref={wrap} className="relative" style={{ height: chartH }}>
        <svg
          width={width}
          height={chartH}
          className="block"
          onPointerMove={(e) => {
            const r = (e.currentTarget as SVGElement).getBoundingClientRect()
            const px = e.clientX - r.left
            setPointerIn(true)
            onHover(px >= LEFT && px <= LEFT + plotW ? xMin + ((px - LEFT) / plotW) * (xMax - xMin) : null)
          }}
          onPointerLeave={() => (setPointerIn(false), onHover(null))}
        >
          {band && <rect x={LEFT} width={plotW} y={sy(Math.min(hi, band.to))} height={Math.max(0, sy(Math.max(lo, band.from)) - sy(Math.min(hi, band.to)))} fill={band.color} />}
          {yTicks.map((y) => (
            <g key={`y${y}`}>
              <line x1={LEFT} x2={LEFT + plotW} y1={sy(y)} y2={sy(y)} stroke="var(--line)" />
              <text x={LEFT - 6} y={sy(y) + 4} textAnchor="end" fontSize={11} fill="var(--ink-3)">
                {fmtTick(y)}
              </text>
            </g>
          ))}
          <line x1={LEFT} x2={LEFT + plotW} y1={top + plotH} y2={top + plotH} stroke="var(--line-strong)" />
          {xTicks.map((x) => (
            <text key={`x${x}`} x={sx(x)} y={top + plotH + 15} textAnchor="middle" fontSize={11} fill="var(--ink-3)">
              {x === 0 ? t('common.time.minutes', { m: 0 }) : fmtTick(x)}
            </text>
          ))}
          {references.map((r, i) => (
            <g key={`r${i}`}>
              <line x1={LEFT} x2={LEFT + plotW} y1={sy(r.y)} y2={sy(r.y)} stroke={r.color ?? 'var(--ink-3)'} strokeDasharray="4 4" strokeOpacity={r.color ? 1 : 0.7} />
              {r.label && (
                <text x={LEFT + 4} y={sy(r.y) - 4} fontSize={11} fill={r.color ?? 'var(--ink-3)'}>
                  {r.label}
                </text>
              )}
            </g>
          ))}
          {events.map((e, i) =>
            e.x >= xMin && e.x <= xMax ? (
              <g key={`e${i}`}>
                <line x1={sx(e.x)} x2={sx(e.x)} y1={top - 2} y2={top + plotH} stroke="var(--ink-3)" strokeDasharray="2 3" />
                <text x={sx(e.x)} y={top - 4 - 14 * eventRows[i]} textAnchor="middle" fontSize={11} fill="var(--ink-2)">
                  {e.label}
                </text>
              </g>
            ) : null,
          )}
          {series.map((s) => (
            <g key={s.name}>
              {s.data.length > 1 && <polyline points={s.data.map((p) => `${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(' ')} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" />}
              {(s.markers || s.data.length === 1) && s.data.map((p, i) => <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r={3.5} fill={s.color} stroke="var(--surface)" strokeWidth={1.5} />)}
            </g>
          ))}
          {ends.map((e) => {
            // A label too long for the space right of the plot sits above its point, inside the chart.
            const x = Math.min(e.x, LEFT + plotW) + 6
            const fits = x + e.text.length * 6.8 <= width
            return (
              <text key={e.s.name} x={fits ? x : width - 2} y={fits ? e.y + 4 : e.y - 8} textAnchor={fits ? 'start' : 'end'} fontSize={12} fontWeight={600} fill="var(--ink)">
                {e.text}
              </text>
            )
          })}
          {hx != null && <line x1={sx(hx)} x2={sx(hx)} y1={top} y2={top + plotH} stroke="var(--ink-2)" strokeOpacity={0.6} />}
          {hx != null &&
            series.map((s) => {
              const p = nearest(s, hx)
              return p ? <circle key={`h${s.name}`} cx={sx(p.x)} cy={sy(p.y)} r={4.5} fill={s.color} stroke="var(--surface)" strokeWidth={2} /> : null
            })}
        </svg>
        {hx != null && pointerIn && (
          <div
            className="pointer-events-none absolute z-10 rounded-lg bg-surface px-2.5 py-1.5 text-[12px] leading-snug whitespace-nowrap"
            style={{ left: sx(hx) > width / 2 ? undefined : sx(hx) + 12, right: sx(hx) > width / 2 ? width - sx(hx) + 12 : undefined, top: top, boxShadow: 'var(--shadow-overlay)' }}
          >
            <div className="mb-0.5 text-ink-3">{timeLabel(hx)}</div>
            {series.map((s) => {
              const p = nearest(s, hx)
              return p ? (
                <div key={s.name} className="flex items-center gap-1.5">
                  <i className="inline-block h-2 w-2 rounded-full" style={{ background: s.color }} />
                  <b className="num">{s.format(p.y)}</b>
                  <span className="text-ink-3">
                    {s.name}
                    {p.name ? ` · ${p.name}` : ''}
                  </span>
                </div>
              ) : null
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function niceStep(raw: number) {
  if (!(raw > 0)) return 1
  const p = 10 ** Math.floor(Math.log10(raw))
  const n = raw / p
  return (n < 1.5 ? 1 : n < 3 ? 2 : n < 7 ? 5 : 10) * p
}

function fmtTick(v: number) {
  return Math.abs(v) >= 100 || Number.isInteger(v) ? String(Math.round(v)).replace('-', '−') : String(Number(v.toFixed(2))).replace('-', '−')
}

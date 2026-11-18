import { useEffect, useMemo, useRef, useState, type PointerEvent as RPointerEvent } from 'react'

// A plain SVG time chart: thin lines, a recessive grid, gaps where a value is missing (NaN), and a crosshair with the
// values under it on hover or touch. One y-axis per chart.

export type ChartSeries = {
  key: string
  label: string
  values: number[]
  color: string
  dash?: 'solid' | 'dashed' | 'dotted'
  /** Area fill under the line, 0–1. */
  fill?: number
  /** Leave out of the tooltip (Fluidd's "Show legend" off). */
  hidden?: boolean
}

type YBound = number | ((dataMax: number) => number)

export function LineChart({
  times,
  series,
  height = 110,
  spanMs,
  min,
  max,
  unit = '',
  digits = 1,
  axis = true,
}: {
  times: number[]
  series: ChartSeries[]
  height?: number
  spanMs: number
  min?: YBound
  max?: YBound
  unit?: string
  digits?: number
  axis?: boolean
}) {
  const box = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [hover, setHover] = useState<number | null>(null)
  useEffect(() => {
    const el = box.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setWidth(Math.floor(e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const left = axis ? 38 : 2
  const right = 4
  const top = 6
  const bottom = axis ? 18 : 2
  const plotW = Math.max(10, width - left - right)
  const plotH = Math.max(10, height - top - bottom)

  const tEnd = times.length ? times[times.length - 1] : Date.now()
  const tStart = tEnd - spanMs

  const { lo, hi, ticks } = useMemo(() => {
    let dMin = Infinity
    let dMax = -Infinity
    for (const s of series)
      for (let i = 0; i < s.values.length; i++) {
        const v = s.values[i]
        if (!Number.isFinite(v) || times[i] < tStart) continue
        if (v < dMin) dMin = v
        if (v > dMax) dMax = v
      }
    if (!Number.isFinite(dMin)) (dMin = 0), (dMax = 1)
    let lo = typeof min === 'function' ? min(dMin) : (min ?? Math.min(0, dMin))
    let hi = typeof max === 'function' ? max(dMax) : (max ?? dMax)
    if (!(hi > lo)) hi = lo + (Math.abs(lo) || 1)
    const step = niceStep((hi - lo) / 3)
    if (min == null) lo = Math.floor(lo / step) * step
    if (max == null) hi = Math.ceil(hi / step) * step
    const ticks: number[] = []
    for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-6; v += step) ticks.push(Number(v.toFixed(6)))
    return { lo, hi, ticks }
  }, [series, times, tStart, min, max])

  const x = (t: number) => left + ((t - tStart) / spanMs) * plotW
  const y = (v: number) => top + plotH - ((Math.min(hi, Math.max(lo, v)) - lo) / (hi - lo)) * plotH

  const paths = series.map((s) => {
    let d = ''
    let area = ''
    let run: string[] = []
    const flush = () => {
      if (run.length > 1 && s.fill) area += `M${run[0].split(',')[0]},${top + plotH}L${run.join('L')}L${run[run.length - 1].split(',')[0]},${top + plotH}Z`
      run = []
    }
    for (let i = 0; i < times.length; i++) {
      const v = s.values[i]
      if (times[i] < tStart - 2000 || !Number.isFinite(v)) {
        flush()
        continue
      }
      const p = `${x(times[i]).toFixed(1)},${y(v).toFixed(1)}`
      d += run.length ? `L${p}` : `M${p}`
      run.push(p)
    }
    flush()
    return { s, d, area }
  })

  const xTicks = useMemo(() => {
    const every = niceTime(spanMs / Math.max(2, Math.floor(plotW / 90)))
    const out: number[] = []
    for (let t = Math.ceil(tStart / every) * every; t <= tEnd; t += every) out.push(t)
    return out
  }, [spanMs, plotW, tStart, tEnd])

  const onMove = (e: RPointerEvent) => {
    const rect = (e.currentTarget as SVGElement).getBoundingClientRect()
    const t = tStart + ((e.clientX - rect.left - left) / plotW) * spanMs
    if (!times.length || t < times[0] - 2000) return setHover(null)
    setHover(nearest(times, t))
  }

  const hv = hover != null && hover < times.length ? hover : null
  const tipLeft = hv != null ? x(times[hv]) : 0
  const fmt = (v: number) => (Number.isFinite(v) ? `${v.toFixed(digits)}${unit}` : '–')

  return (
    <div ref={box} className="relative w-full select-none" style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} className="block touch-pan-y" onPointerMove={onMove} onPointerDown={onMove} onPointerLeave={() => setHover(null)} role="img">
          {axis &&
            ticks.map((v) => (
              <g key={v}>
                <line x1={left} x2={left + plotW} y1={y(v)} y2={y(v)} stroke="var(--line)" strokeWidth={1} />
                <text x={left - 6} y={y(v) + 3.5} textAnchor="end" fontSize={10.5} fill="var(--ink-3)" className="num">
                  {fmtTick(v)}
                </text>
              </g>
            ))}
          {axis &&
            xTicks.map((t) => (
              <text key={t} x={x(t)} y={height - 4} textAnchor="middle" fontSize={10.5} fill="var(--ink-3)" className="num">
                {new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}
              </text>
            ))}
          {paths.map(({ s, area }) => area && <path key={`a${s.key}`} d={area} fill={s.color} fillOpacity={s.fill} stroke="none" />)}
          {paths.map(({ s, d }) => (
            <path key={s.key} d={d} fill="none" stroke={s.color} strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" strokeDasharray={s.dash === 'dashed' ? '6 4' : s.dash === 'dotted' ? '1.5 3.5' : undefined} />
          ))}
          {hv != null && (
            <g>
              <line x1={tipLeft} x2={tipLeft} y1={top} y2={top + plotH} stroke="var(--ink-3)" strokeOpacity={0.6} />
              {series.map((s) =>
                Number.isFinite(s.values[hv]) ? <circle key={s.key} cx={tipLeft} cy={y(s.values[hv])} r={3.5} fill={s.color} stroke="var(--surface)" strokeWidth={2} /> : null,
              )}
            </g>
          )}
        </svg>
      )}
      {hv != null && (
        <div
          className="pointer-events-none absolute z-10 rounded-lg bg-surface px-2.5 py-1.5 text-[12px]"
          style={{ boxShadow: 'var(--shadow-overlay)', top: 0, ...(tipLeft > width / 2 ? { right: width - tipLeft + 10 } : { left: tipLeft + 10 }) }}
        >
          <div className="num mb-0.5 text-ink-3">{new Date(times[hv]).toLocaleTimeString()}</div>
          {series
            .filter((s) => !s.hidden)
            .map((s) => (
              <div key={s.key} className="flex items-center gap-1.5 whitespace-nowrap">
                <span className="swatch" style={{ background: s.color }} />
                <span className="text-ink-2">{s.label}</span>
                <b className="num ml-auto pl-3 text-ink">{fmt(s.values[hv])}</b>
              </div>
            ))}
        </div>
      )}
    </div>
  )
}

function nearest(times: number[], t: number) {
  let lo = 0
  let hi = times.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (times[mid] < t) lo = mid
    else hi = mid
  }
  return Math.abs(times[lo] - t) <= Math.abs(times[hi] - t) ? lo : hi
}

function niceStep(raw: number) {
  if (!(raw > 0)) return 1
  const p = 10 ** Math.floor(Math.log10(raw))
  const f = raw / p
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p
}

function niceTime(ms: number) {
  const steps = [60e3, 120e3, 300e3, 600e3, 900e3, 1800e3, 3600e3]
  return steps.find((s) => s >= ms) ?? 3600e3
}

function fmtTick(v: number) {
  if (Math.abs(v) >= 1000) return `${Math.round(v / 100) / 10}k`
  return String(Number(v.toFixed(2)))
}

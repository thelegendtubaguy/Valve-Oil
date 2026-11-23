import { useEffect, useMemo, useRef, useState } from 'react'
import { usePrinter } from '../state/printer'
import { prettyName } from '../lib/format'
import { t } from '../i18n'
import { bucketPoints } from './temp-chart-points'

// Series colors come from --series-N in fixed order; an entity keeps its slot whatever is hidden.
const SLOTS = 8

type Series = { obj: string; label: string; slot: number }

const isBoxHeater = (o: string) => o.startsWith('heater_generic heater_box')

/** Heaters to chart, in a stable order: nozzle, bed, chamber heaters, Box heaters. */
export function heaterObjects(objs: string[]) {
  const generic = objs.filter((o) => o.startsWith('heater_generic '))
  return [...['extruder', 'heater_bed'].filter((o) => objs.includes(o)), ...generic.filter((o) => !isBoxHeater(o)), ...generic.filter(isBoxHeater)]
}

function useSeries(): Series[] {
  return usePrinter((s) => heaterObjects(s.objects).slice(0, SLOTS).map((obj, i) => ({ obj, slot: i + 1, label: obj === 'extruder' ? t('app.heaters.nozzle') : obj === 'heater_bed' ? t('app.heaters.bed') : isBoxHeater(obj) ? t('app.heaters.box', { n: obj.slice(-1) }) : prettyName(obj) })))
}

/** A series' line colour, as a short stroke. */
const swatch = (slot: number, w: number) => <svg width={w} height="4" aria-hidden><line x1="1" x2={w - 1} y1="2" y2="2" stroke={`var(--series-${slot})`} strokeWidth="2" strokeLinecap="round" /></svg>

// Windows in seconds, labelled in minutes.
const WINDOWS = [300, 600, 1200]

/**
 * The heater history chart. `bare` drops the card, title, and legend for a card that lists the heaters itself; it then
 * takes which series are hidden from `hidden`, and puts the time range on the chart.
 */
export function TempChart({ className = '', bare, hidden: hiddenProp }: { className?: string; bare?: boolean; hidden?: Set<string> }) {
  const series = useSeries()
  const temps = usePrinter((s) => s.temps)
  const live = usePrinter((s) => Object.fromEntries(series.map((x) => [x.obj, { t: s.status[x.obj]?.temperature as number | undefined, target: s.status[x.obj]?.target as number | undefined }])))
  const [ownHidden, setHidden] = useState<Set<string>>(new Set())
  const hidden = hiddenProp ?? ownHidden
  const [win, setWin] = useState(600)
  const [hover, setHover] = useState<number | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const [size, setSize] = useState({ w: 600, h: 220 })

  useEffect(() => {
    const el = box.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.max(200, e.contentRect.width), h: Math.max(140, e.contentRect.height) }))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const shown = series.filter((x) => !hidden.has(x.obj))
  const n = Math.min(win, Math.max(0, ...series.map((x) => temps[x.obj]?.temperatures.length ?? 0)))
  const data = useMemo(
    () =>
      Object.fromEntries(
        series.map((x) => {
          const t = temps[x.obj]
          const all = t?.temperatures ?? []
          // The number of the series' last sample plus one; the first drawn sample's number is this less what's drawn.
          const end = t?.end ?? all.length
          const vals = all.slice(-n)
          const targets = (t?.targets ?? []).slice(-n)
          return [x.obj, { temps: vals, targets, first: end - vals.length, targetsFirst: end - targets.length }]
        }),
      ),
    [series, temps, n],
  )

  const pad = { l: 34, r: 10, t: bare ? 34 : 8, b: 20 }
  const W = size.w
  const H = size.h
  const iw = W - pad.l - pad.r
  const ih = H - pad.t - pad.b
  // The Y axis fits the shown lines and targets, with three or four gridlines at a round step, so a Box at 30 °C and a
  // cold nozzle fill the chart instead of a sliver at the bottom of 0–100°.
  let lo = Infinity
  let hi = -Infinity
  for (const x of shown)
    for (const vals of [data[x.obj].temps, data[x.obj].targets])
      for (const v of vals)
        if (v > 0) {
          if (v < lo) lo = v
          if (v > hi) hi = v
        }
  if (lo > hi) [lo, hi] = [20, 40]
  const step = [5, 10, 20, 25, 50, 100].find((s) => s >= Math.max(hi - lo, 6) / 3) ?? 100
  const bottom = Math.max(0, Math.floor(lo / step) * step)
  // A reading a little over a gridline, like a bed overshooting 100°, runs into the top padding instead of adding a step.
  const top = Math.max(bottom + 2 * step, Math.ceil((hi - step / 10) / step) * step)
  // Until the window fills, the history sits at its right end.
  const offset = n < win ? ((win - n) / (win - 1)) * iw : 0
  const xAt = (i: number) => pad.l + (n <= 1 ? iw : (i / (win - 1)) * iw) + offset
  const yAt = (v: number) => pad.t + ih - ((v - bottom) / (top - bottom)) * ih
  // About one point per 2 px, from the window rather than the samples there are, so the buckets keep their size as the
  // history fills.
  const stride = Math.max(1, Math.floor(win / (iw / 2)))
  const path = (vals: number[], first: number, how: 'mean' | 'last') => {
    let d = ''
    let on = false
    for (const p of bucketPoints(vals, first, stride, how)) {
      if (p.v != null) {
        d += `${on ? 'L' : 'M'}${xAt(p.i).toFixed(1)} ${yAt(p.v).toFixed(1)}`
        on = true
      } else on = false
    }
    return d
  }

  const onMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    const i = Math.round(((e.clientX - r.left - pad.l - offset) / iw) * (win - 1))
    setHover(n && i >= 0 && i < n ? i : null)
  }

  const minutes = win / 60
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => ({ x: pad.l + f * iw, now: f === 1, text: f === 1 ? t('app.chart.now') : t('app.chart.minutes_back', { m: Math.round(minutes * (1 - f)) }) }))

  const windows = (
    <div className="ml-auto flex gap-0.5 rounded-lg bg-canvas p-0.5 shadow-[inset_0_0_0_1px_var(--line)]">
      {WINDOWS.map((w) => (
        <button key={w} onClick={() => setWin(w)} className={`rounded-md border-0 px-2 py-0.5 text-[11.5px] font-medium ${win === w ? 'bg-surface text-ink shadow-[var(--shadow-btn)]' : 'bg-transparent text-ink-3'}`}>
          {bare ? t('app.chart.window_short', { m: w / 60 }) : t('common.time.minutes', { m: w / 60 })}
        </button>
      ))}
    </div>
  )
  const Outer = bare ? 'div' : 'section'
  return (
    <Outer className={`${bare ? '' : 'card'} flex flex-col ${className}`}>
      {bare ? null : (
        <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <h3 className="card-title m-0!">{t('app.chart.title')}</h3>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {series.map((x) => {
              const off = hidden.has(x.obj)
              const l = live[x.obj]
              return (
                <button
                  key={x.obj}
                  type="button"
                  aria-pressed={!off}
                  title={off ? t('app.chart.show', { name: x.label }) : t('app.chart.hide', { name: x.label })}
                  onClick={() => setHidden((h) => new Set(h.has(x.obj) ? [...h].filter((o) => o !== x.obj) : [...h, x.obj]))}
                  className={`flex items-center gap-1.5 rounded-md border-0 bg-transparent px-1 py-0.5 text-[12px] hover:bg-hover ${off ? 'opacity-40' : ''}`}
                >
                  {swatch(x.slot, 14)}
                  <span className="text-ink-2">{x.label}</span>
                  <span className="num font-semibold text-ink">{l?.t != null ? `${Math.round(l.t)}°` : '—'}</span>
                  {l?.target ? <span className="num text-ink-3">/{Math.round(l.target)}</span> : null}
                </button>
              )
            })}
          </div>
          {windows}
        </div>
      )}
      <div ref={box} className="relative min-h-[150px] flex-1">
        {bare && <div className="absolute top-0 right-0 z-[1]">{windows}</div>}
        <svg width={W} height={H} className="absolute inset-0 block touch-none" onPointerMove={onMove} onPointerLeave={() => setHover(null)} role="img" aria-label={t('app.chart.aria')}>
          {Array.from({ length: (top - bottom) / step + 1 }, (_, i) => bottom + i * step).map((v) => (
            <g key={v}>
              <line x1={pad.l} x2={W - pad.r} y1={yAt(v)} y2={yAt(v)} stroke="var(--line)" strokeWidth={1} />
              <text x={pad.l - 6} y={yAt(v) + 3.5} textAnchor="end" fontSize={10.5} fill="var(--ink-3)" className="num">
                {v}°
              </text>
            </g>
          ))}
          {ticks.map((tick) => (
            <text key={tick.x} x={tick.x} y={H - 5} textAnchor={tick.now ? 'end' : 'middle'} fontSize={10.5} fill="var(--ink-3)">
              {tick.text}
            </text>
          ))}
          {shown.map((x) => (
            <path key={`${x.obj}-t`} d={path(data[x.obj].targets, data[x.obj].targetsFirst, 'last')} fill="none" stroke={`var(--series-${x.slot})`} strokeOpacity={0.55} strokeWidth={1.25} strokeDasharray="4 4" />
          ))}
          {shown.map((x) => (
            <path key={x.obj} d={path(data[x.obj].temps, data[x.obj].first, 'mean')} fill="none" stroke={`var(--series-${x.slot})`} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          ))}
          {hover != null && (
            <g>
              <line x1={xAt(hover)} x2={xAt(hover)} y1={pad.t} y2={pad.t + ih} stroke="var(--ink-3)" strokeWidth={1} />
              {shown.map((x) => {
                const v = data[x.obj].temps[hover]
                return !v ? null : <circle key={x.obj} cx={xAt(hover)} cy={yAt(v)} r={4} fill={`var(--series-${x.slot})`} stroke="var(--surface)" strokeWidth={2} />
              })}
            </g>
          )}
        </svg>
        {hover != null && (
          <div
            className="pointer-events-none absolute top-1 z-10 grid gap-0.5 rounded-lg bg-surface px-2.5 py-2 text-[12px]"
            style={{ boxShadow: 'var(--shadow-overlay)', left: xAt(hover) > W / 2 ? xAt(hover) - 150 : xAt(hover) + 12, width: 138 }}
          >
            <div className="mb-0.5 text-[11px] text-ink-3">{Math.round((n - 1 - hover) / 60) > 0 ? t('common.time.minutes_ago', { m: Math.round((n - 1 - hover) / 60) }) : t('app.chart.seconds_ago', { s: n - 1 - hover })}</div>
            {shown.map((x) => {
              const v = data[x.obj].temps[hover]
              const tg = data[x.obj].targets[hover]
              return (
                <div key={x.obj} className="flex items-center gap-1.5">
                  {swatch(x.slot, 10)}
                  <b className="num">{v ? `${v.toFixed(1)}°` : '—'}</b>
                  <span className="truncate text-ink-3">
                    {x.label}
                    {tg ? ` → ${Math.round(tg)}` : ''}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </Outer>
  )
}

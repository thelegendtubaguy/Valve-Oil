import { Activity } from 'lucide-react'
import { Card } from '../ui'
import { usePrinter } from '../../state/printer'
import { LineChart, type ChartSeries } from './LineChart'
import { SYSTEM_RETENTION, useSystemBuffers } from './metrics'
import type { SeriesBuffer } from './chart-data'
import { mcuLabel } from './bits'
import { t } from '../../i18n'

type Line = { col: string; label: string }

/** One small chart with its name and latest values over it, as Fluidd's inline charts. */
function InlineChart({ title, buf, lines, unit = ' %', digits = 1, max, min, hint }: { title: string; buf: SeriesBuffer; lines: Line[]; unit?: string; digits?: number; max?: number | ((m: number) => number); min?: number | ((m: number) => number); hint?: string }) {
  const series: ChartSeries[] = lines.map((l, i) => ({ key: l.col, label: l.label, values: buf.cols[l.col] ?? [], color: `var(--series-${i + 1})`, fill: lines.length === 1 ? 0.12 : 0 }))
  const last = (vals: number[]) => {
    for (let i = vals.length - 1; i >= Math.max(0, vals.length - 5); i--) if (Number.isFinite(vals[i])) return vals[i]
    return NaN
  }
  return (
    <div className="min-w-0 rounded-xl bg-inset p-3" style={{ boxShadow: 'inset 0 0 0 1px var(--line)' }} title={hint}>
      <div className="mb-1 flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
        <span className="text-[12.5px] font-semibold text-ink-2">{title}</span>
        <span className="ml-auto flex flex-wrap items-baseline gap-x-3">
          {series.map((s) => {
            const v = last(s.values)
            return (
              <span key={s.key} className="flex items-baseline gap-1.5">
                {series.length > 1 && <span className="swatch self-center" style={{ background: s.color }} />}
                {series.length > 1 && <span className="text-[11.5px] text-ink-3">{s.label}</span>}
                <b className="num text-[15px] text-ink">
                  {Number.isFinite(v) ? v.toFixed(digits) : '—'}
                  <small className="text-[11px] font-semibold text-ink-3">{unit}</small>
                </b>
              </span>
            )
          })}
        </span>
      </div>
      <LineChart times={buf.times} series={series} height={92} spanMs={SYSTEM_RETENTION * 1000} max={max} min={min} unit={unit} digits={digits} />
    </div>
  )
}

const pctMax = (m: number) => Math.max(100, m)
// MCU load sits low; give it headroom like Fluidd rather than flattening it against 100 %.
const mcuMax = (m: number) => (m <= 80 ? Math.ceil((m + 5) / 10) * 10 + 5 : Math.max(100, m))

export function UsageCard({ cores }: { cores: number }) {
  const b = useSystemBuffers()
  const mcus = usePrinter((s) => s.objects.filter((o) => o === 'mcu' || o.startsWith('mcu ')))
  const hasHealth = usePrinter((s) => s.status.vo_klipper_health != null)
  const has = (buf: SeriesBuffer, col: string) => buf.cols[col]?.some(Number.isFinite)
  return (
    <Card title={t('system.usage.title')} icon={<Activity size={15} />} aside={t('system.usage.last_10')}>
      <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        {has(b.host, 'cpu') && <InlineChart title={t('system.usage.host_cpu')} buf={b.host} lines={[{ col: 'cpu', label: 'CPU' }]} max={pctMax} />}
        {has(b.klipper, 'load') && <InlineChart title={t('system.usage.system_load')} buf={b.klipper} lines={[{ col: 'load', label: t('system.usage.load') }]} unit={` / ${cores}`} digits={2} max={(m) => Math.max(cores, m)} hint={t('system.usage.system_load_hint')} />}
        {has(b.host, 'memused') && <InlineChart title={t('system.usage.memory')} buf={b.host} lines={[{ col: 'memused', label: t('system.usage.memory_used') }]} max={pctMax} />}
        {has(b.host, 'cpu_temp') && <InlineChart title={t('system.usage.cpu_temp')} buf={b.host} lines={[{ col: 'cpu_temp', label: 'CPU' }]} unit=" °C" min={(m: number) => Math.max(0, Math.floor((m - 10) / 10) * 10)} max={(m) => Math.ceil((m + 5) / 10) * 10} />}
        <InlineChart
          title={t('system.usage.klipper_load')}
          buf={b.klipper}
          lines={hasHealth ? [{ col: 'cputime_change', label: t('system.usage.load') }, { col: 'main_thread', label: t('system.usage.main_thread') }] : [{ col: 'cputime_change', label: t('system.usage.load') }]}
          max={pctMax}
          hint={hasHealth ? t('system.usage.klipper_load_hint_thread') : t('system.usage.klipper_load_hint')}
        />
        {hasHealth && (
          <InlineChart
            title={t('system.usage.timers_title')}
            buf={b.klipper}
            lines={[{ col: 'timers', label: t('system.usage.timers') }]}
            unit=""
            digits={0}
            min={0}
            max={(m) => Math.max(100, Math.ceil((m * 1.2) / 50) * 50)}
            hint={t('system.usage.timers_hint')}
          />
        )}
        {has(b.host, 'moonraker') && <InlineChart title={t('system.usage.moonraker_load')} buf={b.host} lines={[{ col: 'moonraker', label: t('system.usage.load') }]} max={pctMax} />}
        {mcus.map((m) =>
          b.mcus[m] ? (
            <InlineChart
              key={m}
              title={t('system.mcu.title', { name: mcuLabel(m) })}
              buf={b.mcus[m]}
              lines={b.mcus[m].cols.load ? [{ col: 'load', label: t('system.usage.load') }, { col: 'awake', label: t('system.mcu.awake') }] : [{ col: 'awake', label: t('system.mcu.awake') }]}
              max={mcuMax}
              digits={2}
            />
          ) : null,
        )}
      </div>
    </Card>
  )
}

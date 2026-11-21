import { useEffect, useState } from 'react'
import { ChartLine, ListTree, Pencil, Plus } from 'lucide-react'
import { Card, Empty, Spinner } from '../ui'
import { usePrinter } from '../../state/printer'
import { LineChart, type ChartSeries } from './LineChart'
import { DIAGNOSTICS_RETENTION, allCards, defaultCard, loadLayout, startCollecting, useDiagnosticsBuffer, useLayout, type ChartAxis, type DiagnosticsCardConfig } from './diagnostics-store'
import type { SeriesBuffer } from './chart-data'
import { DiagnosticsEditor } from './DiagnosticsEditor'
import { StateExplorer } from './StateExplorer'
import { t } from '../../i18n'
import { tr } from '../../i18n/rich'

export function Diagnostics() {
  const { loaded, containers } = useLayout()
  const klippy = usePrinter((s) => s.klippy)
  const [editing, setEditing] = useState<DiagnosticsCardConfig | null>(null)
  useEffect(() => {
    loadLayout().then(startCollecting)
  }, [])
  const cards = allCards(containers).filter((c) => c.enabled)
  return (
    <div className="grid grid-cols-1 gap-3.5">
      <Card
        title={t('system.tabs.diagnostics')}
        icon={<ChartLine size={15} />}
        aside={
          <button className="btn btn-sm btn-primary" onClick={() => setEditing({ ...defaultCard(), id: '' })}>
            <Plus size={14} /> {t('system.diagnostics.add_chart')}
          </button>
        }
      >
        <p className="m-0 text-ink-2">
          {/* i18n-ignore: a collector expression */}
          {tr('system.diagnostics.about', { example: <span className="code">printer.motion_report.live_velocity</span> })}
        </p>
        {klippy !== 'ready' && <p className="mb-0 text-warn">{t('system.diagnostics.paused')}</p>}
      </Card>
      {!loaded ? (
        <div className="grid place-items-center p-6">
          <Spinner size={20} />
        </div>
      ) : cards.length === 0 ? (
        <Empty>{t('system.diagnostics.none')}</Empty>
      ) : (
        <div className="grid grid-cols-1 items-start gap-3.5 xl:grid-cols-2">
          {cards.map((c) => (
            <ChartCard key={c.id} config={c} onEdit={() => setEditing(structuredClone(c))} />
          ))}
        </div>
      )}
      <Card title={t('system.diagnostics.state_explorer')} icon={<ListTree size={15} />}>
        <StateExplorer />
      </Card>
      {editing && <DiagnosticsEditor card={editing} onClose={() => setEditing(null)} />}
    </div>
  )
}

const num = (v: number | string | undefined) => (v === undefined || v === '' || !Number.isFinite(Number(v)) ? undefined : Number(v))

function ChartCard({ config, onEdit }: { config: DiagnosticsCardConfig; onEdit: () => void }) {
  const buf = useDiagnosticsBuffer()
  // One chart per axis rather than two scales on one chart, so each line reads against its own unit.
  const axes = config.axes.filter((a) => a.enabled && a.metrics.length)
  const each = Math.max(110, Math.round((Number(config.height) || 300) / Math.max(1, axes.length)))
  return (
    <Card
      title={config.title || t('system.diagnostics.chart')}
      icon={<ChartLine size={15} />}
      aside={
        <button className="btn btn-ghost btn-sm" onClick={onEdit}>
          <Pencil size={13} /> {t('system.diagnostics.edit_chart')}
        </button>
      }
    >
      {axes.length === 0 ? (
        <Empty>{t('system.diagnostics.no_lines')}</Empty>
      ) : (
        <div className="grid gap-3">
          {axes.map((a, i) => (
            <AxisChart key={i} axis={a} buf={buf} height={each} />
          ))}
        </div>
      )}
    </Card>
  )
}

function AxisChart({ axis, buf, height }: { axis: ChartAxis; buf: SeriesBuffer; height: number }) {
  const series: ChartSeries[] = axis.metrics.map((m, i) => ({
    key: `${i}:${m.collector}`,
    label: m.name,
    values: buf.cols[m.collector] ?? [],
    color: m.style.lineColor,
    dash: m.style.lineStyle,
    fill: m.style.fillOpacity / 100,
    hidden: !m.style.displayLegend,
  }))
  const last = (v: number[]) => (v.length ? v[v.length - 1] : NaN)
  return (
    <div>
      <div className="mb-1 flex flex-wrap items-center gap-x-3.5 gap-y-1 text-[12.5px]">
        {axis.showLegend && axis.unit && <span className="font-semibold text-ink-3">{axis.unit}</span>}
        {series
          .filter((s) => !s.hidden)
          .map((s) => {
            const v = last(s.values)
            return (
              <span key={s.key} className="flex items-center gap-1.5">
                <span className="swatch" style={{ background: s.color }} />
                <span className="text-ink-2">{s.label}</span>
                <b className="num text-ink">{Number.isFinite(v) ? Number(v.toFixed(3)) : '—'}</b>
              </span>
            )
          })}
      </div>
      <LineChart times={buf.times} series={series} height={height} spanMs={DIAGNOSTICS_RETENTION * 1000} min={num(axis.min)} max={num(axis.max)} unit={axis.unit ? ` ${axis.unit}` : ''} digits={3} axis={axis.showLegend} />
    </div>
  )
}

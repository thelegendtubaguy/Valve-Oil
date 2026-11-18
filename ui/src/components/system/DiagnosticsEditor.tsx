import { useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Play, Plus, Search, Trash2 } from 'lucide-react'
import { Dialog, Segmented, Spinner, Toggle, confirm } from '../ui'
import { attempt } from '../toast'
import { collectorPrinter, defaultMetric, deleteCard, evalCollectors, saveCard, type ChartAxis, type DiagnosticsCardConfig, type Metric } from './diagnostics-store'
import { StateExplorer } from './StateExplorer'

type Tab = 'card' | 'left' | 'right'

/** Fluidd's chart editor: the card, then each y-axis with its lines. */
export function DiagnosticsEditor({ card: initial, onClose }: { card: DiagnosticsCardConfig; onClose: () => void }) {
  const [card, setCard] = useState<DiagnosticsCardConfig>(() => {
    const c = structuredClone(initial)
    while (c.axes.length < 2) c.axes.push({ enabled: false, unit: '', showLegend: true, metrics: [] })
    return c
  })
  const [tab, setTab] = useState<Tab>('card')
  const [saving, setSaving] = useState(false)
  const isNew = initial.id === ''
  const setAxis = (i: number, a: ChartAxis) => setCard((c) => ({ ...c, axes: c.axes.map((x, j) => (j === i ? a : x)) }))
  const valid = card.title.trim() !== '' && Number(card.height) >= 1 && card.axes.every((a) => !a.enabled || a.unit.trim() !== '') && card.axes.every((a) => [a.min, a.max].every((v) => v === undefined || v === '' || Number.isFinite(Number(v))))
  const save = async () => {
    setSaving(true)
    const out: DiagnosticsCardConfig = {
      ...card,
      height: Number(card.height),
      axes: card.axes.map((a) => ({ ...a, min: a.min === '' || a.min === undefined ? undefined : Number(a.min), max: a.max === '' || a.max === undefined ? undefined : Number(a.max) })),
    }
    const ok = await attempt(async () => (await saveCard(out), true))
    setSaving(false)
    if (ok) onClose()
  }
  const remove = async () => {
    if (!(await confirm({ title: 'Remove this chart?', body: `${card.title} is removed from Valve Oil UI and Fluidd.`, confirm: 'Remove', danger: true }))) return
    if (await attempt(async () => (await deleteCard(card.id), true))) onClose()
  }
  return (
    <Dialog
      open
      onClose={onClose}
      size="wide"
      title={isNew ? 'Add chart' : 'Edit chart'}
      footer={
        <>
          {!isNew && (
            <button className="btn btn-danger mr-auto" onClick={remove}>
              <Trash2 size={14} /> Remove
            </button>
          )}
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={!valid || saving} onClick={save}>
            {saving && <Spinner size={14} />} {isNew ? 'Add' : 'Save'}
          </button>
        </>
      }
    >
      <div className="mb-4">
        <Segmented
          value={tab}
          onChange={setTab}
          label="Chart settings"
          options={[
            { value: 'card', label: 'Card' },
            { value: 'left', label: `Left Y-axis${card.axes[0].unit ? ` · ${card.axes[0].unit}` : ''}` },
            { value: 'right', label: `Right Y-axis${card.axes[1].unit ? ` · ${card.axes[1].unit}` : ''}` },
          ]}
        />
      </div>
      {tab === 'card' ? (
        <div className="grid gap-3">
          <Field label="Title">
            <input className="field" value={card.title} onChange={(e) => setCard({ ...card, title: e.target.value })} />
          </Field>
          <Field label="Height" hint="The whole card’s chart area; with both axes on, each gets half.">
            <div className="relative max-w-40">
              <input className="field pr-9" inputMode="numeric" value={card.height} onChange={(e) => setCard({ ...card, height: e.target.value as unknown as number })} />
              <span className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-[12px] text-ink-3">px</span>
            </div>
          </Field>
          <p className="m-0 text-[12.5px] text-ink-3">Valve Oil UI draws each y-axis as its own chart, one above the other. Fluidd draws them together.</p>
        </div>
      ) : (
        <AxisEditor key={tab} axis={card.axes[tab === 'left' ? 0 : 1]} onChange={(a) => setAxis(tab === 'left' ? 0 : 1, a)} />
      )}
    </Dialog>
  )
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="grid min-w-0 gap-1.5">
      <span className="lbl">{label}</span>
      {children}
      {hint && <span className="text-[12px] text-ink-3">{hint}</span>}
    </label>
  )
}

function SwitchRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="text-ink-2">{label}</span>
      <Toggle checked={checked} onChange={onChange} label={label} />
    </div>
  )
}

function AxisEditor({ axis, onChange }: { axis: ChartAxis; onChange: (a: ChartAxis) => void }) {
  const [open, setOpen] = useState<number | null>(axis.metrics.length === 1 ? 0 : null)
  const setMetric = (i: number, m: Metric) => onChange({ ...axis, metrics: axis.metrics.map((x, j) => (j === i ? m : x)) })
  return (
    <div className="grid gap-3">
      <div className="grid gap-x-6 sm:grid-cols-2">
        <SwitchRow label="Enable" checked={axis.enabled} onChange={(v) => onChange({ ...axis, enabled: v })} />
        <SwitchRow label="Show legend" checked={axis.showLegend} onChange={(v) => onChange({ ...axis, showLegend: v })} />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Unit">
          <input className="field" value={axis.unit} onChange={(e) => onChange({ ...axis, unit: e.target.value })} />
        </Field>
        <Field label="Min (optional)">
          <input className="field" inputMode="decimal" value={axis.min ?? ''} onChange={(e) => onChange({ ...axis, min: e.target.value })} />
        </Field>
        <Field label="Max (optional)">
          <input className="field" inputMode="decimal" value={axis.max ?? ''} onChange={(e) => onChange({ ...axis, max: e.target.value })} />
        </Field>
      </div>
      <div className="section-label mt-1">Metrics</div>
      <div className="grid gap-2">
        {axis.metrics.map((m, i) => (
          <div key={i} className="min-w-0 rounded-xl bg-inset" style={{ boxShadow: 'inset 0 0 0 1px var(--line)' }}>
            <div className="flex items-center gap-2 px-3 py-2">
              <button className="flex min-w-0 flex-1 items-center gap-2 border-0 bg-transparent p-0 text-left text-ink" onClick={() => setOpen(open === i ? null : i)} aria-expanded={open === i}>
                {open === i ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                <span className="swatch" style={{ background: m.style.lineColor }} />
                <span className="min-w-0 flex-none truncate font-semibold">{m.name || 'Unnamed'}</span>
                <span className="code min-w-0 flex-1 truncate text-ink-3">{m.collector}</span>
              </button>
              <button className="iconbtn h-7 w-7" onClick={() => onChange({ ...axis, metrics: axis.metrics.filter((_, j) => j !== i) })} aria-label={`Remove ${m.name}`} title="Remove">
                <Trash2 size={14} />
              </button>
            </div>
            {open === i && <MetricEditor metric={m} unit={axis.unit} onChange={(x) => setMetric(i, x)} />}
          </div>
        ))}
      </div>
      <div>
        <button
          className="btn btn-sm"
          onClick={() => {
            onChange({ ...axis, metrics: [...axis.metrics, defaultMetric()] })
            setOpen(axis.metrics.length)
          }}
        >
          <Plus size={14} /> Add metric
        </button>
      </div>
    </div>
  )
}

function MetricEditor({ metric, unit, onChange }: { metric: Metric; unit: string; onChange: (m: Metric) => void }) {
  const [result, setResult] = useState('-')
  const [browse, setBrowse] = useState(false)
  const text = useRef<HTMLTextAreaElement>(null)
  const style = (s: Partial<Metric['style']>) => onChange({ ...metric, style: { ...metric.style, ...s } })
  const run = async () => {
    const r = await attempt(async () => (await evalCollectors(await collectorPrinter([metric.collector]), [metric.collector]))[metric.collector])
    if (r !== undefined) setResult(typeof r === 'number' ? String(Math.round(r * 1000) / 1000) : String(r))
  }
  // Puts the picked path where the cursor is in the collector, as Fluidd does.
  const insert = (path: string) => {
    setBrowse(false)
    const el = text.current
    const a = el?.selectionStart ?? metric.collector.length
    const b = el?.selectionEnd ?? metric.collector.length
    onChange({ ...metric, collector: metric.collector.slice(0, a) + path + metric.collector.slice(b) })
  }
  return (
    <div className="grid gap-3 border-t border-line px-3 py-3">
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_auto]">
        <Field label="Name">
          <input className="field" value={metric.name} onChange={(e) => onChange({ ...metric, name: e.target.value })} />
        </Field>
        <Field label="Line color">
          <input type="color" className="h-[34px] w-14 cursor-pointer rounded-lg border-0 bg-transparent p-0" value={metric.style.lineColor} onChange={(e) => style({ lineColor: e.target.value })} />
        </Field>
        <Field label="Fill color">
          <input type="color" className="h-[34px] w-14 cursor-pointer rounded-lg border-0 bg-transparent p-0" value={metric.style.fillColor ?? metric.style.lineColor} onChange={(e) => style({ fillColor: e.target.value })} />
        </Field>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Line style">
          <select className="field" value={metric.style.lineStyle} onChange={(e) => style({ lineStyle: e.target.value as Metric['style']['lineStyle'] })}>
            <option value="solid">Solid</option>
            <option value="dotted">Dotted</option>
            <option value="dashed">Dashed</option>
          </select>
        </Field>
        <Field label={`Fill opacity · ${metric.style.fillOpacity}%`}>
          <input type="range" min={0} max={100} value={metric.style.fillOpacity} onChange={(e) => style({ fillOpacity: Number(e.target.value) })} className="h-[34px] accent-[var(--accent)]" />
        </Field>
      </div>
      <SwitchRow label="Show legend" checked={metric.style.displayLegend} onChange={(v) => style({ displayLegend: v })} />
      <Field label="Collector">
        <textarea ref={text} className="field code min-h-16" spellCheck={false} rows={2} value={metric.collector} onChange={(e) => onChange({ ...metric, collector: e.target.value })} />
      </Field>
      <div className="flex flex-wrap items-center gap-2">
        <button className="btn btn-sm" onClick={() => setBrowse(true)}>
          <Search size={14} /> Browse available metrics
        </button>
        <button className="btn btn-sm" onClick={run}>
          <Play size={14} /> Run collector
        </button>
        <span className="ml-auto text-[12.5px] text-ink-3">Last result</span>
        <span className="code chip">
          {result}
          {unit && result !== '-' && !result.startsWith('Error') ? ` ${unit}` : ''}
        </span>
      </div>
      <Dialog open={browse} onClose={() => setBrowse(false)} title="Metrics explorer" size="wide">
        <StateExplorer onPick={insert} />
      </Dialog>
    </div>
  )
}

// Mockup B's temperatures card. The heater rows double as the chart's legend (click a colour bar to hide or show
// that line), each heater shows how far it is toward its target, and sensors sit in a compact grid under them.
import { useState } from 'react'
import { Flame } from 'lucide-react'
import { draftKeys, objectLabel, send } from '../control/common'
import { boxHeaterNumber, heaterGcode, targetError, targetRange, type HeaterConfig } from '../control/heaters'
import { TempChart } from '../TempChart'
import { toast } from '../toast'
import { usePrinter } from '../../state/printer'
import { boxHeaterMax, useBox } from '../../state/box'
import { prettyName } from '../../lib/format'
import { TempActions } from './Deck'
import { t } from '../../i18n'
import './deck.css'

const SLOTS = 8

/** Heaters in the chart's order, so a heater's colour matches its line. */
function useHeaters() {
  return usePrinter((s) => {
    const objs = s.objects
    const names: string[] = []
    if (objs.includes('extruder')) names.push('extruder')
    if (objs.includes('heater_bed')) names.push('heater_bed')
    for (const o of objs) if (o.startsWith('heater_generic ') && !o.startsWith('heater_generic heater_box')) names.push(o)
    for (const o of objs) if (o.startsWith('heater_generic heater_box')) names.push(o)
    return names
  })
}

function heaterLabel(obj: string) {
  if (obj === 'extruder') return t('app.heaters.nozzle')
  if (obj === 'heater_bed') return t('app.heaters.bed')
  const n = boxHeaterNumber(obj)
  return n != null ? t('app.heaters.box', { n }) : prettyName(obj)
}

export function TempsCard({ className = '' }: { className?: string }) {
  const heaters = useHeaters()
  const sensors = usePrinter((s) => s.objects.filter((o) => (o.startsWith('temperature_sensor ') || o.startsWith('temperature_fan ')) && !o.split(' ')[1].startsWith('_')))
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const toggle = (o: string) => setHidden((h) => new Set(h.has(o) ? [...h].filter((x) => x !== o) : [...h, o]))
  return (
    <section className={`card tc ${className}`}>
      <h3 className="card-title">
        <Flame size={14} /> {t('control.screen.tab_temps')}
        <span className="aside flex gap-1.5">
          <TempActions />
        </span>
      </h3>
      <div className="tc-heaters">
        {heaters.map((o, i) => (
          <HeaterRow key={o} obj={o} slot={i < SLOTS ? i + 1 : null} shown={!hidden.has(o)} onToggle={() => toggle(o)} />
        ))}
      </div>
      {sensors.length > 0 && (
        <div className="tc-sensors">
          {sensors.map((o) => (
            <SensorCell key={o} obj={o} />
          ))}
        </div>
      )}
      <TempChart bare hidden={hidden} className="tc-chart" />
    </section>
  )
}

function HeaterRow({ obj, slot, shown, onToggle }: { obj: string; slot: number | null; shown: boolean; onToggle: () => void }) {
  const st = usePrinter((s) => s.status[obj] ?? {})
  const cfg = usePrinter((s) => s.settings[obj.toLowerCase()] as HeaterConfig | undefined)
  const box = useBox()
  const n = boxHeaterNumber(obj)
  const limit = n != null ? boxHeaterMax(box, n, Infinity) : Infinity
  const target = Math.round((st.target as number | undefined) ?? 0)
  const range = targetRange(cfg, Number.isFinite(limit) ? limit : null, target)
  const power = (st.power as number | undefined) ?? 0
  const temp = st.temperature as number | undefined
  const label = heaterLabel(obj)
  const color = slot ? `var(--series-${slot})` : 'var(--ink-3)'
  const on = target > 0
  // How far from room temperature to the target the heater has come; full once it's within a degree.
  const progress = on && temp != null ? Math.max(0, Math.min(1, target - temp <= 1 ? 1 : (temp - 25) / Math.max(1, target - 25))) : 0
  const set = (v: number) => {
    const err = targetError(v, range)
    if (err) return toast(err, 'error')
    send(heaterGcode(obj, v))
  }
  return (
    <div className={`tc-row ${on ? 'on' : ''}`} style={{ '--c': color } as React.CSSProperties}>
      <button type="button" className={`tc-sw ${shown ? '' : 'hid'}`} aria-pressed={shown} onClick={onToggle} title={shown ? t('home.temps.hide_line', { name: label }) : t('home.temps.show_line', { name: label })} disabled={!slot}>
        <span />
      </button>
      <div className="tc-name">
        <b>{label}</b>
        {on && <small className="num">{t('home.temps.power', { pct: Math.round(power * 100) })}</small>}
      </div>
      <span className="tc-now num">{temp != null ? `${temp.toFixed(1)}°` : '—'}</span>
      <TargetField label={label} value={target} disabled={range.locked} onSet={set} />
      {on && (
        <div className="tc-bar" aria-hidden>
          <span style={{ width: `${progress * 100}%` }} />
        </div>
      )}
    </div>
  )
}

/** The target: plain text until you hover or click it, "off" when the heater is off. Enter sets it; 0 turns it off. */
function TargetField({ label, value, disabled, onSet }: { label: string; value: number; disabled?: boolean; onSet: (v: number) => void }) {
  const [draft, setDraft] = useState<string | null>(null)
  const commit = () => {
    if (draft == null) return
    const v = Math.round(Number(draft))
    setDraft(null)
    if (draft.trim() !== '' && Number.isFinite(v) && v !== value) onSet(v)
  }
  return (
    <label className={`tc-tgt ${value > 0 ? 'set' : ''}`} title={disabled ? undefined : t('home.temps.target_hint', { name: label.toLowerCase() })}>
      <span className="arrow">→</span>
      <input
        className="num"
        inputMode="numeric"
        aria-label={t('control.target_stepper.target', { name: label })}
        disabled={disabled}
        placeholder={t('app.temps.off')}
        value={draft ?? (value > 0 ? String(value) : '')}
        onFocus={(e) => (setDraft(value > 0 ? String(value) : ''), e.target.select())}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={draftKeys(setDraft)}
      />
      {(value > 0 || draft != null) && <span className="unit">°</span>}
    </label>
  )
}

function SensorCell({ obj }: { obj: string }) {
  const st = usePrinter((s) => s.status[obj] ?? {})
  const temp = st.temperature as number | undefined
  const fan = obj.startsWith('temperature_fan ')
  const name = objectLabel(obj)
  return (
    <div className="tc-sensor" title={fan && typeof st.target === 'number' ? t('home.temps.fan_title', { name, pct: Math.round(((st.speed as number) ?? 0) * 100), target: Math.round(st.target) }) : name}>
      <span className="k">{name}</span>
      <span className="num v">
        {temp != null ? `${temp.toFixed(1)}°` : '—'}
        {fan && typeof st.speed === 'number' && <small> · {Math.round(st.speed * 100)}%</small>}
      </span>
    </div>
  )
}

import { useState, type ReactNode } from 'react'
import { ArrowDownToLine, Droplet, Fan, Flame, Gauge, Heater, Thermometer } from 'lucide-react'
import { Choices, Dialog, Stepper } from '../ui'
import { send, usePercent, useZOffset } from '../control/common'
import { TargetStepper, startTarget } from '../control/TargetStepper'
import { heaterGcode, targetRange, type HeaterConfig } from '../control/heaters'
import { fanLabel } from '../control/Outputs'
import { usePrinter } from '../../state/printer'
import { gcode } from '../control/gcode'
import { t, type MessageKey } from '../../i18n'

// The in-print Tune panel, as the touchscreen's Tune screen: heater targets first (the chamber only where there is
// one), then speed, flow, and Z offset, then the part, aux, and exhaust fans, each as − value +.

const Z_STEPS = [0.005, 0.01, 0.05]
const TEMP_STEPS = [5, 1]

// QIDI's fans and the M106 P index the printer's own fan macro takes for each; P2 drives both auxiliary fans. Labels
// are message keys, looked up while rendering.
const QIDI_FANS: { obj: string; labelKey: MessageKey | null; p: number }[] = [
  { obj: 'fan_generic cooling_fan', labelKey: 'home.tune.part_fan', p: 0 },
  { obj: 'fan_generic auxiliary_cooling_fan', labelKey: 'home.tune.aux_fan', p: 2 },
  { obj: 'fan_generic chamber_circulation_fan', labelKey: 'home.tune.exhaust_fan', p: 3 },
]

export function TuneDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog open onClose={onClose} title={t('home.tune.title')} size="wide">
      <TunePanel />
    </Dialog>
  )
}

export function TunePanel() {
  const [zStep, setZStep] = useState(Z_STEPS[0])
  const [tStep, setTStep] = useState(TEMP_STEPS[0])
  const heaters = usePrinter((s) => ['extruder', 'heater_bed', 'heater_generic chamber'].filter((o) => s.objects.includes(o)))
  const fans = usePrinter((s) => {
    const qidi = QIDI_FANS.filter((f) => s.objects.includes(f.obj))
    return qidi.length ? qidi : s.objects.includes('fan') ? [{ obj: 'fan', labelKey: null, p: -1 }] : []
  })
  return (
    <div className="grid gap-3">
      <div className="grid gap-2.5 sm:grid-cols-3">
        {heaters.map((o) => (
          <HeaterTile key={o} obj={o} step={tStep} />
        ))}
      </div>
      <div className="grid gap-2.5 sm:grid-cols-3">
        <PercentTile factor="speed_factor" icon={<Gauge size={14} />} label={t('home.tune.speed')} command="M220" step={5} min={10} max={500} />
        <PercentTile factor="extrude_factor" icon={<Droplet size={14} />} label={t('home.tune.flow')} command="M221" step={1} min={50} max={200} />
        <ZOffsetTile step={zStep} />
      </div>
      {fans.length > 0 && (
        <div className="grid gap-2.5 sm:grid-cols-3">
          {fans.map((f) => (
            <FanTile key={f.obj} obj={f.obj} label={f.labelKey ? t(f.labelKey) : fanLabel('fan')} p={f.p} />
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-line pt-3">
        <span className="flex items-center gap-2">
          <span className="text-ink-2">{t('home.tune.z_step')}</span>
          <Choices value={zStep} options={Z_STEPS.map((v) => ({ value: v, label: String(v) }))} onChange={setZStep} />
        </span>
        <span className="flex items-center gap-2">
          <span className="text-ink-2">{t('home.tune.temp_step')}</span>
          <Choices value={tStep} options={TEMP_STEPS.map((v) => ({ value: v, label: `${v}°` }))} onChange={setTStep} />
        </span>
      </div>
    </div>
  )
}

function Tile({ icon, label, now, hot, children }: { icon: ReactNode; label: string; now?: string | null; hot?: boolean; children: ReactNode }) {
  return (
    // A row on a phone, so the whole panel fits on one screen; a tile with the label on top from sm up.
    <div className={`tile grid-cols-[88px_minmax(0,1fr)] items-center gap-x-2! gap-y-2! py-2.5! sm:grid-cols-1 ${hot ? 'hot' : ''}`}>
      <div className="flex flex-wrap items-center gap-x-1.5 text-[12.5px] font-semibold text-ink-2">
        <span className="text-accent-ink">{icon}</span>
        {label}
        {now && <span className="num w-full font-semibold text-ink-3 sm:ml-auto sm:w-auto">{t('home.tune.now', { value: now })}</span>}
      </div>
      {children}
    </div>
  )
}

const HEATER_TILES: Record<string, { labelKey: MessageKey; icon: ReactNode }> = {
  extruder: { labelKey: 'app.heaters.nozzle', icon: <Flame size={14} /> },
  heater_bed: { labelKey: 'app.heaters.bed', icon: <Heater size={14} /> },
  'heater_generic chamber': { labelKey: 'app.heaters.chamber', icon: <Thermometer size={14} /> },
}

function HeaterTile({ obj, step }: { obj: string; step: number }) {
  const st = usePrinter((s) => s.status[obj] ?? {})
  const cfg = usePrinter((s) => s.settings[obj] as HeaterConfig | undefined)
  const target = (st.target as number | undefined) ?? 0
  const { labelKey, icon } = HEATER_TILES[obj]
  const label = t(labelKey)
  return (
    <Tile icon={icon} label={label} now={typeof st.temperature === 'number' ? `${Math.round(st.temperature)}°` : null} hot>
      <TargetStepper size="lg" label={label} step={step} start={startTarget(obj)} target={target} range={targetRange(cfg, null, target)} onSet={(t) => send(heaterGcode(obj, t))} />
    </Tile>
  )
}

function PercentTile({ factor, icon, label, command, step, min, max }: { factor: 'speed_factor' | 'extrude_factor'; icon: ReactNode; label: string; command: string; step: number; min: number; max: number }) {
  const v = usePercent(factor)
  return (
    <Tile icon={icon} label={label}>
      <Stepper label={label} value={v} step={step} min={min} max={max} unit="%" onChange={(x) => send(gcode`${command} S${Math.round(x)}`)} />
    </Tile>
  )
}

function ZOffsetTile({ step }: { step: number }) {
  const z = useZOffset()
  return (
    <Tile icon={<ArrowDownToLine size={14} />} label={t('home.tune.z_offset')}>
      <Stepper label={t('home.tune.z_offset')} value={z} step={step} min={-5} max={5} digits={3} onChange={(v) => send(gcode`SET_GCODE_OFFSET Z_ADJUST=${Math.round((v - z) * 1000) / 1000} MOVE=1`)} />
    </Tile>
  )
}

function FanTile({ obj, label, p }: { obj: string; label: string; p: number }) {
  const speed = usePrinter((s) => Math.round(((s.status[obj]?.speed as number | undefined) ?? 0) * 100))
  const set = (pct: number) => {
    const s255 = Math.round((Math.min(100, Math.max(0, pct)) / 100) * 255)
    send(p < 0 ? (s255 ? gcode`M106 S${s255}` : 'M107') : gcode`M106 P${p} S${s255}`)
  }
  return (
    <Tile icon={<Fan size={14} />} label={label}>
      <Stepper label={label} value={speed} step={10} min={0} max={100} unit="%" onChange={set} />
    </Tile>
  )
}

import { useState } from 'react'
import { ChevronDown, Ellipsis, Fan, Flame, Snowflake, Thermometer } from 'lucide-react'
import { Card, Dialog, Empty, MenuButton, confirm } from '../ui'
import { TempChart } from '../TempChart'
import { Note, objectLabel, send, useHasCommand, useJobActive, usePrinting } from './common'
import { TargetStepper, startTarget } from './TargetStepper'
import { boxHeaterNumber, targetError, targetRange, type HeaterConfig } from './heaters'
import { encodeGcodeParamValue, gcode } from './gcode'
import { attempt } from '../toast'
import { usePrinter } from '../../state/printer'
import { applyPreset, usePresets } from '../../state/presets'
import { boxHeaterMax, useBox } from '../../state/box'
import { navigate } from '../../lib/router'
import { t } from '../../i18n'
import { tr } from '../../i18n/rich'

/** A heater's name as people know it: Nozzle, Bed, Chamber, Box 1 heater. */
export function heaterLabel(obj: string) {
  if (obj === 'extruder') return t('control.temps.nozzle')
  if (obj === 'heater_bed') return t('control.temps.bed')
  const box = boxHeaterNumber(obj)
  if (box != null) return t('control.temps.box_heater', { n: box })
  return objectLabel(obj)
}

/** The name SET_HEATER_TEMPERATURE takes. */
export const heaterName = (obj: string) => obj.replace(/^heater_generic /, '')

/** The Temperatures tab: the chart, every heater's target, temperature fans, and sensors. */
export function TempsTab() {
  return (
    <div className="grid grid-cols-1 gap-3.5">
      <div className="h-[300px]">
        <TempChart className="h-full" />
      </div>
      <div className="grid grid-cols-1 items-start gap-3.5 lg:grid-cols-2">
        <HeatersCard />
        <div className="grid min-w-0 grid-cols-1 gap-3.5">
          <TemperatureFansCard />
          <SensorsCard />
        </div>
      </div>
    </div>
  )
}

/** Presets and Cool down, beside the tabs. */
export function TempsActions() {
  const [presets] = usePresets()
  const active = useJobActive()
  return (
    <>
      <MenuButton
        className="btn btn-sm"
        label={t('control.temps.presets')}
        button={
          <>
            <Flame size={13} /> {t('control.temps.presets')} <ChevronDown size={13} />
          </>
        }
        items={
          presets.length
            ? presets.map((p) => ({ label: p.name, onSelect: () => void attempt(() => applyPreset(p)) }))
            : [{ label: t('control.temps.no_presets'), onSelect: () => navigate('settings') }]
        }
      />
      <button
        className="btn btn-sm"
        onClick={async () => {
          if (active && !(await confirm({ title: t('control.temps.cool_down_title'), body: t('control.temps.cool_down_body'), confirm: t('control.temps.cool_down'), danger: true }))) return
          send('TURN_OFF_HEATERS')
        }}
      >
        <Snowflake size={13} /> {t('control.temps.cool_down')}
      </button>
    </>
  )
}

function HeatersCard() {
  const heaters = usePrinter((s) => s.objects.filter((o) => o === 'extruder' || o === 'heater_bed' || o.startsWith('heater_generic ')))
  const plain = heaters.filter((o) => boxHeaterNumber(o) == null)
  const box = heaters.filter((o) => boxHeaterNumber(o) != null)
  return (
    <Card title={t('control.temps.heaters')} icon={<Flame size={14} />}>
      <div className="grid gap-1">
        {plain.map((o) => (
          <HeaterRow key={o} obj={o} boxLimit={null} />
        ))}
        {box.length > 0 && <BoxHeaterRows objs={box} />}
      </div>
      <Note>{t('control.temps.heaters_note')}</Note>
    </Card>
  )
}

// One useBox for all Box heaters, for their filament limits.
function BoxHeaterRows({ objs }: { objs: string[] }) {
  const box = useBox()
  return (
    <>
      {objs.map((o) => {
        const limit = boxHeaterMax(box, boxHeaterNumber(o)!, Infinity)
        return <HeaterRow key={o} obj={o} boxLimit={Number.isFinite(limit) ? limit : null} />
      })}
    </>
  )
}

function HeaterRow({ obj, boxLimit }: { obj: string; boxLimit: number | null }) {
  const st = usePrinter((s) => s.status[obj] ?? {})
  const cfg = usePrinter((s) => s.settings[obj.toLowerCase()] as HeaterConfig | undefined)
  const printing = usePrinting()
  const mpc = useHasCommand('MPC_CALIBRATE')
  const [calibrate, setCalibrate] = useState<'pid' | 'mpc' | null>(null)
  const target = (st.target as number | undefined) ?? 0
  const temp = st.temperature as number | undefined
  const power = (st.power as number | undefined) ?? 0
  const range = targetRange(cfg, boxLimit, target)
  const label = heaterLabel(obj)
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1.5 rounded-lg px-1 py-1.5 sm:grid-cols-[minmax(0,1fr)_minmax(150px,190px)_auto]">
      <div className="min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="min-w-0 truncate font-semibold">{label}</span>
          <span className={`num ml-auto text-[17px] font-bold ${target > 0 ? 'text-accent-ink' : ''}`}>
            {temp != null ? temp.toFixed(1) : '—'}
            <span className="text-[12px] text-ink-3">°C</span>
          </span>
        </div>
        <div className="mt-1 flex items-center gap-2">
          <span className="h-1 flex-1 overflow-hidden rounded-full bg-inset" title={t('control.temps.heater_power')}>
            <i className="block h-full rounded-full transition-[width] duration-500" style={{ width: `${Math.round(power * 100)}%`, background: 'var(--orange)' }} />
          </span>
          <span className="num w-9 text-right text-[11.5px] text-ink-3" title={t('control.temps.heater_power')}>{target > 0 ? `${Math.round(power * 100)}%` : ''}</span>
        </div>
      </div>
      <div className="col-span-2 row-start-2 sm:col-span-1 sm:col-start-2 sm:row-start-1">
        <TargetStepper label={label} start={startTarget(obj)} target={target} range={range} onSet={(v) => send(gcode`SET_HEATER_TEMPERATURE HEATER=${encodeGcodeParamValue(heaterName(obj))} TARGET=${v}`)} />
      </div>
      <div className="col-start-2 row-start-1 sm:col-start-3">
        <MenuButton
          label={t('control.temps.actions', { name: label })}
          button={<Ellipsis size={16} />}
          items={[
            { label: t('control.temps.turn_off'), onSelect: () => send(`SET_HEATER_TEMPERATURE HEATER=${encodeGcodeParamValue(heaterName(obj))} TARGET=0`), disabled: printing || target === 0 },
            { label: <span className="code">PID_CALIBRATE</span>, onSelect: () => setCalibrate('pid'), disabled: printing }, // i18n-ignore: command names
            mpc && { label: <span className="code">MPC_CALIBRATE</span>, onSelect: () => setCalibrate('mpc'), disabled: printing || cfg?.control !== 'mpc' }, // i18n-ignore: command names
          ]}
        />
      </div>
      {range.locked && <div className="col-span-2 text-[12px] text-ink-3 sm:col-span-3">{t('control.temps.box_locked')}</div>}
      {calibrate && <CalibrateDialog obj={obj} kind={calibrate} onClose={() => setCalibrate(null)} />}
    </div>
  )
}

function CalibrateDialog({ obj, kind, onClose }: { obj: string; kind: 'pid' | 'mpc'; onClose: () => void }) {
  const cfg = usePrinter((s) => s.settings[obj.toLowerCase()] as HeaterConfig | undefined)
  const [target, setTarget] = useState('100')
  const [breakpoints, setBreakpoints] = useState('')
  const range = targetRange(cfg, null)
  const value = Number(target)
  const error = target.trim() === '' || !(value > 0) ? t('control.temps.target_positive') : targetError(value, range)
  const bp = breakpoints.trim() === '' ? null : Number(breakpoints)
  const bpError = bp != null && !(bp > 0) ? t('control.temps.breakpoints_error') : null
  const name = heaterLabel(obj)
  const command = kind === 'pid' ? 'PID_CALIBRATE' : 'MPC_CALIBRATE'
  const run = () => {
    if (error || bpError) return
    send(gcode`${command} HEATER=${encodeGcodeParamValue(heaterName(obj))} TARGET=${value}${kind === 'mpc' && bp != null ? gcode` FAN_BREAKPOINTS=${bp}` : ''}`)
    onClose()
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('control.temps.calibrate_title', { kind: kind === 'pid' ? 'PID' : 'MPC', name })}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button className="btn btn-primary" disabled={!!error || !!bpError} onClick={run}>
            {t('control.temps.calibrate')}
          </button>
        </>
      }
    >
      <div className="grid gap-3">
        <label className="grid gap-1.5">
          <span className="lbl">{t('control.temps.target_temperature')}</span>
          <input className="field" inputMode="numeric" autoFocus value={target} onChange={(e) => setTarget(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && run()} />
          {error && <span className="text-[12px] text-bad">{error}</span>}
        </label>
        {kind === 'mpc' && (
          <label className="grid gap-1.5">
            <span className="lbl">{t('control.temps.fan_breakpoints')}</span>
            <input className="field" inputMode="numeric" value={breakpoints} onChange={(e) => setBreakpoints(e.target.value)} />
            {bpError && <span className="text-[12px] text-bad">{bpError}</span>}
          </label>
        )}
        <div className="text-[12.5px] text-ink-2">
          {tr('control.temps.calibrate_note', { command: <span className="code">{command}</span>, name: name.toLowerCase() })}
        </div>
      </div>
    </Dialog>
  )
}

function TemperatureFansCard() {
  const fans = usePrinter((s) => s.objects.filter((o) => o.startsWith('temperature_fan ') && !o.slice(16).startsWith('_')))
  if (!fans.length) return null
  return (
    <Card title={t('control.temps.temperature_fans')} icon={<Fan size={14} />}>
      <div className="grid gap-1">
        {fans.map((o) => (
          <TemperatureFanRow key={o} obj={o} />
        ))}
      </div>
    </Card>
  )
}

function TemperatureFanRow({ obj }: { obj: string }) {
  const st = usePrinter((s) => s.status[obj] ?? {})
  const cfg = usePrinter((s) => s.settings[obj.toLowerCase()] as HeaterConfig | undefined)
  const target = (st.target as number | undefined) ?? 0
  const label = objectLabel(obj)
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_minmax(150px,190px)] items-center gap-3 py-1.5">
      <div className="min-w-0">
        <div className="truncate font-semibold">{label}</div>
        <div className="num text-[12.5px] text-ink-3">
          {t('control.temps.fan_speed', { temp: typeof st.temperature === 'number' ? `${st.temperature.toFixed(1)} °C` : '—', speed: Math.round(((st.speed as number | undefined) ?? 0) * 100) })}
        </div>
      </div>
      <TargetStepper label={label} target={target} range={targetRange(cfg, null, target)} onSet={(v) => send(gcode`SET_TEMPERATURE_FAN_TARGET TEMPERATURE_FAN=${encodeGcodeParamValue(obj.slice(16))} TARGET=${v}`)} />
    </div>
  )
}

function SensorsCard() {
  const sensors = usePrinter((s) => s.objects.filter((o) => (o.startsWith('temperature_sensor ') || o.startsWith('temperature_probe ')) && !o.split(' ')[1].startsWith('_')))
  return (
    <Card title={t('control.temps.sensors')} icon={<Thermometer size={14} />}>
      {sensors.length ? (
        <div className="grid">
          {sensors.map((o) => (
            <SensorRow key={o} obj={o} />
          ))}
        </div>
      ) : (
        <Empty>{t('control.temps.no_sensors')}</Empty>
      )}
    </Card>
  )
}

function SensorRow({ obj }: { obj: string }) {
  const st = usePrinter((s) => s.status[obj] ?? {})
  const temp = st.temperature as number | undefined
  const lo = st.measured_min_temp as number | undefined
  const hi = st.measured_max_temp as number | undefined
  return (
    <div className="row-k">
      <span className="k truncate">{objectLabel(obj)}</span>
      <span className="v">
        {temp != null ? `${temp.toFixed(1)} °C` : '—'}
        {lo != null && hi != null && <span className="ml-2 text-[11.5px] font-medium text-ink-3">{`${lo.toFixed(0)}–${hi.toFixed(0)}`}</span>}
      </span>
    </div>
  )
}

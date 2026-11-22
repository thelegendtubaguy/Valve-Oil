import type { ReactNode } from 'react'
import { Gauge, ScanLine, Undo2 } from 'lucide-react'
import { Card, Empty, Stepper, Toggle } from '../ui'
import { Note, objectLabel, send } from './common'
import { encodeGcodeParamValue, gcode } from './gcode'
import { usePrinter } from '../../state/printer'
import { t } from '../../i18n'

/** The Limits tab: printer limits, runout sensors, and firmware retraction where the printer has it. */
export function LimitsTab() {
  return (
    <div className="grid grid-cols-1 items-start gap-3.5 lg:grid-cols-2">
      <LimitsCard />
      <div className="grid min-w-0 grid-cols-1 gap-3.5">
        <RunoutCard />
        <RetractCard />
      </div>
    </div>
  )
}

/** A labelled stepper with the configured value to go back to. */
function Limit({ label, value, reset, unit, step, min, max, digits = 0, onSet }: { label: ReactNode; value: number; reset: number; unit: string; step: number; min: number; max: number; digits?: number; onSet: (v: number) => void }) {
  const changed = Math.abs(value - reset) > 10 ** -(digits + 1)
  return (
    <div className="grid gap-1">
      <span className="section-label flex justify-between gap-2">
        {label}
        {changed ? (
          <button type="button" className="border-0 bg-transparent p-0 text-[11px] font-semibold text-accent-ink normal-case" onClick={() => onSet(reset)} title={t('control.limits.back_to_configured', { value: reset.toFixed(digits), unit })}>
            {t('control.limits.reset_to', { value: reset.toFixed(digits) })}
          </button>
        ) : (
          <span className="font-semibold normal-case">{t('control.limits.as_configured')}</span>
        )}
      </span>
      <Stepper label={typeof label === 'string' ? label : undefined} value={value} step={step} min={min} max={max} digits={digits} unit={unit} onChange={onSet} />
    </div>
  )
}

export function LimitsCard() {
  const th = usePrinter((s) => s.status.toolhead ?? {})
  const cfg = usePrinter((s) => s.settings.printer ?? {})
  const velocity = (cfg.max_velocity as number | undefined) ?? 100
  const accel = (cfg.max_accel as number | undefined) ?? 100
  const scv = (cfg.square_corner_velocity as number | undefined) ?? 5
  const mcr = Math.round(((cfg.minimum_cruise_ratio as number | undefined) ?? 0.5) * 100)
  const a2d = (cfg.max_accel_to_decel as number | undefined) ?? accel / 2
  const set = (arg: string, v: number) => send(gcode`SET_VELOCITY_LIMIT ${arg}=${v}`)
  return (
    <Card title={t('control.limits.printer_limits')} icon={<Gauge size={14} />}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Limit label={t('control.limits.velocity')} unit="mm/s" value={(th.max_velocity as number | undefined) ?? velocity} reset={velocity} step={10} min={1} max={velocity} onSet={(v) => set('VELOCITY', v)} />
        <Limit label={t('control.limits.acceleration')} unit="mm/s²" value={(th.max_accel as number | undefined) ?? accel} reset={accel} step={500} min={1} max={accel} onSet={(v) => set('ACCEL', v)} />
        <Limit label={t('control.limits.square_corner_velocity')} unit="mm/s" value={(th.square_corner_velocity as number | undefined) ?? scv} reset={scv} step={0.5} min={0} max={scv} digits={1} onSet={(v) => set('SQUARE_CORNER_VELOCITY', v)} />
        {th.minimum_cruise_ratio != null && (
          <Limit label={t('control.limits.minimum_cruise_ratio')} unit="%" value={Math.round((th.minimum_cruise_ratio as number) * 100)} reset={mcr} step={5} min={0} max={99} onSet={(v) => set('MINIMUM_CRUISE_RATIO', v / 100)} />
        )}
        {th.max_accel_to_decel != null && (
          <Limit label={t('control.limits.accel_to_decel')} unit="mm/s²" value={th.max_accel_to_decel as number} reset={a2d} step={500} min={1} max={a2d} onSet={(v) => set('ACCEL_TO_DECEL', v)} />
        )}
      </div>
      <div className="mt-3">
        <Note>{t('control.limits.note')}</Note>
      </div>
    </Card>
  )
}

export function RunoutCard() {
  const sensors = usePrinter((s) =>
    s.objects.filter((o) => (o === 'hall_filament_width_sensor' || o.startsWith('filament_switch_sensor ') || o.startsWith('filament_motion_sensor ')) && s.status[o]?.filament_detected != null),
  )
  return (
    <Card title={t('control.limits.runout_sensors')} icon={<ScanLine size={14} />}>
      {sensors.length ? (
        <div className="grid gap-2">
          {sensors.map((o) => (
            <RunoutRow key={o} obj={o} />
          ))}
        </div>
      ) : (
        <Empty>{t('control.limits.no_runout_sensors')}</Empty>
      )}
    </Card>
  )
}

function RunoutRow({ obj }: { obj: string }) {
  const st = usePrinter((s) => s.status[obj] ?? {})
  const name = obj.trim().split(/\s+/).pop() ?? obj
  const label = objectLabel(obj)
  const set = (on: boolean) => {
    const lines = [`SET_FILAMENT_SENSOR SENSOR=${encodeGcodeParamValue(name)} ENABLE=${+on}`]
    // Older Klipper doesn't sync the runout state with the width sensor.
    if (name === 'hall_filament_width_sensor') lines.unshift(`${on ? 'ENABLE' : 'DISABLE'}_FILAMENT_WIDTH_SENSOR`)
    send(lines.join('\n'))
  }
  const enabled = st.enabled !== false
  return (
    <div className="flex items-center gap-3">
      <div className="min-w-0 flex-1">
        <div className="truncate font-semibold">{label}</div>
        <div className={`text-[12.5px] ${!enabled ? 'text-ink-3' : st.filament_detected ? 'text-good' : 'text-warn'}`}>{!enabled ? t('common.off') : st.filament_detected ? t('control.limits.filament_detected') : t('control.limits.empty')}</div>
      </div>
      <Toggle label={t('control.limits.sensor_enabled', { name: label })} checked={enabled} onChange={set} />
    </div>
  )
}

export function RetractCard() {
  const st = usePrinter((s) => s.status.firmware_retraction as Record<string, number> | undefined)
  const cfg = usePrinter((s) => s.settings.firmware_retraction as Record<string, number> | undefined)
  if (!cfg || !st) return null
  const max = (base: number | undefined, fallback: number, digits: number) => (base && base > 0 ? Math.round(base * 2 * 10 ** digits) / 10 ** digits : fallback)
  const set = (arg: string, v: number) => send(gcode`SET_RETRACTION ${arg}=${v}`)
  return (
    <Card title={t('control.limits.firmware_retraction')} icon={<Undo2 size={14} />}>
      <div className="grid gap-3 sm:grid-cols-2">
        <Limit label={t('control.limits.retract_length')} unit="mm" digits={2} step={0.05} min={0} max={max(cfg.retract_length, 15, 2)} value={st.retract_length ?? 0} reset={cfg.retract_length ?? 0} onSet={(v) => set('RETRACT_LENGTH', v)} />
        <Limit label={t('control.limits.retract_speed')} unit="mm/s" step={5} min={0} max={max(cfg.retract_speed, 100, 0)} value={st.retract_speed ?? 0} reset={cfg.retract_speed ?? 0} onSet={(v) => set('RETRACT_SPEED', v)} />
        <Limit
          label={t('control.limits.unretract_extra_length')}
          unit="mm"
          digits={2}
          step={0.05}
          min={0}
          max={max(cfg.unretract_extra_length, 15, 2)}
          value={st.unretract_extra_length ?? 0}
          reset={cfg.unretract_extra_length ?? 0}
          onSet={(v) => set('UNRETRACT_EXTRA_LENGTH', v)}
        />
        <Limit label={t('control.limits.unretract_speed')} unit="mm/s" step={5} min={0} max={max(cfg.unretract_speed, 100, 0)} value={st.unretract_speed ?? 0} reset={cfg.unretract_speed ?? 0} onSet={(v) => set('UNRETRACT_SPEED', v)} />
      </div>
    </Card>
  )
}

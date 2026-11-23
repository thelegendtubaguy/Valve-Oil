import { useEffect, useRef, useState, type ReactNode } from 'react'
import { attempt, toast } from '../toast'
import { printer, runGcode } from '../../state/printer'
import { clampJog, moveGcode } from './gcode'
import { t } from '../../i18n'
import './control.css'

/** Runs G-code, logging it to the console and toasting a failure. */
export const send = (gcode: string) => attempt(() => runGcode(gcode))

/**
 * Jogs an axis by a relative distance, shortened to stay within the axis limits. Says so instead of moving when the
 * toolhead is already at the end of travel. Rates are mm/s.
 */
export function jog(axis: 'X' | 'Y' | 'Z', distance: number, rate: number, clientMacro: string | null) {
  const th = printer.get().status.toolhead ?? {}
  const i = 'XYZ'.indexOf(axis)
  const d = clampJog(th.position?.[i], distance, th.axis_minimum?.[i], th.axis_maximum?.[i])
  if (d === 0) return toast(t('control.end_of_travel', { axis }), 'error')
  return send(moveGcode({ [axis]: d }, rate, false, clientMacro))
}

/**
 * A range slider that sends its value when released, not while dragging, so a drag doesn't flood the printer with
 * commands. It follows the printer's value whenever it isn't being dragged.
 */
export function Slider({
  value,
  onCommit,
  min = 0,
  max = 100,
  step = 1,
  disabled,
  label,
  format = (v) => `${v}`,
}: {
  value: number
  onCommit: (v: number) => void
  min?: number
  max?: number
  step?: number
  disabled?: boolean
  label: string
  format?: (v: number) => string
}) {
  const [draft, setDraft] = useState<number | null>(null)
  useEffect(() => setDraft(null), [value])
  const commit = () => {
    if (draft != null && draft !== value) onCommit(draft)
    else setDraft(null)
  }
  const shown = draft ?? value
  return (
    <div className="flex items-center gap-3">
      <input
        type="range"
        className="vo-range min-w-0 flex-1"
        aria-label={label}
        min={min}
        max={max}
        step={step}
        value={shown}
        disabled={disabled}
        style={{ ['--fill' as string]: `${((shown - min) / (max - min || 1)) * 100}%` }}
        onChange={(e) => setDraft(Number(e.target.value))}
        onPointerUp={commit}
        onKeyUp={commit}
        onBlur={() => draft != null && commit()}
      />
      <span className="num w-[52px] flex-none text-right font-semibold">{format(shown)}</span>
    </div>
  )
}

/** A small number input that applies on Enter or when it loses focus, and shows the printer's value otherwise. */
export function NumberInput({
  value,
  onCommit,
  digits = 2,
  disabled,
  label,
  readOnly,
  className = '',
}: {
  value: number
  onCommit: (v: number) => void
  digits?: number
  disabled?: boolean
  label: string
  readOnly?: boolean
  className?: string
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const commit = () => {
    if (draft == null) return
    const v = Number(draft)
    setDraft(null)
    if (draft.trim() !== '' && Number.isFinite(v) && v !== Number(value.toFixed(digits))) onCommit(v)
  }
  return (
    <input
      className={`field num text-right font-semibold ${className}`}
      inputMode="decimal"
      aria-label={label}
      disabled={disabled}
      readOnly={readOnly}
      value={draft ?? value.toFixed(digits)}
      onFocus={(e) => !readOnly && (setDraft(value.toFixed(digits)), e.target.select())}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
        if (e.key === 'Escape') (setDraft(null), (e.target as HTMLInputElement).blur())
      }}
    />
  )
}

/**
 * A Klipper object's name in sentence case: "temperature_sensor Chamber_Thermal_Protection_Sensor" reads "Chamber
 * thermal protection sensor", and all-caps words such as MCU stay as they are.
 */
export function objectLabel(obj: string) {
  const name = obj.includes(' ') ? obj.slice(obj.indexOf(' ') + 1) : obj
  return name
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((w, i) => (w.length > 1 && w === w.toUpperCase() ? w : i === 0 ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w.toLowerCase()))
    .join(' ')
}

/** A short note under a control: why it's disabled, or what it does. */
export function Note({ children, tone }: { children: ReactNode; tone?: 'warn' | 'info' }) {
  return <div className={`text-[12.5px] ${tone === 'warn' ? 'text-warn' : tone === 'info' ? 'text-info' : 'text-ink-3'}`}>{children}</div>
}

// A color input that sends only the final color: React's onChange fires on every move inside the picker, the native
// change event once it's picked.
export function ColorInput({ value, onPick, label }: { value: string; onPick: (hex: string) => void; label: string }) {
  const ref = useRef<HTMLInputElement>(null)
  const pick = useRef(onPick)
  pick.current = onPick
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onChange = () => pick.current(el.value)
    el.addEventListener('change', onChange)
    return () => el.removeEventListener('change', onChange)
  }, [])
  useEffect(() => {
    if (ref.current) ref.current.value = value
  }, [value])
  return <input ref={ref} type="color" className="absolute inset-0 cursor-pointer opacity-0" defaultValue={value} aria-label={label} />
}

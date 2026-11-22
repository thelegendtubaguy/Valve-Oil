import { useState } from 'react'
import { toast } from '../toast'
import { targetError, type TargetRange } from './heaters'
import { t } from '../../i18n'

/**
 * − target + for a heater. 0 is off and always allowed; other targets stay within the heater's range, so − from the
 * lowest target turns the heater off, and + from off starts at a usual target for that heater.
 */
export function TargetStepper({
  target,
  range,
  onSet,
  step = 5,
  start = 40,
  label,
  size = 'md',
  disabled,
}: {
  target: number
  range: TargetRange
  onSet: (t: number) => void
  step?: number
  /** Where + from off starts. */
  start?: number
  label: string
  size?: 'md' | 'lg'
  disabled?: boolean
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const cur = Math.round(target)
  const lowest = Math.max(range.min, 1)
  const down = cur <= lowest ? 0 : Math.max(lowest, cur - step)
  const up = cur === 0 ? Math.min(range.max, Math.max(lowest, start)) : Math.min(range.max, cur + step)
  const locked = disabled || range.locked || range.max <= 0
  const set = (v: number) => {
    const err = targetError(v, range)
    if (err) return toast(err, 'error')
    onSet(v)
  }
  const commit = () => {
    if (draft == null) return
    const v = Number(draft)
    setDraft(null)
    if (draft.trim() === '' || v === cur) return
    set(Math.round(v))
  }
  const big = size === 'lg'
  return (
    <div className="flex items-center gap-1.5">
      <button type="button" className={`pm ${big ? 'h-11! w-11! text-[20px]' : ''}`} disabled={disabled || cur === 0} onClick={() => set(down)} aria-label={t('common.lower_named', { name: label })}>
        −
      </button>
      <div className="relative min-w-0 flex-1">
        <input
          className={`field num text-center font-bold ${big ? 'h-11 text-[20px]' : 'h-[34px] text-[16px]'} ${cur > 0 ? 'text-accent-ink' : ''}`}
          inputMode="numeric"
          aria-label={t('control.target_stepper.target', { name: label })}
          disabled={disabled || (range.locked && cur === 0)}
          value={draft ?? (cur > 0 ? String(cur) : t('common.off'))}
          onFocus={(e) => (setDraft(String(cur)), e.target.select())}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
            if (e.key === 'Escape') (setDraft(null), (e.target as HTMLInputElement).blur())
          }}
        />
        {cur > 0 && draft == null && <span className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-[12px] font-semibold text-ink-3">°C</span>}
      </div>
      <button type="button" className={`pm ${big ? 'h-11! w-11! text-[20px]' : ''}`} disabled={locked || cur >= range.max} onClick={() => set(up)} aria-label={t('common.raise_named', { name: label })}>
        +
      </button>
    </div>
  )
}

/** Where + starts a heater that's off: a usual first target for each. */
export function startTarget(obj: string) {
  return obj === 'extruder' ? 200 : obj === 'heater_bed' ? 60 : obj.includes('heater_box') ? 45 : 40
}

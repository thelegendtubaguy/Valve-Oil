import { useEffect, useState, type ReactNode } from 'react'
import { LoaderCircle, Plus, X } from 'lucide-react'
import { Card, Spinner, Toggle } from '../ui'
import { useUiSetting, type UI_DEFAULTS, type UiSettingPath } from './fluidd'
import { locale, t } from '../../i18n'

// Building blocks for the Settings sections and the Updates screen: a card of rows, each a title with an optional
// explanation on the left and its control on the right, stacking on phones.

export function SettingsCard({ title, icon, aside, children, footer }: { title: ReactNode; icon?: ReactNode; aside?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <Card title={title} icon={icon} aside={aside}>
      <div className="grid divide-y divide-line">{children}</div>
      {footer && <div className="mt-3 border-t border-line pt-2.5 text-[12px] text-ink-3">{footer}</div>}
    </Card>
  )
}

/** One setting. `wide` gives the control a fixed width beside the text, for fields and steppers. */
export function Row({ title, sub, children, wide, id }: { title: ReactNode; sub?: ReactNode; children?: ReactNode; wide?: boolean; id?: string }) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3 first:pt-0 last:pb-0" id={id}>
      <div className="min-w-[min(220px,100%)] flex-1">
        <div className="font-medium">{title}</div>
        {sub && <div className="mt-0.5 text-[12.5px] text-ink-3">{sub}</div>}
      </div>
      {children != null && <div className={wide ? 'w-full sm:w-[280px] sm:flex-none' : 'ml-auto flex min-w-0 max-w-full flex-wrap items-center justify-end gap-2'}>{children}</div>}
    </div>
  )
}

export function ToggleRow({ title, sub, checked, onChange, disabled }: { title: ReactNode; sub?: ReactNode; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <Row title={title} sub={sub}>
      <Toggle checked={checked} onChange={onChange} disabled={disabled} label={typeof title === 'string' ? title : undefined} />
    </Row>
  )
}

type UiSwitchPath = { [P in UiSettingPath]: (typeof UI_DEFAULTS)[P] extends boolean ? P : never }[UiSettingPath]

/** A switch for one of Fluidd's settings, read and saved by its path. */
export function UiToggleRow({ path, title, sub }: { path: UiSwitchPath; title: string; sub?: string }) {
  const [checked, set] = useUiSetting(path)
  return <ToggleRow title={title} sub={sub} checked={checked} onChange={set} />
}

/** A switch whose change the printer is still saving: it shows a spinner, and `checked` is the value asked for. */
export function PendingToggleRow({ title, sub, checked, pending, disabled, onChange }: { title: string; sub: string; checked: boolean; pending: boolean; disabled: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-3 py-1">
      <div className="min-w-0 flex-1">
        <div className="font-medium">{title}</div>
        <div className="text-[12.5px] text-ink-3">{sub}</div>
      </div>
      {pending && <LoaderCircle size={15} className="spin flex-none text-ink-3" />}
      <Toggle checked={checked} disabled={disabled} label={title} onChange={onChange} />
    </label>
  )
}

/** A card with only a line saying why there's nothing to show yet, with a spinner while it's on its way. */
export function WaitingCard({ title, icon, waiting, children }: { title: string; icon: ReactNode; waiting: boolean; children: ReactNode }) {
  return (
    <Card title={title} icon={icon}>
      <div className="flex items-center gap-2.5 text-ink-2">
        {waiting && <Spinner size={14} />}
        {children}
      </div>
    </Card>
  )
}

/** A text field that saves when it loses focus or Enter is pressed. Escape puts it back. */
export function TextField({ value, onSave, placeholder, required, label, mono }: { value: string; onSave: (v: string) => void; placeholder?: string; required?: boolean; label?: string; mono?: boolean }) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])
  const commit = () => {
    const v = draft.trim()
    if (v === value || (required && !v)) return setDraft(value)
    onSave(v)
  }
  return (
    <input
      className={`field ${mono ? 'code' : ''}`}
      aria-label={label}
      value={draft}
      placeholder={placeholder}
      spellCheck={false}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
        if (e.key === 'Escape') (setDraft(value), (e.target as HTMLInputElement).blur())
      }}
    />
  )
}

/**
 * A short list of numbers, such as jog distances: each a chip that can be removed, and a field to add one. Kept sorted
 * and without repeats, between `min` and `max` entries long.
 */
export function NumberList({ value, onChange, min = 1, max = 6, unit, label }: { value: number[]; onChange: (v: number[]) => void; min?: number; max?: number; unit?: string; label: string }) {
  const parse = (draft: string) => (draft.trim() !== '' && Number.isFinite(Number(draft)) && Number(draft) > 0 ? Number(draft) : null)
  return <ChipList kind="num" value={value} onChange={onChange} parse={parse} min={min} max={max} unit={unit} placeholder={t('settings.common.add_value')} label={t('settings.common.add_to', { label })} />
}

/**
 * Chips that can each be removed, and a field to add one: numbers (kept sorted, `min` to `max` of them) or codes such as
 * file extensions. `parse` turns the field's text into the entry it adds, or null.
 */
export function ChipList<T extends string | number>({ kind, value, onChange, parse, min = 0, max = Infinity, unit, placeholder, label }: { kind: 'num' | 'code'; value: T[]; onChange: (v: T[]) => void; parse: (draft: string) => T | null; min?: number; max?: number; unit?: string; placeholder: string; label: string }) {
  const [draft, setDraft] = useState('')
  const next = parse(draft)
  const valid = next != null && !value.includes(next)
  const add = () => {
    if (!valid || value.length >= max) return
    onChange(kind === 'num' ? [...value, next].sort((a, b) => Number(a) - Number(b)) : [...value, next])
    setDraft('')
  }
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap gap-1.5">
        {value.map((v) => (
          <span key={v} className={`chip ${kind} pr-1!`}>
            {v}
            {unit && <span className="text-ink-3">{unit}</span>}
            <button type="button" className={`iconbtn ${kind === 'num' ? 'h-5! w-5!' : 'h-6! w-6!'}`} disabled={value.length <= min} onClick={() => onChange(value.filter((x) => x !== v))} aria-label={t('settings.common.remove_named', { name: v })}>
              <X size={12} />
            </button>
          </span>
        ))}
      </div>
      {value.length < max && (
        <div className="flex gap-1.5">
          <input className={`field ${kind} h-[30px] py-1`} inputMode={kind === 'num' ? 'decimal' : undefined} placeholder={placeholder} aria-label={label} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} />
          <button type="button" className="btn btn-sm" disabled={!valid} onClick={add}>
            <Plus size={13} /> {t('common.add')}
          </button>
        </div>
      )}
    </div>
  )
}

/** A label and a value on one line, inside a panel. */
export function KV({ k, children }: { k: ReactNode; children: ReactNode }) {
  return (
    <div className="flex min-h-[28px] flex-wrap items-center justify-between gap-x-4 py-0.5">
      <span className="text-ink-2">{k}</span>
      <span className="min-w-0 text-right [overflow-wrap:anywhere]">{children}</span>
    </div>
  )
}

export const fmtDateTime = (ms: number) => new Date(ms).toLocaleString(locale(), { dateStyle: 'medium', timeStyle: 'short' })
export const fmtTime = (ms: number) => new Date(ms).toLocaleTimeString(locale(), { timeStyle: 'short' })

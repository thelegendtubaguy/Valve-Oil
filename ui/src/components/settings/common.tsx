import { useEffect, useState, type ReactNode } from 'react'
import { CircleAlert, CircleCheck, Info, Plus, TriangleAlert, X } from 'lucide-react'
import { Card, Toggle } from '../ui'
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
      {children != null && <div className={wide ? 'w-full sm:w-[280px] sm:flex-none' : 'ml-auto flex flex-none flex-wrap items-center justify-end gap-2'}>{children}</div>}
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
  const [draft, setDraft] = useState('')
  const n = Number(draft)
  const valid = draft.trim() !== '' && Number.isFinite(n) && n > 0 && !value.includes(n)
  const add = () => {
    if (!valid || value.length >= max) return
    onChange([...value, n].sort((a, b) => a - b))
    setDraft('')
  }
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap gap-1.5">
        {value.map((v) => (
          <span key={v} className="chip num pr-1!">
            {v}
            {unit && <span className="text-ink-3">{unit}</span>}
            <button type="button" className="iconbtn h-5! w-5!" disabled={value.length <= min} onClick={() => onChange(value.filter((x) => x !== v))} aria-label={t('settings.common.remove_named', { name: v })}>
              <X size={12} />
            </button>
          </span>
        ))}
      </div>
      {value.length < max && (
        <div className="flex gap-1.5">
          <input
            className="field num h-[30px] py-1"
            inputMode="decimal"
            placeholder={t('settings.common.add_value')}
            aria-label={t('settings.common.add_to', { label })}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && add()}
          />
          <button type="button" className="btn btn-sm" disabled={!valid} onClick={add}>
            <Plus size={13} /> {t('common.add')}
          </button>
        </div>
      )}
    </div>
  )
}

export function Notice({ tone, icon, onClose, children, className = '' }: { tone: 'info' | 'warn' | 'bad' | 'good'; icon?: ReactNode; onClose?: () => void; children: ReactNode; className?: string }) {
  const cls = { info: 'tone-accent', warn: 'tone-warn', bad: 'tone-bad', good: 'tone-good' }[tone]
  const Icon = { info: Info, warn: TriangleAlert, bad: CircleAlert, good: CircleCheck }[tone]
  return (
    <div className={`${cls} flex items-start gap-2.5 rounded-lg px-3 py-2 ${className}`}>
      <span className="mt-0.5 flex-none">{icon ?? <Icon size={15} />}</span>
      <div className="min-w-0 flex-1 text-ink [overflow-wrap:anywhere]">{children}</div>
      {onClose && (
        <button className="iconbtn -my-1.5 -mr-2 h-7 w-7 flex-none" onClick={onClose} aria-label={t('common.dismiss')}>
          <X size={15} />
        </button>
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

// Material, brand, and filament as pills, and colors as swatches, as on the touchscreen's slot screen. Used by the
// filament editor and the spool tag writer.
import { useMemo, useRef, useState, type ReactNode } from 'react'
import { Plus } from 'lucide-react'
import { SectionLabel } from '../ui'
import type { QidiFilament } from '../../state/box'
import { t } from '../../i18n'

// Three rows of common filament colors, light to dark within each family.
export const FILAMENT_PALETTE = [
  '#FFFFFF', '#F2EFE8', '#C8CCD2', '#8A8F98', '#5A6068', '#33373D', '#000000', '#8B5A3C', '#C9A27A', '#E8D9B5', '#F4E9D0', '#D4A82A', '#B87333',
  '#E5484D', '#FF7A1A', '#F5C542', '#C7D93A', '#30A46C', '#1E7F5C', '#2EC4B6', '#4CC9F0', '#2F6BFF', '#2E3A8C', '#7B5CE5', '#B24BD6', '#E46FA8',
  '#8B1E2B', '#F2A1A1', '#FFD6A5', '#FFF3B0', '#CDEAC0', '#A0E7D0', '#BDE0FE', '#A2B9FF', '#C9B6FF', '#E7C6FF', '#FFC8DD', '#556B2F',
]

const shortName = (f: QidiFilament) => (f.vendor && f.name.startsWith(`${f.vendor} `) ? f.name.slice(f.vendor.length + 1) : f.name)

/** The most common values first, then the rest alphabetically. */
function byCount(values: string[]) {
  const counts = new Map<string, number>()
  for (const v of values) if (v) counts.set(v, (counts.get(v) ?? 0) + 1)
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([v]) => v)
}

/** A row of pills that shows the first few, the picked one, and More for the rest. */
export function PillRow({ label, options, value, onChange, show = 8, disabled, render }: { label: ReactNode; options: string[]; value: string | null; onChange: (v: string) => void; show?: number; disabled?: boolean; render?: (v: string) => ReactNode }) {
  const [more, setMore] = useState(false)
  const shown = more || options.length <= show + 1 ? options : [...options.slice(0, show), ...(value && options.indexOf(value) >= show ? [value] : [])]
  return (
    <div className="grid gap-2">
      <SectionLabel>{label}</SectionLabel>
      <div className="flex flex-wrap gap-1.5">
        {shown.map((o) => (
          <button key={o} type="button" disabled={disabled} className={`choice ${o === value ? 'on' : ''}`} aria-pressed={o === value} onClick={() => onChange(o)}>
            {render ? render(o) : o}
          </button>
        ))}
        {options.length > shown.length && (
          <button type="button" className="choice" disabled={disabled} onClick={() => setMore(true)}>
            {t('box.picker.more')}
          </button>
        )}
        {more && options.length > show + 1 && (
          <button type="button" className="choice text-ink-3!" onClick={() => setMore(false)}>
            {t('box.picker.fewer')}
          </button>
        )}
      </div>
    </div>
  )
}

/**
 * Picks a filament from 01.01.07.01's filament database: material, then brand, then (when the brand makes several
 * of that material) the filament. Changing the material keeps the brand when it makes that material.
 */
export function FilamentPicker({ database, value, onChange, disabled }: { database: readonly QidiFilament[]; value: string | null; onChange: (id: string) => void; disabled?: boolean }) {
  const current = database.find((f) => f.id === value) ?? null
  const types = useMemo(() => byCount(database.map((f) => f.type)), [database])
  const [type, setType] = useState(current?.type ?? types[0] ?? '')
  const shownType = current?.type ?? type
  const vendors = useMemo(() => {
    const all = byCount(database.filter((f) => f.type === shownType).map((f) => f.vendor))
    // QIDI's own filaments first, as the touchscreen lists them.
    return all.includes('QIDI') ? ['QIDI', ...all.filter((v) => v !== 'QIDI')] : all
  }, [database, shownType])
  const vendor = current?.vendor ?? null
  const filaments = database.filter((f) => f.type === shownType && f.vendor === vendor)

  const pickType = (t: string) => {
    setType(t)
    const same = database.filter((f) => f.type === t)
    const next = same.find((f) => f.vendor === vendor) ?? same.find((f) => f.vendor === 'QIDI') ?? same.find((f) => f.vendor === 'Generic') ?? same[0]
    if (next) onChange(next.id)
  }
  const pickVendor = (v: string) => {
    const next = database.find((f) => f.type === shownType && f.vendor === v)
    if (next) onChange(next.id)
  }

  return (
    <div className="grid gap-3.5">
      <PillRow label={t('box.picker.material')} options={types} value={shownType} onChange={pickType} disabled={disabled} />
      <PillRow label={t('box.picker.brand')} options={vendors} value={vendor} onChange={pickVendor} show={6} disabled={disabled} />
      {filaments.length > 1 && (
        <PillRow
          label={t('box.picker.filament')}
          options={filaments.map((f) => f.id)}
          value={current?.id ?? null}
          onChange={onChange}
          disabled={disabled}
          render={(id) => shortName(database.find((f) => f.id === id)!)}
        />
      )}
    </div>
  )
}

/** Color swatches with a custom color behind +. Without onCustom, only the palette can be picked. */
export function ColorSwatches({ value, onChange, palette = FILAMENT_PALETTE, custom = true, disabled, size = 30 }: { value: string | null; onChange: (c: string) => void; palette?: string[]; custom?: boolean; disabled?: boolean; size?: number }) {
  const input = useRef<HTMLInputElement>(null)
  const picked = value?.toUpperCase() ?? null
  const isCustom = custom && picked != null && !palette.includes(picked)
  const ring = (on: boolean) => (on ? '0 0 0 2px var(--surface), 0 0 0 4px var(--accent)' : 'inset 0 0 0 1px rgb(128 128 128 / 0.45)')
  return (
    <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={t('box.picker.color')}>
      {palette.map((c) => (
        <button
          key={c}
          type="button"
          role="radio"
          aria-checked={picked === c}
          aria-label={c}
          title={c}
          disabled={disabled}
          onClick={() => onChange(c)}
          className="rounded-full border-0 p-0 disabled:opacity-45"
          style={{ width: size, height: size, background: c, boxShadow: ring(picked === c) }}
        />
      ))}
      {custom && (
        <span className="relative">
          <button
            type="button"
            disabled={disabled}
            onClick={() => input.current?.click()}
            className="grid place-items-center rounded-full border border-dashed border-line-strong p-0 text-ink-2 disabled:opacity-45"
            style={{ width: size, height: size, background: isCustom ? picked! : 'transparent', boxShadow: isCustom ? ring(true) : undefined }}
            aria-label={t('box.picker.custom_color')}
            title={t('box.picker.custom_color')}
          >
            {!isCustom && <Plus size={14} />}
          </button>
          <input ref={input} type="color" tabIndex={-1} aria-hidden className="pointer-events-none absolute inset-0 h-full w-full opacity-0" value={picked ?? '#FFFFFF'} onChange={(e) => onChange(e.target.value.toUpperCase())} />
        </span>
      )}
    </div>
  )
}

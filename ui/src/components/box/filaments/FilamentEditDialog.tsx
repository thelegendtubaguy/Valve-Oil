// Adds, duplicates, or edits a filament in QIDI's list. A new filament copies the settings the dialog doesn't show,
// such as QIDI's drying presets, from the filament it duplicates, or else from a filament of the same type.
import { useId, useMemo, useState, type ReactNode } from 'react'
import { Dialog, Toggle } from '../../ui'
import { QIDI_FILAMENT_ID_PATTERN, qidiFilamentBaseKey, qidiFilamentIdOwner, qidiFilamentKey, type QidiFilamentConfig, type QidiFilamentDraft } from '../../../qidi/qidi-filament-config'

export type FilamentSave = { draft: QidiFilamentDraft; previousKey?: string; baseKey?: string }

export function FilamentEditDialog({ config, initial, previousKey, sourceKey, busy, onSave, onClose }: { config: QidiFilamentConfig; initial: QidiFilamentDraft; previousKey?: string; sourceKey?: string; busy: boolean; onSave: (s: FilamentSave) => void; onClose: () => void }) {
  const [d, setD] = useState<QidiFilamentDraft>({ ...initial })
  const set = <K extends keyof QidiFilamentDraft>(k: K, v: QidiFilamentDraft[K]) => setD((x) => ({ ...x, [k]: v }))
  const sorted = (key: string) => [...new Set(Object.values(config).map((e) => String(e[key] ?? '')))].filter(Boolean).sort((a, b) => a.localeCompare(b))
  const vendors = useMemo(() => sorted('filament_vendor'), [config]) // eslint-disable-line react-hooks/exhaustive-deps
  const types = useMemo(() => sorted('type'), [config]) // eslint-disable-line react-hooks/exhaustive-deps
  const key = qidiFilamentKey(d)
  const baseKey = previousKey != null ? undefined : (sourceKey ?? qidiFilamentBaseKey(config, d.type))

  const owner = qidiFilamentIdOwner(config, d.id, previousKey)
  const errors = {
    vendor: !d.vendor.trim() ? 'Required' : null,
    name: !d.name.trim() ? 'Required' : key !== previousKey && key in config ? `${key} is already in the list` : null,
    type: !d.type.trim() ? 'Required' : null,
    id: !d.id ? 'Required' : !QIDI_FILAMENT_ID_PATTERN.test(d.id) ? 'QF, a capital letter, and two digits, such as QFA94' : owner != null ? `Already used by ${owner}` : null,
    nozzleMin: d.nozzleMin == null ? 'Required' : d.nozzleMin <= 0 ? 'Must be above 0' : null,
    nozzleMax: d.nozzleMax == null ? 'Required' : d.nozzleMax < (d.nozzleMin ?? 0) ? `At least ${d.nozzleMin}` : null,
    boxMax: d.boxMax == null ? 'Required' : d.boxMax < 0 ? 'At least 0' : null,
    chamber: d.chamber == null ? 'Required' : d.chamber < 0 ? 'At least 0' : null,
  }
  const valid = Object.values(errors).every((e) => e == null)
  const save = () => valid && !busy && onSave({ draft: { ...d, vendor: d.vendor.trim(), name: d.name.trim(), type: d.type.trim() }, previousKey, baseKey })
  const num = (v: string) => (v.trim() === '' || !Number.isFinite(Number(v)) ? null : Number(v))

  return (
    <Dialog
      open
      onClose={onClose}
      title={previousKey != null ? 'Edit filament' : 'Add filament'}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={!valid || busy} onClick={save}>
            Save
          </button>
        </>
      }
    >
      <form className="grid grid-cols-2 gap-x-3 gap-y-3" onSubmit={(e) => (e.preventDefault(), save())}>
        <Field label="Vendor" error={errors.vendor} className="col-span-2 sm:col-span-1">
          {(id) => <ListInput id={id} value={d.vendor} options={vendors} onChange={(v) => set('vendor', v)} />}
        </Field>
        <Field label="Filament" error={errors.name} className="col-span-2 sm:col-span-1">
          {(id) => <input id={id} className="field" spellCheck={false} value={d.name} onChange={(e) => set('name', e.target.value)} />}
        </Field>
        <Field label="Type" error={errors.type} className="col-span-2 sm:col-span-1">
          {(id) => <ListInput id={id} value={d.type} options={types} onChange={(v) => set('type', v)} />}
        </Field>
        <Field label="ID" error={errors.id} hint={previousKey != null ? 'Slots refer to the filament by its ID' : undefined} className="col-span-2 sm:col-span-1">
          {(id) => <input id={id} className="field code" spellCheck={false} disabled={previousKey != null} value={d.id} onChange={(e) => set('id', e.target.value.toUpperCase())} />}
        </Field>
        <Field label="Nozzle min" error={errors.nozzleMin}>
          {(id) => <Degrees id={id} value={d.nozzleMin} onChange={(v) => set('nozzleMin', num(v))} />}
        </Field>
        <Field label="Nozzle max" error={errors.nozzleMax}>
          {(id) => <Degrees id={id} value={d.nozzleMax} onChange={(v) => set('nozzleMax', num(v))} />}
        </Field>
        <Field label="Box max" error={errors.boxMax} hint="The Box won't heat above this with the filament loaded; 0 means no heating">
          {(id) => <Degrees id={id} value={d.boxMax} onChange={(v) => set('boxMax', num(v))} />}
        </Field>
        <Field label="Chamber" error={errors.chamber}>
          {(id) => <Degrees id={id} value={d.chamber} onChange={(v) => set('chamber', num(v))} />}
        </Field>
        <label className="col-span-2 flex items-center gap-3">
          <Toggle checked={d.support} onChange={(v) => set('support', v)} label="Support material" />
          <span className="font-medium">Support material</span>
        </label>
        <button type="submit" hidden />
      </form>
      <div className="mt-4 text-[12.5px] text-ink-3">
        Listed as {key}.{baseKey != null && ` Drying and other settings are copied from ${baseKey}.`}
      </div>
    </Dialog>
  )
}

function Field({ label, error, hint, className = '', children }: { label: string; error: string | null; hint?: string; className?: string; children: (id: string) => ReactNode }) {
  const id = useId()
  return (
    <div className={`grid min-w-0 content-start gap-1 ${className}`}>
      <label htmlFor={id} className="lbl">
        {label}
      </label>
      {children(id)}
      {(error || hint) && <span className={`text-[11.5px] ${error ? 'text-bad' : 'text-ink-3'}`}>{error ?? hint}</span>}
    </div>
  )
}

/** A text field that suggests the list's existing values, as Fluidd's combobox does. */
function ListInput({ id, value, options, onChange }: { id: string; value: string; options: string[]; onChange: (v: string) => void }) {
  const list = `${id}-list`
  return (
    <>
      <input id={id} className="field" list={list} spellCheck={false} value={value} onChange={(e) => onChange(e.target.value)} />
      <datalist id={list}>
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </>
  )
}

function Degrees({ id, value, onChange }: { id: string; value: number | null; onChange: (v: string) => void }) {
  return (
    <div className="relative">
      <input id={id} className="field num pr-9" type="number" inputMode="numeric" value={value ?? ''} onChange={(e) => onChange(e.target.value)} />
      <span className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-[12px] font-semibold text-ink-3">°C</span>
    </div>
  )
}

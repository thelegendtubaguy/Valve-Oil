import { useEffect, useRef, useState } from 'react'
import { Check, Filter, Pencil, Plus, Trash2 } from 'lucide-react'
import { Dialog, Toggle, confirm } from '../ui'
import { attempt } from '../toast'
import { validExpression, type ConsoleFilter, type ConsoleFilterType } from './model'
import { useConsoleSettings } from './store'

// Fluidd's console filters: "Hide temp waits" and named filters that hide matching lines, stored in its namespace
// (`uiSettings.general.hideTempWaits`, `console.consoleFilters`), so a filter set up in either UI works in both.

function newId() {
  // crypto.randomUUID needs a secure context, which a printer on plain http isn't.
  const b = crypto.getRandomValues(new Uint8Array(16))
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

/** The toolbar's filter button: Hide temp waits and each saved filter, on or off. */
export function FilterMenu({ onManage }: { onManage: () => void }) {
  const s = useConsoleSettings()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: Event) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', esc)
    return () => (window.removeEventListener('pointerdown', close), window.removeEventListener('keydown', esc))
  }, [open])
  const active = (s.hideTempWaits ? 1 : 0) + s.filters.filter((f) => f.enabled).length
  const toggle = (f: ConsoleFilter) => attempt(() => s.setFilters(s.filters.map((x) => (x.id === f.id ? { ...x, enabled: !x.enabled } : x))))
  const Row = ({ on, label, onClick }: { on: boolean; label: string; onClick: () => void }) => (
    <button type="button" role="menuitemcheckbox" aria-checked={on} onClick={onClick}>
      <span className={`grid h-4 w-4 flex-none place-items-center rounded ${on ? 'bg-accent text-on-accent' : 'shadow-[inset_0_0_0_1.5px_var(--line-strong)]'}`}>{on && <Check size={12} strokeWidth={3} />}</span>
      <span className="truncate">{label}</span>
    </button>
  )
  return (
    <div className="relative inline-flex" ref={ref}>
      <button type="button" className={`btn btn-sm ${active ? 'text-accent-ink' : ''}`} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)} title="Filter">
        <Filter size={14} />
        <span className="max-sm:hidden">Filter</span>
        {active > 0 && <span className="num text-[11px]">{active}</span>}
      </button>
      {open && (
        <div className="menu top-[calc(100%+4px)] right-0 max-w-[min(320px,calc(100vw-24px))]" role="menu">
          <Row on={s.hideTempWaits} label="Hide temp waits" onClick={() => attempt(() => s.setHideTempWaits(!s.hideTempWaits))} />
          {s.filters.length > 0 && <div className="sep my-1" />}
          {s.filters.map((f) => (
            <Row key={f.id} on={f.enabled} label={f.name || f.value} onClick={() => toggle(f)} />
          ))}
          <div className="sep my-1" />
          <button type="button" onClick={() => (setOpen(false), onManage())}>
            <Pencil size={14} /> Manage filters…
          </button>
        </div>
      )}
    </div>
  )
}

const TYPES: { value: ConsoleFilterType; label: string }[] = [
  { value: 'contains', label: 'Contains' },
  { value: 'starts-with', label: 'Starts with' },
  { value: 'expression', label: 'Expression' },
]

/** Add, edit, and remove console filters, as in Fluidd's console settings. */
export function FiltersDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const s = useConsoleSettings()
  const [edit, setEdit] = useState<ConsoleFilter | null>(null)
  useEffect(() => {
    if (!open) setEdit(null)
  }, [open])

  const remove = async (f: ConsoleFilter) => {
    if (await confirm({ title: 'Remove filter', body: `Are you sure you want to remove the console filter ${f.name}?`, confirm: 'Remove', danger: true }))
      attempt(() => s.setFilters(s.filters.filter((x) => x.id !== f.id)))
  }
  const save = (f: ConsoleFilter) =>
    attempt(async () => {
      await s.setFilters(f.id ? s.filters.map((x) => (x.id === f.id ? f : x)) : [...s.filters, { ...f, id: newId() }])
      setEdit(null)
    })

  if (edit) return <FilterEditor filter={edit} others={s.filters} onCancel={() => setEdit(null)} onSave={save} />
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Console filters"
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
          <button className="btn btn-primary" onClick={() => setEdit({ id: '', name: '', type: 'contains', value: '', enabled: true })}>
            <Plus size={15} /> Add filter
          </button>
        </>
      }
    >
      <p className="mt-0 mb-3 text-ink-2">Lines a filter matches are hidden from the console. Fluidd uses the same filters.</p>
      {s.filters.length === 0 ? (
        <div className="rounded-lg border border-dashed border-line-strong p-4 text-center text-ink-3">No filters yet.</div>
      ) : (
        <div className="grid gap-1.5">
          {s.filters.map((f) => (
            <div key={f.id} className="flex items-center gap-3 rounded-xl bg-inset px-3 py-2 shadow-[inset_0_0_0_1px_var(--line)]">
              <Toggle checked={f.enabled} onChange={(v) => attempt(() => s.setFilters(s.filters.map((x) => (x.id === f.id ? { ...x, enabled: v } : x))))} label={`Use ${f.name}`} />
              <div className="min-w-0 flex-1">
                <div className="truncate font-semibold">{f.name}</div>
                <div className="truncate text-[12px] text-ink-3">
                  {TYPES.find((t) => t.value === f.type)?.label}: <span className="font-mono">{f.value}</span>
                </div>
              </div>
              <button className="iconbtn" onClick={() => setEdit({ ...f })} aria-label={`Edit ${f.name}`} title="Edit">
                <Pencil size={15} />
              </button>
              <button className="iconbtn" onClick={() => remove(f)} aria-label={`Remove ${f.name}`} title="Remove">
                <Trash2 size={15} />
              </button>
            </div>
          ))}
        </div>
      )}
    </Dialog>
  )
}

function FilterEditor({ filter, others, onCancel, onSave }: { filter: ConsoleFilter; others: ConsoleFilter[]; onCancel: () => void; onSave: (f: ConsoleFilter) => void }) {
  const [f, setF] = useState(filter)
  const nameTaken = others.some((o) => o.id !== f.id && o.name.toLowerCase() === f.name.trim().toLowerCase())
  const badExpr = f.type === 'expression' && f.value !== '' && !validExpression(f.value)
  const ok = f.name.trim() !== '' && f.value !== '' && !nameTaken && !badExpr
  const typeLabel = TYPES.find((t) => t.value === f.type)!.label
  return (
    <Dialog
      open
      onClose={onCancel}
      title={f.id ? 'Edit filter' : 'Add filter'}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={!ok} onClick={() => onSave({ ...f, name: f.name.trim() })}>
            Save
          </button>
        </>
      }
    >
      <div className="grid gap-4">
        <label className="flex items-center justify-between gap-3">
          <span className="font-semibold">Enable</span>
          <Toggle checked={f.enabled} onChange={(enabled) => setF({ ...f, enabled })} label="Enable" />
        </label>
        <label className="grid gap-1.5">
          <span className="lbl">Name</span>
          <input className="field" autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          {nameTaken && <span className="text-[12px] text-bad">A filter with this name already exists.</span>}
        </label>
        <div className="grid gap-1.5">
          <span className="lbl">Type</span>
          <div className="flex flex-wrap gap-1.5">
            {TYPES.map((t) => (
              <button key={t.value} type="button" className={`choice ${f.type === t.value ? 'on' : ''}`} onClick={() => setF({ ...f, type: t.value })}>
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <label className="grid gap-1.5">
          <span className="lbl">{typeLabel}</span>
          <input className="field font-mono" spellCheck={false} autoCapitalize="off" value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} placeholder={f.type === 'expression' ? '^// pressure_advance' : 'pressure_advance'} />
          {badExpr ? (
            <span className="text-[12px] text-bad">This isn’t a valid regular expression.</span>
          ) : (
            <span className="text-[12px] text-ink-3">{f.type === 'expression' ? 'A JavaScript regular expression, case-sensitive.' : 'Not case-sensitive.'}</span>
          )}
        </label>
      </div>
    </Dialog>
  )
}

import { useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Eye, EyeOff, Pencil, Plus, Settings2, Trash2, X } from 'lucide-react'
import { Card, Dialog, Empty, Toggle, confirm, prompt } from '../ui'
import { attempt } from '../toast'
import { ColorInput, send, usePrinting } from './common'
import { gcodeCommandBuilder } from './gcode'
import { addCategory, categoriesOf, listMacros, macroGroups, macroParams, MACROS_KEY, removeCategory, renameCategory, saveMacros, UNCATEGORIZED, type Macro, type MacrosDb } from './macro-settings'
import { usePrinter } from '../../state/printer'
import { FLUIDD, useDbItem } from '../../state/database'

function useMacrosDb() {
  const [db, setDb, loaded] = useDbItem<MacrosDb>(FLUIDD, MACROS_KEY, {})
  const save = (next: MacrosDb) => attempt(() => setDb(next))
  return { db, save, loaded }
}

function useMacros(db: MacrosDb) {
  const objects = usePrinter((s) => s.objects)
  const settings = usePrinter((s) => s.settings)
  return useMemo(() => listMacros(objects, settings, db), [objects, settings, db])
}

/** The Macros tab: Fluidd's macros card, by category, with the same settings as Fluidd. */
export function MacrosTab() {
  const { db, save, loaded } = useMacrosDb()
  const macros = useMacros(db)
  const groups = macroGroups(macros, db)
  const expanded = db.expanded ?? [0]
  const toggle = (i: number) => save({ ...db, expanded: expanded.includes(i) ? expanded.filter((x) => x !== i) : [...expanded, i].sort((a, b) => a - b) })
  if (!loaded) return null
  return (
    <div className="grid grid-cols-1 gap-3.5">
      {groups.length === 0 && <Empty>{macros.length ? 'Every macro is hidden. Use Organize to show some.' : 'This printer has no macros.'}</Empty>}
      {groups.map((g, i) => {
        const open = expanded.includes(i)
        return (
          <Card key={g.id}>
            <button type="button" className="flex w-full items-center gap-2 border-0 bg-transparent p-0 text-left text-ink" onClick={() => toggle(i)} aria-expanded={open}>
              {open ? <ChevronDown size={16} className="text-ink-3" /> : <ChevronRight size={16} className="text-ink-3" />}
              <span className="text-[14px] font-bold">{g.name ?? 'Uncategorized'}</span>
              <span className="chip">{g.macros.length}</span>
            </button>
            {open && (
              <div className="mt-3 flex flex-wrap gap-2">
                {g.macros.map((m) => (
                  <MacroButton key={m.name} macro={m} />
                ))}
              </div>
            )}
          </Card>
        )
      })}
    </div>
  )
}

/** Organize beside the tabs. */
export function MacrosActions() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button className="btn btn-sm" onClick={() => setOpen(true)}>
        <Settings2 size={13} /> Organize
      </button>
      {open && <OrganizeDialog onClose={() => setOpen(false)} />}
    </>
  )
}

function MacroButton({ macro }: { macro: Macro }) {
  const printing = usePrinting()
  const [params, setParams] = useState(false)
  const fields = macroParams(macro)
  const disabled = macro.disabledWhilePrinting && printing
  const style = macro.color ? { boxShadow: `inset 4px 0 0 ${macro.color}, var(--shadow-btn)` } : undefined
  return (
    <div className="inline-flex" title={macro.description}>
      <button className={`btn ${fields.length ? 'rounded-r-none' : ''}`} style={style} disabled={disabled} onClick={() => send(macro.name.toUpperCase())}>
        {macro.alias || macro.name}
      </button>
      {fields.length > 0 && (
        <button className="btn rounded-l-none border-l border-line px-2!" disabled={disabled} onClick={() => setParams(true)} aria-label={`${macro.name} with parameters`} title="Parameters">
          <ChevronDown size={14} />
        </button>
      )}
      {params && <ParamsDialog macro={macro} onClose={() => setParams(false)} />}
    </div>
  )
}

function ParamsDialog({ macro, onClose }: { macro: Macro; onClose: () => void }) {
  const fields = macroParams(macro)
  const [values, setValues] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((f) => [f.name, f.value])))
  const command = gcodeCommandBuilder(macro.name, values)
  const run = () => {
    send(command)
    onClose()
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={macro.alias || macro.name}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={run}>
            Send
          </button>
        </>
      }
    >
      {macro.description && <div className="mb-3 text-ink-2">{macro.description}</div>}
      <form
        className="grid gap-2.5 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault()
          run()
        }}
      >
        {fields.map((f) => (
          <label key={f.name} className="grid gap-1">
            <span className="lbl flex justify-between">
              <span className="code">{f.name}</span>
              {values[f.name] !== f.value && (
                <button type="button" className="border-0 bg-transparent p-0 text-[11px] font-semibold text-accent-ink" onClick={() => setValues((v) => ({ ...v, [f.name]: f.value }))}>
                  Reset
                </button>
              )}
            </span>
            <input className="field code" spellCheck={false} value={values[f.name]} placeholder={f.value} onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))} onFocus={(e) => e.target.select()} />
          </label>
        ))}
        <button type="submit" hidden />
      </form>
      <div className="code mt-3 rounded-lg bg-inset px-3 py-2 text-[12px] break-all text-ink-2">{command}</div>
    </Dialog>
  )
}

/** Fluidd's macro settings: categories, and each macro's visibility, alias, color, category, and print lock. */
function OrganizeDialog({ onClose }: { onClose: () => void }) {
  const { db, save } = useMacrosDb()
  const macros = useMacros(db)
  const categories = categoriesOf(db)
  const [filter, setFilter] = useState('')
  const [category, setCategory] = useState<string>('all')
  const shown = macros.filter((m) => (category === 'all' || m.categoryId === category) && (!filter || `${m.name} ${m.alias} ${m.description ?? ''}`.toLowerCase().includes(filter.toLowerCase())))
  const update = (m: Macro, change: Partial<Macro>) => save(saveMacros(db, [{ ...m, ...change }]))
  const setAll = (visible: boolean) => save(saveMacros(db, shown.map((m) => ({ ...m, visible }))))
  const count = (id: string) => macros.filter((m) => m.categoryId === id).length
  return (
    <Dialog open onClose={onClose} title="Organize macros" size="full">
      <div className="grid gap-4">
        <section className="grid gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="section-label mr-auto">Categories</span>
            <button
              className="btn btn-sm"
              onClick={async () => {
                const name = await prompt({ title: 'Add category', input: { label: 'Name', value: '' }, confirm: 'Add' })
                if (name) save(addCategory(db, name))
              }}
            >
              <Plus size={13} /> Add category
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button className={`choice ${category === 'all' ? 'on' : ''}`} onClick={() => setCategory('all')}>
              All <span className="text-ink-3">{macros.length}</span>
            </button>
            {categories.map((c) => (
              <span key={c.id} className={`choice gap-1! pr-1.5! ${category === c.id ? 'on' : ''}`}>
                <button type="button" className="border-0 bg-transparent p-0 font-[inherit] text-inherit" onClick={() => setCategory(c.id)}>
                  {c.name} <span className="text-ink-3">{count(c.id)}</span>
                </button>
                <button
                  type="button"
                  className="iconbtn h-6 w-6"
                  aria-label={`Rename ${c.name}`}
                  onClick={async () => {
                    const name = await prompt({ title: 'Rename category', input: { label: 'Name', value: c.name }, confirm: 'Rename' })
                    if (name) save(renameCategory(db, c.id, name))
                  }}
                >
                  <Pencil size={12} />
                </button>
                <button
                  type="button"
                  className="iconbtn h-6 w-6"
                  aria-label={`Remove ${c.name}`}
                  onClick={async () => {
                    if (await confirm({ title: `Remove ${c.name}?`, body: 'Its macros move to Uncategorized.', confirm: 'Remove', danger: true })) {
                      if (category === c.id) setCategory('all')
                      save(removeCategory(db, c.id))
                    }
                  }}
                >
                  <Trash2 size={12} />
                </button>
              </span>
            ))}
            <button className={`choice ${category === UNCATEGORIZED ? 'on' : ''}`} onClick={() => setCategory(UNCATEGORIZED)}>
              Uncategorized <span className="text-ink-3">{count(UNCATEGORIZED)}</span>
            </button>
          </div>
        </section>
        <section className="grid gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <input className="field max-w-[280px] flex-1" placeholder="Find a macro" value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Find a macro" />
            <div className="flex-1" />
            <button className="btn btn-sm" disabled={!shown.length} onClick={() => setAll(true)}>
              <Eye size={13} /> Show all
            </button>
            <button className="btn btn-sm" disabled={!shown.length} onClick={() => setAll(false)}>
              <EyeOff size={13} /> Hide all
            </button>
          </div>
          {shown.length === 0 && <Empty>No macros match.</Empty>}
          <div className="grid">
            {shown.map((m) => (
              <MacroSettingsRow key={m.name} macro={m} categories={categories} onChange={(c) => update(m, c)} />
            ))}
          </div>
        </section>
      </div>
    </Dialog>
  )
}

function MacroSettingsRow({ macro, categories, onChange }: { macro: Macro; categories: { id: string; name: string }[]; onChange: (c: Partial<Macro>) => void }) {
  const [alias, setAlias] = useState<string | null>(null)
  const commitAlias = () => {
    if (alias != null && alias !== macro.alias) onChange({ alias: alias.trim() })
    setAlias(null)
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-line py-2.5 last:border-b-0">
      <Toggle label={`Show ${macro.name}`} checked={macro.visible} onChange={(visible) => onChange({ visible })} />
      <div className="min-w-[180px] flex-1">
        <div className={`code font-semibold break-all ${macro.visible ? '' : 'text-ink-3'}`}>
          {macro.name}
        </div>
        {macro.description && <div className="text-[12px] text-ink-3">{macro.description}</div>}
      </div>
      <input
        className="field w-[150px]! py-1!"
        placeholder="Alias"
        aria-label={`Alias for ${macro.name}`}
        value={alias ?? macro.alias}
        onChange={(e) => setAlias(e.target.value)}
        onBlur={commitAlias}
        onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
      />
      <select className="field w-[150px]! py-1!" aria-label={`Category for ${macro.name}`} value={macro.categoryId} onChange={(e) => onChange({ categoryId: e.target.value })}>
        <option value={UNCATEGORIZED}>Uncategorized</option>
        {categories.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </select>
      <span className="inline-flex items-center gap-1">
        <label className="relative h-7 w-7 cursor-pointer overflow-hidden rounded-md shadow-[inset_0_0_0_1px_var(--line-strong)]" style={{ background: macro.color || 'transparent' }} title="Color">
          <ColorInput value={macro.color || '#f2b05a'} onPick={(color) => onChange({ color })} label={`Color for ${macro.name}`} />
        </label>
        {macro.color && (
          <button className="iconbtn h-6 w-6" aria-label="Clear color" onClick={() => onChange({ color: '' })}>
            <X size={12} />
          </button>
        )}
      </span>
      <label className="flex items-center gap-2 text-[12.5px] text-ink-2">
        <Toggle label={`Disable ${macro.name} while printing`} checked={macro.disabledWhilePrinting} onChange={(disabledWhilePrinting) => onChange({ disabledWhilePrinting })} />
        Disabled while printing
      </label>
    </div>
  )
}

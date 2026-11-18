import { gcodeMacroParams, getParamNameForRawGcodeCommand } from './gcode'

// Fluidd's macro settings, read and written in the `fluidd` database namespace under `macros` with Fluidd's shapes
// (src/store/macros in Fluidd), so categories, aliases, colors, and visibility are the same in both UIs.

export type MacroCategory = { id: string; name: string }
export type StoredMacro = { name: string; alias?: string; visible?: boolean; disabledWhilePrinting?: boolean; color?: string; categoryId?: string; order?: number }
export type MacrosDb = { expanded?: number[]; categories?: MacroCategory[]; stored?: StoredMacro[] }

export type Macro = {
  name: string
  alias: string
  visible: boolean
  disabledWhilePrinting: boolean
  color: string
  categoryId: string
  order?: number
  description?: string
  gcode?: string
}

export const MACROS_KEY = 'macros'
export const UNCATEGORIZED = '0'

const DEFAULTS = { alias: '', visible: true, disabledWhilePrinting: false, color: '', categoryId: UNCATEGORIZED, order: undefined }
const STORED_KEYS = ['name', ...Object.keys(DEFAULTS)]

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type Settings = Record<string, Record<string, any>>

/** Categories as Fluidd reads them: very old Fluidd stored plain strings, which it drops. */
export function categoriesOf(db: MacrosDb): MacroCategory[] {
  const c = db.categories ?? []
  return typeof c[0] === 'string' ? [] : c
}

/** Every macro Klipper has that doesn't start with `_`, merged with its stored settings, in Fluidd's order. */
export function listMacros(objects: string[], settings: Settings, db: MacrosDb): Macro[] {
  const categories = categoriesOf(db)
  return objects
    .filter((key) => key.startsWith('gcode_macro '))
    .map((key) => {
      const name = key.split(/\s+/, 2)[1]
      const config = settings[key.toLowerCase()]
      const stored = db.stored?.find((m) => m.name?.toLowerCase() === name.toLowerCase())
      const macro: Macro = { ...DEFAULTS, ...stored, name, description: config && config.description !== 'G-Code macro' ? config.description : undefined, gcode: config?.gcode }
      if (!categories.some((c) => c.id === macro.categoryId)) macro.categoryId = UNCATEGORIZED
      return macro
    })
    .filter((m) => !m.name.startsWith('_'))
    .sort((a, b) => (a.order !== undefined && b.order !== undefined && a.order !== b.order ? a.order - b.order : a.name.toLowerCase().localeCompare(b.name.toLowerCase())))
}

export type MacroGroup = { id: string; name: string | null; macros: Macro[] }

/** Visible macros by category, the uncategorized last, leaving out empty groups: the dashboard's sections. */
export function macroGroups(macros: Macro[], db: MacrosDb): MacroGroup[] {
  return [...categoriesOf(db), { id: UNCATEGORIZED, name: null }]
    .map(({ id, name }) => ({ id, name, macros: macros.filter((m) => m.categoryId === id && m.visible) }))
    .filter((g) => g.macros.length > 0)
}

function sanitize(m: Partial<Macro> & { name: string }): StoredMacro {
  return Object.fromEntries(Object.entries(m).filter(([k]) => STORED_KEYS.includes(k))) as StoredMacro
}

/** The database value with these macros' settings saved. */
export function saveMacros(db: MacrosDb, macros: (Partial<Macro> & { name: string })[]): MacrosDb {
  const stored = [...(db.stored ?? [])]
  for (const macro of macros) {
    const i = stored.findIndex((m) => m.name.toLowerCase() === macro.name.toLowerCase())
    const merged = sanitize({ ...DEFAULTS, ...(i >= 0 ? stored[i] : {}), ...macro, name: i >= 0 ? stored[i].name : macro.name.toLowerCase() })
    if (i < 0) stored.push(merged)
    else stored[i] = merged
  }
  return { ...db, stored }
}

export function addCategory(db: MacrosDb, name: string, id = uuid()): MacrosDb {
  return { ...db, categories: [...categoriesOf(db), { id, name }] }
}

export function renameCategory(db: MacrosDb, id: string, name: string): MacrosDb {
  return { ...db, categories: categoriesOf(db).map((c) => (c.id === id ? { id, name } : c)) }
}

/** Removes a category; its macros go back to uncategorized. */
export function removeCategory(db: MacrosDb, id: string): MacrosDb {
  return {
    ...db,
    categories: categoriesOf(db).filter((c) => c.id !== id),
    stored: (db.stored ?? []).map((m) => {
      if (m.categoryId !== id) return m
      const rest = { ...m }
      delete rest.categoryId
      return rest
    }),
  }
}

/** The parameters a macro reads, with their defaults, as Fluidd's macro button offers them. */
export function macroParams(macro: Pick<Macro, 'name' | 'gcode'>): { name: string; value: string }[] {
  const raw = getParamNameForRawGcodeCommand(macro.name.toUpperCase())
  if (raw) return [{ name: raw, value: '' }]
  if (!macro.gcode) return []
  const out: { name: string; value: string }[] = []
  for (const p of gcodeMacroParams(macro.gcode)) if (!p.name.startsWith('_') && !out.some((o) => o.name === p.name)) out.push(p)
  return out
}

// crypto.randomUUID needs a secure context, and the printer is usually reached over plain http.
function uuid() {
  const b = crypto.getRandomValues(new Uint8Array(16))
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

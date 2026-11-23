// File-system logic shared by the file browser and the editor: what each Moonraker root allows, Fluidd's file filters,
// columns, sorting, and which service a config file restarts. Mirrors the Fluidd fork's src/store/files/getters.ts
// (getRootProperties), FileSystem.vue (filters, headers), and globals.ts (CONFIG_SERVICE_MAP).

import type { FileMeta, HistoryJob } from '../../state/files'
import { t, type MessageKey } from '../../i18n'

export type FsEntry = {
  kind: 'dir' | 'file'
  name: string
  /** Path within the root, without the root: "sub/file.gcode". */
  path: string
  modified: number
  size: number
  permissions: string
  /** Inline metadata from get_directory(extended), for G-code files. */
  meta?: FileMeta
}

export type FilterType = 'print_start_time' | 'hidden_files' | 'klipper_backup_files' | 'rolled_log_files' | 'moonraker_backup_files' | 'moonraker_temporary_upload_files' | 'crowsnest_backup_files'

export type RootProps = { readonly: boolean; accepts: string[]; filterTypes: FilterType[] }

// QIDI's Moonraker prints sliced 3MF (.gcode.3mf) files, so the gcodes root takes .3mf as the fork's does.
export const GCODE_EXTENSIONS = ['.gcode', '.g', '.gc', '.gco', '.ufp', '.nc', '.3mf']

export function rootProps(root: string): RootProps {
  switch (root) {
    case 'gcodes':
      return { readonly: false, accepts: GCODE_EXTENSIONS, filterTypes: ['print_start_time', 'hidden_files', 'moonraker_temporary_upload_files'] }
    case 'config':
      return { readonly: false, accepts: ['.conf', '.cfg', '.md', '.css', '.jpg', '.jpeg', '.png', '.gif'], filterTypes: ['hidden_files', 'klipper_backup_files', 'moonraker_backup_files', 'moonraker_temporary_upload_files', 'crowsnest_backup_files'] }
    case 'logs':
      return { readonly: true, accepts: [], filterTypes: ['hidden_files', 'rolled_log_files'] }
    case 'timelapse':
      return { readonly: false, accepts: [], filterTypes: ['hidden_files', 'moonraker_temporary_upload_files'] }
    default:
      // config_examples, docs, and anything else Moonraker registers are read-only.
      return { readonly: true, accepts: [], filterTypes: ['hidden_files'] }
  }
}

/** A filter's label in the Filters menu. */
export const filterLabel = (f: FilterType) => t(`files.filters.${f}` as MessageKey)

/** Folders and files Fluidd never lists: thumbnails and git internals. */
export function alwaysHidden(e: Pick<FsEntry, 'kind' | 'name'>): boolean {
  if (e.kind === 'dir') return e.name === '.thumbs' || e.name === '.git'
  return e.name.startsWith('.thumbs') || e.name.startsWith('thumbs') || e.name.endsWith('.ignoreme')
}

/** True when one of the active filters hides the entry (FileSystem.vue's `files`). */
export function isFiltered(e: FsEntry, filters: readonly FilterType[]): boolean {
  return filters.some((f) => {
    if (f === 'hidden_files') return /^\.(?!\.$)/.test(e.name)
    if (e.kind !== 'file') return false
    switch (f) {
      case 'moonraker_backup_files':
        return e.name === '.moonraker.conf.bkp'
      case 'moonraker_temporary_upload_files':
        return extOf(e.name) === '.mru'
      case 'klipper_backup_files':
        return /^printer-\d{8}_\d{6}\.cfg$/.test(e.name)
      case 'print_start_time':
        return e.meta?.print_start_time != null
      case 'rolled_log_files':
        return /\.\d{4}-\d{2}-\d{2}(?:_\d{2}-\d{2}-\d{2})?$/.test(e.name) || /\.log\.\d+$/.test(e.name)
      case 'crowsnest_backup_files':
        return /^crowsnest\.conf\.\d{4}-\d{2}-\d{2}-\d{4}$/.test(e.name)
    }
    return false
  })
}

// ---- paths ----

export const joinPath = (...parts: string[]) => parts.filter(Boolean).join('/')
export const parentOf = (path: string) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '')
export const baseName = (path: string) => path.slice(path.lastIndexOf('/') + 1)

/** The extension as Fluidd takes it: from the last dot, so "a.gcode.3mf" is ".3mf". */
export function extOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i > 0 ? name.slice(i).toLowerCase() : ''
}

/** Name and extension, keeping .gcode.3mf whole, for "copy" names and renames. */
export function splitName(name: string): [string, string] {
  const m = /(\.gcode\.3mf|\.[^./]+)$/i.exec(name)
  return m && m.index > 0 ? [name.slice(0, m.index), m[1]] : [name, '']
}

/** A free name for a copy: "part copy.gcode", then "part copy 2.gcode". */
export function copyName(name: string, taken: Iterable<string>): string {
  const used = new Set(taken)
  const [base, ext] = splitName(name)
  for (let n = 1; ; n++) {
    const candidate = `${base} copy${n > 1 ? ` ${n}` : ''}${ext}`
    if (!used.has(candidate)) return candidate
  }
}

/** A file or folder name Moonraker will take: no slashes, not empty, not "." or "..". */
export function validName(name: string): string | null {
  const n = name.trim()
  if (!n) return t('files.name.empty')
  if (n.includes('/') || n.includes('\\')) return t('files.name.slash')
  if (n === '.' || n === '..') return t('files.name.dots')
  return null
}

export const isPrintable = (name: string) => GCODE_EXTENSIONS.includes(extOf(name))
/** The G-code preview reads plain G-code and sliced 3MF archives. */
export const canPreview = (name: string) => /\.(gcode|g|gco|gc)$/i.test(name) || /\.gcode\.3mf$/i.test(name)
export const isImage = (name: string) => ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp'].includes(extOf(name))
export const isVideo = (name: string) => ['.mp4', '.webm', '.mkv', '.mov'].includes(extOf(name))

/** Files a click opens in the editor, as Fluidd's default autoEditExtensions, plus the text files Valve Oil keeps. */
const EDIT_ON_CLICK = ['.cfg', '.conf', '.ini', '.log', '.sh', '.txt', '.json', '.yaml', '.yml', '.md', '.py', '.bak', '.bkp', '.css']
export const editsOnClick = (name: string) => EDIT_ON_CLICK.includes(extOf(name))

/**
 * Files Valve Oil UI never changes. QIDI's config/fluidd.cfg is read-only on the printer; the project's rule is to
 * leave it alone, so it opens read-only and offers no rename, move, or delete, and nothing may be written over it.
 */
export function isLocked(root: string, path: string): boolean {
  return root === 'config' && path.replace(/^\/+/, '') === 'fluidd.cfg'
}

/** True when an entry can't be changed: a read-only root, a read-only file, or a locked file. */
export function isReadonly(root: string, e: Pick<FsEntry, 'path' | 'permissions'>): boolean {
  return rootProps(root).readonly || !e.permissions.includes('w') || isLocked(root, e.path)
}

// ---- config files and the services they belong to (Fluidd's CONFIG_SERVICE_MAP) ----

type ServiceMapItem = { filename?: string; prefix?: string; suffix?: string; service: string; link?: string }
const CONFIG_SERVICE_MAP: ServiceMapItem[] = [
  { filename: 'moonraker.conf', service: 'moonraker', link: 'https://moonraker.readthedocs.io/en/latest/configuration/' },
  { filename: 'webcam.txt', service: 'webcamd' },
  { filename: 'crowsnest.conf', service: 'crowsnest', link: 'https://crowsnest.mainsail.xyz/configuration/sample-config' },
  { filename: 'klipperscreen.conf', service: 'KlipperScreen', link: 'https://klipperscreen.readthedocs.io/en/latest/' },
  { filename: 'sonar.conf', service: 'sonar', link: 'https://github.com/mainsail-crew/sonar' },
  { filename: 'mooncord-webcam.json', service: 'webcamd', link: 'https://github.com/eliteSchwein/mooncord' },
  { prefix: 'mooncord', service: 'MoonCord', link: 'https://github.com/eliteSchwein/mooncord' },
  { filename: 'telegram.conf', service: 'moonraker-telegram-bot', link: 'https://github.com/nlef/moonraker-telegram-bot/wiki/Sample-config' },
  { suffix: '.cfg', service: 'klipper', link: 'https://www.klipper3d.org/Config_Reference.html' },
]

/** The service a config file configures, matched by exact name, then prefix, then suffix. */
export function configService(filename: string): { service: string; link?: string } | null {
  const f = baseName(filename).toLowerCase()
  const item = CONFIG_SERVICE_MAP.find((o) => o.filename?.toLowerCase() === f) ?? CONFIG_SERVICE_MAP.find((o) => o.prefix && f.startsWith(o.prefix.toLowerCase())) ?? CONFIG_SERVICE_MAP.find((o) => o.suffix && f.endsWith(o.suffix))
  return item ? { service: item.service, link: item.link } : null
}

// ---- columns (FileSystem.vue's configurableHeaders, in the same order and with the same keys) ----

export type Column = { value: string; label: string; /** Valve Oil UI's default; Fluidd shows more. */ visible: boolean; numeric?: boolean }

// Labels come from files.columns.<value>, with a dot in the value as `_`, when the columns are asked for.
type ColumnDef = Omit<Column, 'label'>

export const GCODE_COLUMNS: ColumnDef[] = [
  { value: 'history.status', visible: true },
  { value: 'object_height', visible: false, numeric: true },
  { value: 'first_layer_height', visible: false, numeric: true },
  { value: 'layer_height', visible: true, numeric: true },
  { value: 'filament_name', visible: false },
  { value: 'filament_colors', visible: false },
  { value: 'extruder_colors', visible: false },
  { value: 'filament_temps', visible: false },
  { value: 'filament_type', visible: true },
  { value: 'filament_total', visible: true, numeric: true },
  { value: 'filament_change_count', visible: false, numeric: true },
  { value: 'filament_weight_total', visible: false, numeric: true },
  { value: 'filament_weights', visible: false },
  { value: 'mmu_print', visible: false },
  { value: 'referenced_tools', visible: false },
  { value: 'history.filament_used', visible: false, numeric: true },
  { value: 'nozzle_diameter', visible: false, numeric: true },
  { value: 'slicer', visible: true },
  { value: 'slicer_version', visible: false },
  { value: 'printer_vendor', visible: false },
  { value: 'printer_model', visible: false },
  { value: 'printer_variant', visible: false },
  { value: 'profile_version', visible: false },
  { value: 'estimated_time', visible: true, numeric: true },
  { value: 'history.print_duration', visible: false, numeric: true },
  { value: 'history.total_duration', visible: false, numeric: true },
  { value: 'first_layer_bed_temp', visible: false, numeric: true },
  { value: 'first_layer_extr_temp', visible: false, numeric: true },
  { value: 'chamber_temp', visible: false, numeric: true },
  { value: 'file_processors', visible: false },
  { value: 'print_start_time', visible: false, numeric: true },
]
const COMMON_COLUMNS: ColumnDef[] = [
  { value: 'modified', visible: true, numeric: true },
  { value: 'size', visible: true, numeric: true },
]

/** A column's (or a metadata field's) label: "layer_height" -> "Layer height". */
export const columnLabel = (value: string) => t(`files.columns.${value.replace('.', '_')}` as MessageKey)

export const columnsFor = (root: string): Column[] => (root === 'gcodes' ? [...GCODE_COLUMNS, ...COMMON_COLUMNS] : COMMON_COLUMNS).map((c) => ({ ...c, label: columnLabel(c.value) }))

/** Fluidd's table-header key: `${root}_${name}`, with name "jobs" on the Jobs page and "configure" on Configure. */
export const headersKey = (root: string) => `uiSettings.tableHeaders.${root}_${root === 'gcodes' ? 'jobs' : 'configure'}`

export type ConfiguredHeader = { value: string; visible?: boolean }

/**
 * Applies Fluidd's saved header choices (getMergedTableHeaders): a saved list covering every column gives the order
 * too; any other list, such as the single entries Fluidd saves when a column is ticked, gives each column it names its
 * visibility. Unlike Fluidd, a saved list of the right length that names a column this table lacks never drops one.
 */
export function mergeColumns(columns: Column[], saved: ConfiguredHeader[] | null | undefined): Column[] {
  if (!saved || !Array.isArray(saved)) return columns
  const apply = (c: Column, s: ConfiguredHeader | undefined): Column => (s?.visible == null ? c : { ...c, visible: s.visible })
  if (saved.length === columns.length) {
    const out = saved.flatMap((s) => {
      const c = columns.find((x) => x.value === s.value)
      return c ? [apply(c, s)] : []
    })
    if (out.length === columns.length) return out
  }
  return columns.map((c) => apply(c, saved.find((s) => s.value === c.value)))
}

// ---- values and sorting ----

/** A column's raw value for an entry; history.* columns read the file's last job. */
export function columnValue(e: FsEntry, key: string, job?: HistoryJob): unknown {
  if (key === 'modified') return e.modified
  if (key === 'size') return e.kind === 'dir' ? null : e.size
  if (key === 'name') return e.name
  if (key.startsWith('history.')) return job ? (job as Record<string, unknown>)[key.slice(8)] : undefined
  return e.meta?.[key]
}

/** Moonraker reports some multi-filament fields as ';'-separated strings and others as arrays. */
export function asList(v: unknown): string[] {
  if (Array.isArray(v)) return v.map((x) => String(x))
  if (typeof v === 'string') return v.split(';').map((x) => x.trim()).filter(Boolean)
  return []
}

function sortKey(v: unknown): number | string | null {
  if (v == null || v === '') return null
  if (typeof v === 'number') return v
  if (typeof v === 'boolean') return v ? 1 : 0
  if (Array.isArray(v)) return v.join(',').toLowerCase()
  const n = Number(v)
  return typeof v === 'string' && v.trim() !== '' && Number.isFinite(n) ? n : String(v).toLowerCase()
}

// Names sort in the browser's own order, whatever language the UI shows. i18n-ignore
export const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** Folders first, then by the column; empty values last either way, names to break ties. */
export function sortEntries(entries: FsEntry[], by: string, desc: boolean, jobOf?: (e: FsEntry) => HistoryJob | undefined): FsEntry[] {
  const dir = desc ? -1 : 1
  return [...entries].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1
    if (by !== 'name') {
      const va = sortKey(columnValue(a, by, jobOf?.(a)))
      const vb = sortKey(columnValue(b, by, jobOf?.(b)))
      if (va == null && vb != null) return 1
      if (vb == null && va != null) return -1
      if (va != null && vb != null && va !== vb) {
        if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir
        return collator.compare(String(va), String(vb)) * dir
      }
    }
    return collator.compare(a.name, b.name) * (by === 'name' ? dir : 1)
  })
}

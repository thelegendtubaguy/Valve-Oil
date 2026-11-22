// Print history, job queue, and timelapse logic for the History screen. Column keys, defaults, and the stored column
// settings follow Fluidd (src/components/widgets/history/JobHistory.vue, src/store/config/getters.ts), so both UIs
// share `uiSettings.tableHeaders.*` in the `fluidd` database namespace.
import type { FileMeta, HistoryJob } from '../../state/files'
import { fmtDuration } from '../../lib/format'
import { locale, t, type MessageKey, type Messages } from '../../i18n'
import { en } from '../../i18n/en'

export type AuxData = { provider: string; name: string; value: unknown; description: string; units: string | null }
export type Job = Omit<HistoryJob, 'metadata'> & { user?: string; auxiliary_data?: AuxData[]; metadata?: FileMeta | Record<string, never> }

// ---------------------------------------------------------------------------------------------------------------
// Status

export type StatusGroup = 'completed' | 'cancelled' | 'failed' | 'in_progress'
export const STATUS_GROUPS: StatusGroup[] = ['completed', 'cancelled', 'failed', 'in_progress']

export const groupLabel = (g: StatusGroup) => t(`history.status.${g}` as MessageKey)

export function statusGroup(status: string): StatusGroup {
  if (status === 'completed') return 'completed'
  if (status === 'in_progress' || status === 'printing') return 'in_progress'
  if (status === 'cancelled') return 'cancelled'
  return 'failed'
}

/** Pill tone, as Fluidd colors JobHistoryItemStatus: Klipper going away warns, errors are red. */
export function statusTone(status: string): 'good' | 'info' | 'muted' | 'warn' | 'bad' {
  switch (status) {
    case 'completed':
      return 'good'
    case 'in_progress':
    case 'printing':
      return 'info'
    case 'cancelled':
      return 'muted'
    case 'interrupted':
    case 'klippy_shutdown':
    case 'klippy_disconnect':
      return 'warn'
    default:
      return 'bad'
  }
}

// Moonraker's job statuses; any other comes from the printer and is shown as it arrives.
const KNOWN_STATUSES = new Set(['completed', 'cancelled', 'error', 'interrupted', 'klippy_shutdown', 'klippy_disconnect', 'server_exit'])

export function statusLabel(status: string): string {
  if (status === 'in_progress' || status === 'printing') return t('history.status.in_progress')
  if (KNOWN_STATUSES.has(status)) return t(`history.status.${status}` as MessageKey)
  const s = status.replace(/_/g, ' ')
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// ---------------------------------------------------------------------------------------------------------------
// Values

/** Fluidd's getStringArray: a ';'-separated or JSON list of names, without quotes. */
export function stringArray(value: string, separator = ';'): string[] {
  if (value.startsWith('["') && value.endsWith('"]')) {
    try {
      const parsed = JSON.parse(value)
      if (Array.isArray(parsed) && parsed.every((x) => typeof x === 'string')) return parsed
    } catch {
      // fall back to splitting
    }
  }
  return value.split(separator).map((x) => x.replace(/^"|"$/g, ''))
}

function path(obj: unknown, key: string): unknown {
  let v: unknown = obj
  for (const k of key.split('.')) {
    if (v == null || typeof v !== 'object') return undefined
    v = (v as Record<string, unknown>)[k]
  }
  return v
}

/** A column's raw value. The fork's history getter turns filament_name and filament_type into lists. */
export function rawValue(job: Job, key: string): unknown {
  if (key.startsWith('auxiliary_data.')) return job.auxiliary_data?.find((a) => a.name === key.slice(15))?.value
  const v = path(job, key)
  if ((key === 'metadata.filament_name' || key === 'metadata.filament_type') && typeof v === 'string') return stringArray(v)
  return v
}

/** The colors a job's file uses: its filament colors, or QIDI's preset colors for the extruders it uses. */
export function jobColors(meta: Record<string, unknown> | undefined): string[] {
  if (!meta) return []
  const fc = meta.filament_colors
  if (Array.isArray(fc) && fc.length) return fc.filter((c): c is string => typeof c === 'string' && c !== '')
  const preset = meta.preset_colours
  if (!Array.isArray(preset)) return []
  const used = Array.isArray(meta.used_extruders) ? (meta.used_extruders as number[]) : null
  return (used ? used.map((i) => preset[i]) : preset).filter((c): c is string => typeof c === 'string' && c !== '')
}

export const fmtDateTime = (epochSeconds: number) => new Date(epochSeconds * 1000).toLocaleString(locale(), { dateStyle: 'medium', timeStyle: 'short' })

/** A duration; with days, totals over a day read as "11 d 10 h 55 m" rather than "274 h 55 m". */
export function fmtSpan(seconds: number | null | undefined, days = false): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return '—'
  if (!days || seconds < 86400) return fmtDuration(seconds)
  const s = Math.round(seconds)
  const d = Math.floor(s / 86400)
  const h = Math.floor((s % 86400) / 3600)
  const m = Math.floor((s % 3600) / 60)
  return t('history.format.days_hours_minutes', { d, h, mm: String(m).padStart(2, '0') })
}

const trim = (n: number, digits: number) => String(Number(n.toFixed(digits)))

/** Millimetres, as Fluidd's getReadableLengthString: mm, then m, then km when asked. */
export function fmtLength(mm: number | null | undefined, km = false): string {
  if (mm == null || !isFinite(mm)) return '—'
  if (km && Math.abs(mm) >= 1e6) return `${trim(mm / 1e6, 2)} km`
  if (Math.abs(mm) >= 1000) return `${trim(mm / 1000, 2)} m`
  return `${trim(mm, 1)} mm`
}

export function fmtWeight(g: number | null | undefined): string {
  if (g == null || !isFinite(g)) return '—'
  if (Math.abs(g) >= 1000) return `${trim(g / 1000, 2)} kg`
  return `${trim(g, 1)} g`
}

export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${trim(bytes / 1024, 1)} KB`
  if (bytes < 1024 ** 3) return `${trim(bytes / 1024 ** 2, 1)} MB`
  return `${trim(bytes / 1024 ** 3, 2)} GB`
}

const pretty = (s: string) => {
  const text = s.replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

function withUnits(value: unknown, units: string | null | undefined): string {
  if (typeof value === 'number') return units ? `${trim(value, 2)} ${units}` : trim(value, 2)
  if (typeof value === 'string') return units ? `${value} ${units}` : value
  if (value == null) return ''
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

// ---------------------------------------------------------------------------------------------------------------
// Columns

export type ColumnKind = 'status' | 'time' | 'duration' | 'length' | 'weight' | 'weights' | 'mm' | 'temp' | 'temps' | 'size' | 'colors' | 'list' | 'pretty-list' | 'text' | 'aux'
type ColumnName = keyof Messages['history']['columns']
export type Column = { key: string; label: string; kind: ColumnKind; visible?: boolean; name?: ColumnName }

/**
 * A built-in column. Its label is looked up each time it's read, so it's in the language shown; `name` finds its English
 * label for the CSV export's header.
 */
function col(key: string, kind: ColumnKind, visible?: boolean): Column {
  const name = key.replace(/^metadata\./, '') as ColumnName
  return {
    key,
    kind,
    name,
    ...(visible === undefined ? {} : { visible }),
    get label() {
      return t(`history.columns.${name}` as MessageKey)
    },
  }
}

/** Fluidd's configurable history columns, in its order. Valve Oil shows print duration and filament by default. */
export const HISTORY_COLUMNS: Column[] = [
  col('status', 'status'),
  col('start_time', 'time'),
  col('end_time', 'time'),
  col('print_duration', 'duration'),
  col('total_duration', 'duration'),
  col('filament_used', 'length'),
  col('user', 'text', false),
  col('metadata.object_height', 'length', false),
  col('metadata.first_layer_height', 'mm', false),
  col('metadata.layer_height', 'mm', false),
  col('metadata.filament_name', 'list', false),
  col('metadata.filament_colors', 'colors', false),
  col('metadata.extruder_colors', 'colors', false),
  col('metadata.filament_temps', 'temps', false),
  col('metadata.filament_type', 'list', false),
  col('metadata.filament_total', 'length', false),
  col('metadata.filament_change_count', 'text', false),
  col('metadata.filament_weight_total', 'weight', false),
  col('metadata.filament_weights', 'weights', false),
  col('metadata.mmu_print', 'text', false),
  col('metadata.referenced_tools', 'pretty-list', false),
  col('metadata.nozzle_diameter', 'mm', false),
  col('metadata.slicer', 'text', false),
  col('metadata.slicer_version', 'text', false),
  col('metadata.printer_vendor', 'text', false),
  col('metadata.printer_model', 'text', false),
  col('metadata.printer_variant', 'text', false),
  col('metadata.profile_version', 'text', false),
  col('metadata.estimated_time', 'duration', false),
  col('metadata.first_layer_bed_temp', 'temp', false),
  col('metadata.first_layer_extr_temp', 'temp', false),
  col('metadata.chamber_temp', 'temp', false),
  col('metadata.file_processors', 'pretty-list', false),
]
const TAIL_COLUMNS: Column[] = [
  col('metadata.modified', 'time', false),
  col('metadata.size', 'size', false),
]

/** A column's CSV header: in English whatever the language shown, as the export is read by programs. */
export const csvHeader = (c: Pick<Column, 'label' | 'name'>) => (c.name ? en.history.columns[c.name] : c.label)

/** Every history column: Fluidd's, then one per auxiliary data field the loaded jobs carry, then modified and size. */
export function historyColumns(jobs: Job[]): Column[] {
  const aux = new Map<string, string>()
  for (const j of jobs) for (const a of j.auxiliary_data ?? []) aux.set(a.name, a.description)
  return [...HISTORY_COLUMNS, ...[...aux].map(([name, description]): Column => ({ key: `auxiliary_data.${name}`, label: pretty(description), kind: 'aux', visible: false })), ...TAIL_COLUMNS]
}

export type ConfiguredColumn = { value: string; visible?: boolean }

/**
 * Fluidd's getMergedTableHeaders: with as many stored entries as columns, the stored order wins; otherwise each
 * column takes its stored visibility.
 */
export function mergeColumns(columns: Column[], configured: ConfiguredColumn[] | null | undefined): Column[] {
  if (!configured || !Array.isArray(configured)) return columns
  if (columns.length === configured.length) {
    return configured.flatMap((c) => {
      const col = columns.find((x) => x.key === c.value)
      return col ? [{ ...col, ...(c.visible != null ? { visible: c.visible } : {}) }] : []
    })
  }
  return columns.map((col) => {
    const c = configured.find((x) => x.value === col.key)
    return c && c.visible != null ? { ...col, visible: c.visible } : col
  })
}

/** Fluidd's setUpdateHeader: replaces the column's entry, or adds it. */
export function toggleColumn(configured: ConfiguredColumn[] | null | undefined, key: string, visible: boolean): ConfiguredColumn[] {
  const list = Array.isArray(configured) ? [...configured] : []
  const i = list.findIndex((c) => c.value === key)
  if (i >= 0) list[i] = { value: key, visible }
  else list.push({ value: key, visible })
  return list
}

export const isVisible = (c: Column) => c.visible !== false

/** A cell as text (or a list of text); colors come back as the raw color list. */
export function cellText(job: Job, col: Column): string | string[] | null {
  const v = rawValue(job, col.key)
  if (col.kind === 'aux') {
    const aux = job.auxiliary_data?.find((a) => a.name === col.key.slice(15))
    if (Array.isArray(v) && v.length) return v.map((x) => withUnits(x, aux?.units))
    return v == null ? null : withUnits(v, aux?.units)
  }
  if (v == null || v === '') return null
  if (typeof v === 'number') {
    switch (col.kind) {
      case 'time':
        return fmtDateTime(v)
      case 'duration':
        return fmtSpan(v)
      case 'length':
        return fmtLength(v)
      case 'weight':
        return fmtWeight(v)
      case 'mm':
        return `${v} mm`
      case 'temp':
        return `${v} °C`
      case 'size':
        return fmtSize(v)
    }
  }
  if (col.kind === 'mm' && typeof v === 'string') return `${v} mm`
  if (Array.isArray(v)) {
    if (!v.length) return null
    if (col.kind === 'weights') return v.map((x) => (typeof x === 'number' ? fmtWeight(x) : String(x)))
    if (col.kind === 'temps') return v.map((x) => (typeof x === 'number' ? `${x} °C` : String(x)))
    if (col.kind === 'pretty-list') return v.map((x) => (typeof x === 'string' ? pretty(x) : String(x)))
    return v.map((x) => String(x))
  }
  if (col.kind === 'status') return statusLabel(String(v))
  if (typeof v === 'boolean') return v ? t('common.yes') : t('common.no')
  return typeof v === 'object' ? JSON.stringify(v) : String(v)
}

/** Raw values in their base units (seconds, millimetres, grams, °C, bytes), as Fluidd exports them. */
export function exportValue(job: Job, key: string): unknown {
  const v = rawValue(job, key)
  if (typeof v === 'number' && (key === 'start_time' || key === 'end_time' || key === 'metadata.modified')) return new Date(v * 1000)
  return v
}

// ---------------------------------------------------------------------------------------------------------------
// Searching, filtering, sorting

export function filterJobs(jobs: Job[], query: string, group: StatusGroup | 'all'): Job[] {
  const q = query.trim().toLowerCase()
  return jobs.filter((j) => {
    if (group !== 'all' && statusGroup(j.status) !== group) return false
    if (!q) return true
    const m = (j.metadata ?? {}) as Record<string, unknown>
    const hay = [j.filename, statusLabel(j.status), j.user, m.slicer, m.filament_type, m.filament_name].filter((x) => typeof x === 'string').join(' ').toLowerCase()
    return hay.includes(q)
  })
}

function comparable(v: unknown): number | string | null {
  if (v == null || v === '') return null
  if (typeof v === 'number') return v
  if (typeof v === 'boolean') return v ? 1 : 0
  if (Array.isArray(v)) return v.length ? comparable(v[0]) : null
  const n = Number(v)
  return typeof v === 'string' && v.trim() !== '' && isFinite(n) ? n : String(v).toLowerCase()
}

/** Sorts by a column; empty values go last either way. */
export function sortJobs(jobs: Job[], key: string, desc: boolean): Job[] {
  const val = (j: Job) => comparable(key === 'filename' ? j.filename.split('/').pop() : rawValue(j, key))
  return [...jobs].sort((a, b) => {
    const x = val(a)
    const y = val(b)
    if (x == null || y == null) return x == null && y == null ? 0 : x == null ? 1 : -1
    const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), locale(), { numeric: true })
    return desc ? -c : c
  })
}

// ---------------------------------------------------------------------------------------------------------------
// Statistics

export type Totals = { total_jobs: number; total_time: number; total_print_time: number; total_filament_used: number; longest_job: number; longest_print: number }

/** Fluidd's history rollup: the totals plus averages per job. */
export function rollup(t: Totals) {
  return {
    ...t,
    filament_avg: t.total_filament_used ? t.total_filament_used / t.total_jobs : 0,
    print_avg: t.total_print_time ? t.total_print_time / t.total_jobs : 0,
    total_avg: t.total_time ? t.total_time / t.total_jobs : 0,
  }
}

/** How many of the given jobs ended each way, with their print time. */
export function statusBreakdown(jobs: Job[]): { group: StatusGroup; label: string; count: number; printTime: number }[] {
  const out = STATUS_GROUPS.map((g) => ({ group: g, label: groupLabel(g), count: 0, printTime: 0 }))
  for (const j of jobs) {
    const e = out.find((x) => x.group === statusGroup(j.status))!
    e.count++
    e.printTime += j.print_duration || 0
  }
  return out
}

// ---------------------------------------------------------------------------------------------------------------
// Job queue

export type QueuedJob = { filename: string; job_id: string; time_added: number; time_in_queue: number }
export type QueueState = 'ready' | 'loading' | 'starting' | 'paused'

/** Fluidd's JobQueueBrowser totals: filament, weight, and time from each queued file's metadata. */
export function queueTotals(jobs: QueuedJob[], meta: (filename: string) => FileMeta | null | undefined) {
  const t = { filamentLength: 0, filamentWeight: 0, time: 0, withoutFile: 0 }
  for (const j of jobs) {
    const m = meta(j.filename)
    if (!m) {
      t.withoutFile++
      continue
    }
    t.filamentLength += m.filament_total ?? 0
    t.filamentWeight += m.filament_weight_total ?? 0
    t.time += m.estimated_time ?? 0
  }
  return t
}

/** The order after moving one queued job; the queue is reordered by posting it again with reset. */
export function moveJob(jobs: QueuedJob[], from: number, to: number): string[] {
  const list = jobs.map((j) => j.filename)
  if (from < 0 || from >= list.length) return list
  const [f] = list.splice(from, 1)
  list.splice(Math.max(0, Math.min(list.length, to)), 0, f)
  return list
}

/** Fluidd's multiply: every selected file, the given number of times over. */
export function multiplyFilenames(filenames: string[], copies: number): string[] {
  return Array.from({ length: Math.max(0, Math.floor(copies)) }).flatMap(() => filenames)
}

// ---------------------------------------------------------------------------------------------------------------
// Timelapse

export type TimelapseSettings = {
  enabled: boolean
  autorender: boolean
  camera: string
  mode: 'layermacro' | 'hyperlapse'
  hyperlapse_cycle: number
  parkhead: boolean
  parkpos: 'custom' | 'front_left' | 'front_right' | 'center' | 'back_left' | 'back_right' | 'x_only' | 'y_only'
  park_time: number
  park_travel_speed: number
  park_custom_pos_x: number
  park_custom_pos_y: number
  park_custom_pos_dz: number
  fw_retract: boolean
  park_retract_distance: number
  park_retract_speed: number
  park_extrude_distance: number
  park_extrude_speed: number
  stream_delay_compensation: number
  gcode_verbose: boolean
  variable_fps: boolean
  targetlength: number
  variable_fps_min: number
  variable_fps_max: number
  output_framerate: number
  saveframes: boolean
  duplicatelastframe: number
  constant_rate_factor: number
  previewimage: boolean
  time_format_code: string
  pixelformat: string
  extraoutputparams: string
  snapshoturl?: string
  rotation?: number
  flip_x?: boolean
  flip_y?: boolean
  blockedsettings?: string[]
}

/** Fluidd's defaultWritableSettings (src/store/timelapse/state.ts): shown until the settings load, and CRF's reset value. */
export const TIMELAPSE_DEFAULTS: Omit<TimelapseSettings, 'snapshoturl' | 'rotation' | 'flip_x' | 'flip_y' | 'blockedsettings'> = Object.freeze({
  enabled: true,
  mode: 'layermacro',
  camera: '',
  stream_delay_compensation: 0.05,
  gcode_verbose: false,
  parkhead: false,
  parkpos: 'back_left',
  park_custom_pos_x: 10.0,
  park_custom_pos_y: 10.0,
  park_custom_pos_dz: 0.0,
  park_travel_speed: 100,
  park_retract_speed: 15,
  park_extrude_speed: 15,
  park_retract_distance: 1.0,
  park_extrude_distance: 1.0,
  park_time: 0.1,
  fw_retract: false,
  hyperlapse_cycle: 30,
  autorender: false,
  constant_rate_factor: 23,
  output_framerate: 30,
  pixelformat: 'yuv420p',
  time_format_code: '%Y%m%d_%H%M',
  extraoutputparams: '',
  variable_fps: false,
  targetlength: 10,
  variable_fps_min: 5,
  variable_fps_max: 60,
  duplicatelastframe: 5,
  previewimage: true,
  saveframes: false,
})

/** Fluidd's isBlockedSetting: blocked until the settings load, then as Moonraker's configuration says. */
export const isBlocked = (s: TimelapseSettings | null, key: string) => s?.blockedsettings?.includes(key) ?? !s


/** Fluidd's getLastFrame: the frame count, falling back to the number in the last frame's file name. */
export function lastFrame(info: { framecount?: number; lastframefile?: string } | null) {
  if (!info) return null
  const uniqueCount = +(info.framecount ?? info.lastframefile?.match(/\d+/)?.[0] ?? 0)
  return { count: info.framecount ?? 0, uniqueCount, file: info.lastframefile ?? '' }
}

/** The frame image for a frame number, named like the last frame (frame000123.jpg). */
export const frameFile = (lastFile: string, n: number) => `frame${String(n).padStart(6, '0')}.${lastFile.split('.').pop()}`

/** Fluidd's render length estimate: frames plus duplicated last frames at the fixed or variable frame rate. */
export function renderLength(frames: number, s: Pick<TimelapseSettings, 'variable_fps' | 'variable_fps_min' | 'variable_fps_max' | 'targetlength' | 'output_framerate' | 'duplicatelastframe'>): string {
  const total = frames + (s.duplicatelastframe ?? 0)
  const fps = s.variable_fps ? Math.min(s.variable_fps_max, Math.max(s.variable_fps_min, total / s.targetlength)) : s.output_framerate
  const seconds = (total || 0) / fps
  const minutes = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return minutes ? t('history.format.render_minutes_seconds', { m: minutes, s: secs }) : t('history.format.render_seconds', { s: secs })
}

export type TimelapseFile = { path: string; modified: number; size: number }
export type TimelapseItem = { key: string; title: string; when: number | null; kind: 'video' | 'image' | 'archive' | 'other'; file: TimelapseFile; poster: TimelapseFile | null }

const VIDEO = /\.(mp4|mkv|webm|mov|avi)$/i
const IMAGE = /\.(jpe?g|png|webp|gif)$/i

/**
 * The timelapse folder as things to show: each video with its preview image (moonraker-timelapse writes one beside it,
 * with the same name), frame archives, and images whose video is gone.
 */
export function timelapseItems(files: TimelapseFile[]): TimelapseItem[] {
  const base = (p: string) => p.replace(/\.[^./]+$/, '')
  const images = new Map(files.filter((f) => IMAGE.test(f.path)).map((f) => [base(f.path), f]))
  const used = new Set<string>()
  const items: TimelapseItem[] = []
  for (const f of files) {
    if (IMAGE.test(f.path)) continue
    const poster = VIDEO.test(f.path) ? (images.get(base(f.path)) ?? null) : null
    if (poster) used.add(poster.path)
    items.push({ key: f.path, ...timelapseTitle(f.path), kind: VIDEO.test(f.path) ? 'video' : /\.zip$/i.test(f.path) ? 'archive' : 'other', file: f, poster })
  }
  for (const f of files) if (IMAGE.test(f.path) && !used.has(f.path)) items.push({ key: f.path, ...timelapseTitle(f.path), kind: 'image', file: f, poster: f })
  return items.sort((a, b) => b.file.modified - a.file.modified)
}

/**
 * moonraker-timelapse names its output timelapse_<print file>_<YYYYMMDD_HHMM> (time_format_code's default); the title
 * is the print file without .gcode, and `when` the time in the name, if it has one.
 */
export function timelapseTitle(path: string): { title: string; when: number | null } {
  // Saved frames are zipped as <video name>_frames.zip.
  const name = (path.split('/').pop() ?? path).replace(/\.[^.]+$/, '').replace(/_frames$/, '')
  const m = /^(?:timelapse_)?(.*?)(?:_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2}))?$/.exec(name)
  const title = (m?.[1] || name).replace(/\.gcode(\.3mf)?$/i, '')
  const when = m?.[2] ? new Date(+m[2], +m[3] - 1, +m[4], +m[5], +m[6]).getTime() / 1000 : null
  return { title, when }
}

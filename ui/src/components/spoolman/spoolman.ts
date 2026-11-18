// Spoolman logic ported from the Fluidd fork's Spoolman store and card (src/store/spoolman/, src/components/widgets/
// spoolman/), plus the per-slot spools Valve Oil's tltg_spoolman Klipper module reports. Setting keys and sort keys
// follow Fluidd's uiSettings.spoolman, so both UIs share them in the `fluidd` database namespace.

// ---- Spoolman's API shapes, as Moonraker's proxy passes them through ----

export type RawVendor = { id: number; registered: string; name: string; comment?: string; empty_spool_weight?: number }

export type RawFilament = {
  id: number
  registered: string
  density: number
  diameter: number
  name?: string
  vendor?: RawVendor
  material?: string
  price?: number
  weight?: number
  spool_weight?: number
  comment?: string
  settings_extruder_temp?: number
  settings_bed_temp?: number
  color_hex?: string
  multi_color_hexes?: string
}

export type RawSpool = {
  id: number
  registered: string
  filament: RawFilament
  first_used?: string
  last_used?: string
  price?: number
  remaining_weight?: number
  initial_weight?: number
  spool_weight?: number
  used_weight?: number
  remaining_length?: number
  used_length?: number
  location?: string
  lot_nr?: string
  comment?: string
  archived: boolean
}

export type SpoolmanInfo = { version: string }

/** A spool with the values Fluidd derives: its display name, colors, lengths from weights, and how full it is. */
export type Spool = Omit<RawSpool, 'filament' | 'registered' | 'first_used' | 'last_used'> & {
  filament: Omit<RawFilament, 'registered' | 'multi_color_hexes' | 'color_hex'> & { color_hex?: string; colors?: string[] }
  filament_name: string
  registered: number
  first_used?: number
  last_used?: number
  initial_length?: number
  /** Percent left, 0 to 100. */
  progress?: number
}

// l[mm] = m[g] / D[g/cm³] / A[mm²] × 1000 mm³/cm³
export function weightToLength(weight: number, filament: Pick<RawFilament, 'density' | 'diameter'>) {
  return (weight / filament.density / ((Math.PI / 4) * filament.diameter ** 2)) * 1000
}

const time = (v: string | undefined) => {
  if (!v) return undefined
  const t = Date.parse(v)
  return Number.isFinite(t) ? t : undefined
}

export function toSpool(spool: RawSpool): Spool {
  const f = spool.filament
  const color_hex = f.color_hex ? `#${f.color_hex}` : undefined
  const multi = f.multi_color_hexes ? f.multi_color_hexes.split(',').map((x) => `#${x}`) : undefined
  const filament = {
    ...f,
    color_hex,
    colors: multi?.length ? multi : color_hex ? [color_hex] : undefined,
    spool_weight: f.spool_weight ?? f.vendor?.empty_spool_weight,
  }
  const initial_weight = spool.initial_weight ?? f.weight
  const length = (w: number | undefined) => (w != null ? weightToLength(w, f) : undefined)
  return {
    ...spool,
    filament,
    filament_name: [f.vendor?.name, f.name].filter(Boolean).join(' - ') || String(spool.id),
    registered: time(spool.registered) ?? 0,
    first_used: time(spool.first_used),
    last_used: time(spool.last_used),
    price: spool.price ?? f.price,
    initial_weight,
    spool_weight: spool.spool_weight ?? filament.spool_weight,
    remaining_length: spool.remaining_length ?? length(spool.remaining_weight),
    used_length: spool.used_length ?? length(spool.used_weight),
    initial_length: length(initial_weight),
    progress: initial_weight && spool.used_weight != null ? ((initial_weight - spool.used_weight) / initial_weight) * 100 : undefined,
  }
}

// ---- Moonraker's proxy ----

type ProxyV2<T> = { response: T; error: null } | { response: null; error: string | { message: string } }

/** Moonraker's v2 proxy response (or a bare v1 one) as the response or the error message. */
export function unwrapProxy<T>(payload: unknown): { response: T; error: null } | { response: null; error: string } {
  if (payload != null && typeof payload === 'object' && 'error' in payload && 'response' in payload) {
    const p = payload as ProxyV2<T>
    if (p.error != null) return { response: null, error: typeof p.error === 'string' ? p.error : p.error.message }
    return { response: p.response as T, error: null }
  }
  return { response: payload as T, error: null }
}

// ---- Spoolman's address ----

/** Fluidd's is-loopback, without ipaddr.js: localhost, 127.0.0.0/8, ::1, and IPv4-mapped loopback. */
export function isLoopback(hostname: string) {
  let h = hostname.trim().toLowerCase()
  if (h.startsWith('[') && h.endsWith(']')) h = h.slice(1, -1)
  if (h === 'localhost') return true
  const v4 = (s: string) => /^127(\.\d{1,3}){3}$/.test(s) && s.split('.').every((n) => Number(n) <= 255)
  if (v4(h)) return true
  if (h === '::1' || /^(0{0,4}:){7}0{0,3}1$/.test(h)) return true
  const mapped = /^::ffff:(.+)$/.exec(h)?.[1]
  if (mapped) {
    if (v4(mapped)) return true
    // URL() writes a mapped address in hex: ::ffff:7f00:1.
    const hex = /^([0-9a-f]{1,4}):[0-9a-f]{1,4}$/.exec(mapped)?.[1]
    if (hex) return parseInt(hex, 16) >> 8 === 127
  }
  return false
}

/**
 * Spoolman's address from moonraker.conf's [spoolman] server, as the browser reaches it. A loopback address means
 * Spoolman runs on the printer, so it's the printer's address from here.
 */
export function spoolmanUrl(server: string | undefined, moonrakerOrigin: string): string | undefined {
  if (!server) return undefined
  try {
    const url = new URL(/^https?:\/\//.test(server) ? server : `http://${server}`)
    if (isLoopback(url.hostname)) {
      const api = new URL(moonrakerOrigin).hostname
      if (!isLoopback(api)) url.hostname = api
    }
    return url.toString()
  } catch {
    return undefined
  }
}

/** Spoolman's websocket for every change, as Fluidd opens it. */
export function spoolmanSocketUrl(url: string) {
  const u = new URL(url)
  u.pathname += `${u.pathname.endsWith('/') ? '' : '/'}api/v1/`
  u.protocol = u.protocol === 'https:' ? 'wss:' : 'ws:'
  return u.toString()
}

/** The spool's page in Spoolman's web UI. */
export function spoolPageUrl(url: string, id: number) {
  return `${url.replace(/\/+$/, '')}/spool/show/${id}`
}

// ---- live changes from Spoolman's websocket ----

export type SocketMessage = { type: 'added' | 'updated' | 'deleted'; resource: string; payload: Record<string, unknown> }

/** Applies one Spoolman websocket message to the spool list, as Fluidd's onSpoolChange, onFilamentChange, and onVendorChange do. */
export function applySocketMessage(spools: readonly RawSpool[], msg: SocketMessage): RawSpool[] {
  const out = [...spools]
  if (msg.resource === 'spool') {
    const spool = msg.payload as unknown as RawSpool
    const i = out.findIndex((s) => s.id === spool.id)
    if (msg.type === 'added' && i < 0) out.push(spool)
    else if (msg.type === 'updated' && i >= 0) out[i] = spool
    else if (msg.type === 'deleted' && i >= 0) out.splice(i, 1)
    return out
  }
  // Filament and vendor changes matter only when edited; a deleted one can't still be on a spool.
  if (msg.type !== 'updated') return out
  if (msg.resource === 'filament') {
    const filament = msg.payload as unknown as RawFilament
    return out.map((s) => (s.filament.id === filament.id ? { ...s, filament } : s))
  }
  if (msg.resource === 'vendor') {
    const vendor = msg.payload as unknown as RawVendor
    return out.map((s) => (s.filament.vendor?.id === vendor.id ? { ...s, filament: { ...s.filament, vendor } } : s))
  }
  return out
}

// ---- the selection dialog: search, filter, sort ----

/** Fluidd's filterResults: the ID, comment, filament name, material, or vendor contains the query. */
export function matchesSearch(spool: Spool, query: string) {
  const q = query.trim().toLowerCase()
  if (!q) return true
  return [spool.id, spool.comment, spool.filament.name, spool.filament.material, spool.filament.vendor?.name].some((v) => v != null && String(v).toLowerCase().includes(q))
}

export type SortOrder = { key: string | null; desc: boolean | null }

/** Fluidd's default, uiSettings.spoolman.selectionDialogSortOrder. */
export const DEFAULT_SORT: SortOrder = { key: 'last_used', desc: false }

// Fluidd's table columns, by the key its sort order stores.
export const SORT_KEYS: { key: string; label: string }[] = [
  { key: 'filament_name', label: 'Filament' },
  { key: 'id', label: 'ID' },
  { key: 'filament.material', label: 'Material' },
  { key: 'remaining_weight', label: 'Remaining weight' },
  { key: 'used_weight', label: 'Used weight' },
  { key: 'location', label: 'Location' },
  { key: 'first_used', label: 'First used' },
  { key: 'last_used', label: 'Last used' },
]

function valueAt(spool: Spool, path: string): unknown {
  let v: unknown = spool
  for (const part of path.split('.')) v = v != null && typeof v === 'object' ? (v as Record<string, unknown>)[part] : undefined
  return v
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** Sorted by a Fluidd column key. Spools without the value, such as never used ones by last used, go last either way. */
export function sortSpools(spools: Spool[], order: SortOrder): Spool[] {
  const key = order.key
  if (!key) return spools
  const dir = order.desc ? -1 : 1
  return [...spools].sort((a, b) => {
    const va = valueAt(a, key)
    const vb = valueAt(b, key)
    const na = va == null || va === ''
    const nb = vb == null || vb === ''
    if (na || nb) return na === nb ? a.id - b.id : na ? 1 : -1
    const c = typeof va === 'number' && typeof vb === 'number' ? va - vb : collator.compare(String(va), String(vb))
    return c * dir || a.id - b.id
  })
}

/** Unarchived spools matching the search and material, in the chosen order. */
export function selectableSpools(spools: Spool[], query: string, material: string | null, order: SortOrder) {
  return sortSpools(
    spools.filter((s) => !s.archived && matchesSearch(s, query) && (material == null || (s.filament.material ?? '') === material)),
    order,
  )
}

export function materials(spools: Spool[]) {
  return [...new Set(spools.filter((s) => !s.archived).map((s) => s.filament.material ?? ''))].filter(Boolean).sort(collator.compare)
}

// ---- checks before changing the spool during a print ----

export type SpoolCheck = 'mismatched_filament' | 'no_required_length' | 'no_filament'

/**
 * Fluidd's checks when a spool is picked while a print is under way: the material differs from the slicer's, the file
 * doesn't say how much it needs, or the spool hasn't enough left for the rest of the print.
 */
export function spoolChecks(
  spool: Spool,
  job: { state: string; filamentUsed: number; filamentTotal: number | undefined; filamentTypes: string[] },
  warn: { mismatch: boolean; notEnough: boolean },
): SpoolCheck[] {
  if (['complete', 'cancelled'].includes(job.state)) return []
  const out: SpoolCheck[] = []
  const material = spool.filament.material?.toLowerCase()
  const types = job.filamentTypes.map((t) => t.toLowerCase())
  if (warn.mismatch && material && types.length && !types.includes(material)) out.push('mismatched_filament')
  let required = job.filamentTotal
  if (required && ['printing', 'paused'].includes(job.state)) required = Math.max(required - job.filamentUsed, 0)
  if (!required) out.push('no_required_length')
  else if (warn.notEnough && spool.remaining_length != null && required >= spool.remaining_length) out.push('no_filament')
  return out
}

/** The slicer's filament types from file metadata, which Moonraker gives as one ';'-separated string. */
export function fileFilamentTypes(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean)
  if (typeof value !== 'string') return []
  return value
    .split(/[;,]/)
    .map((s) => s.trim().replace(/^"|"$/g, ''))
    .filter(Boolean)
}

export const CHECK_TEXT: Record<SpoolCheck | 'no_spool', string> = {
  no_spool: "You haven't selected a spool. Are you sure you want to print without filament tracking?",
  no_filament: 'The spool you selected may not have enough filament left on it to successfully finish the print job. Are you sure you want to continue?',
  no_required_length: 'The amount of filament required to print the selected file is unknown. Do you wish to continue?',
  mismatched_filament: 'The material of the selected filament does not match the selected material in the slicer. Are you sure you want to continue?',
}

// ---- per-slot spools from tltg_spoolman ----

export const EXTERNAL_SLOT = 16

export type SlotSpools = { loadedSlot: number | null; spools: Map<number, number> }

const slotNumber = (v: unknown) => {
  if (typeof v !== 'string') return null
  const m = /^slot(\d+)$/.exec(v)
  if (!m) return null
  const n = Number(m[1])
  return n >= 0 && n <= EXTERNAL_SLOT ? n : null
}

/** tltg_spoolman's status: `spools` maps 'slotN' to a spool ID, `loaded_slot` is 'slotN' or null. */
export function slotSpools(status: Record<string, unknown> | undefined): SlotSpools | null {
  if (!status) return null
  const spools = new Map<number, number>()
  const raw = status.spools
  if (raw && typeof raw === 'object') {
    for (const [k, v] of Object.entries(raw)) {
      const slot = slotNumber(k)
      if (slot != null && typeof v === 'number' && Number.isInteger(v) && v > 0) spools.set(slot, v)
    }
  }
  return { loadedSlot: slotNumber(status.loaded_slot), spools: new Map([...spools].sort((a, b) => a[0] - b[0])) }
}

// ---- formatting, as Fluidd's getReadableWeightString and getReadableLengthString ----

export function fmtWeight(g: number | undefined, digits = 2) {
  if (g == null || !Number.isFinite(g)) return '—'
  return Math.abs(g) >= 1000 ? `${(g / 1000).toFixed(digits)} kg` : `${g.toFixed(digits)} g`
}

export function fmtLength(mm: number | undefined) {
  if (mm == null || !Number.isFinite(mm)) return '—'
  const a = Math.abs(mm)
  if (a >= 1000) return `${(mm / 1000).toFixed(2)} m`
  if (a > 100) return `${(mm / 10).toFixed(1)} cm`
  return `${mm.toFixed(1)} mm`
}

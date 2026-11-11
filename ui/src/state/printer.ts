import { MoonrakerClient, resolveMoonrakerOrigin, type ConnectionState } from '../moonraker/client'
import { createStore, useStore } from '../lib/store'

export type KlippyState = 'startup' | 'ready' | 'error' | 'shutdown' | 'disconnected' | 'unknown'
// Klipper object status: object name -> field -> value. Values are whatever Klipper reports.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Status = Record<string, Record<string, any>>

export type ConsoleLine = { id: number; time: number; text: string; kind: 'command' | 'response' | 'error' }

export type TempSeries = { temperatures: number[]; targets?: number[] }

export type HostStats = {
  cpuTemp: number | null
  cpu: number | null
  memory: { total: number; available: number; used: number } | null
  uptime: number | null
  moonrakerCpu: number | null
}

export type PrinterState = {
  conn: ConnectionState
  klippy: KlippyState
  klippyMessage: string
  objects: string[]
  status: Status
  temps: Record<string, TempSeries>
  console: ConsoleLine[]
  /** G-code commands Klipper knows, for feature detection. */
  commands: string[]
  /** configfile.settings for heaters, for target limits. */
  heaterSettings: Record<string, { min_temp?: number; max_temp?: number; target_max_temp?: number; target_min_temp?: number }>
  /** Moonraker's host statistics (machine.proc_stats / notify_proc_stat_update). */
  host: HostStats | null
  /** Bumped when Moonraker reports a file-list change. */
  filesRev: number
  /** Bumped when Moonraker reports a print-history change. */
  historyRev: number
}

export const client = new MoonrakerClient(resolveMoonrakerOrigin())

export const printer = createStore<PrinterState>({
  conn: 'closed',
  klippy: 'unknown',
  klippyMessage: '',
  objects: [],
  status: {},
  temps: {},
  console: [],
  commands: [],
  heaterSettings: {},
  host: null,
  filesRev: 0,
  historyRev: 0,
})

export function usePrinter<S>(selector: (s: PrinterState) => S): S {
  return useStore(printer, selector)
}

/** Status of one Klipper object, or an empty object when it doesn't exist. */
export function useObject(name: string) {
  return usePrinter((s) => s.status[name] ?? EMPTY)
}
const EMPTY = Object.freeze({}) as Record<string, never>

// Objects subscribed in full. Prefix entries end with a space and match every object of that type.
const SUBSCRIBE = [
  'webhooks',
  'print_stats',
  'virtual_sdcard',
  'display_status',
  'idle_timeout',
  'pause_resume',
  'toolhead',
  'gcode_move',
  'extruder',
  'heater_bed',
  'fan',
  'exclude_object',
  'motion_report',
  'save_variables',
  'heater_generic ',
  'temperature_sensor ',
  'temperature_fan ',
  'heater_fan ',
  'controller_fan ',
  'fan_generic ',
  'output_pin ',
  'filament_switch_sensor ',
  'filament_motion_sensor ',
  'box_extras',
  'multi_color_controller',
  'box_stepper ',
  'box_heater_fan ',
  'aht20_f ',
  'tltg_',
  'mcu',
  'mcu ',
]
const extraMatchers: ((name: string) => boolean)[] = []

/** Lets feature modules subscribe to more objects (for example the QIDI Box). */
export function registerObjects(match: (name: string) => boolean) {
  extraMatchers.push(match)
}

function wanted(name: string) {
  for (const p of SUBSCRIBE) {
    if (p.endsWith(' ') || p.endsWith('_') ? name.startsWith(p) : name === p) return true
  }
  return extraMatchers.some((m) => m(name))
}

const TEMP_TYPES = ['extruder', 'heater_bed', 'heater_generic ', 'temperature_sensor ', 'temperature_fan ']
export function isTempObject(name: string) {
  return TEMP_TYPES.some((t) => name === t || (t.endsWith(' ') && name.startsWith(t)))
}

// ---- status batching: apply at most once per animation frame ----
let queued: Status | null = null
function queueStatus(update: Status) {
  if (!queued) {
    queued = {}
    requestAnimationFrame(flushStatus)
  }
  for (const [obj, fields] of Object.entries(update)) queued[obj] = mergeFields(obj, queued[obj], fields)
}
function flushStatus() {
  const q = queued
  queued = null
  if (!q) return
  printer.set((s) => {
    const status = { ...s.status }
    for (const [obj, fields] of Object.entries(q)) status[obj] = mergeFields(obj, status[obj], fields)
    return { ...s, status }
  })
}

// QIDI's multi_color_controller sends only the changed keys of its nested objects (task, box, operation, extra),
// so those merge one level deeper. Every other object's fields arrive whole.
const DEEP_MERGE = new Set(['multi_color_controller'])
function mergeFields(obj: string, prev: Record<string, unknown> | undefined, next: Record<string, unknown>) {
  if (!DEEP_MERGE.has(obj) || !prev) return { ...prev, ...next }
  const out: Record<string, unknown> = { ...prev }
  for (const [k, v] of Object.entries(next)) {
    const old = out[k]
    out[k] = v && old && typeof v === 'object' && typeof old === 'object' && !Array.isArray(v) && !Array.isArray(old) ? { ...old, ...v } : v
  }
  return out
}

// ---- temperature history: Moonraker keeps 20 min at 1 s; we append once a second ----
const TEMP_POINTS = 1200
setInterval(() => {
  const s = printer.get()
  if (s.klippy !== 'ready') return
  const temps: Record<string, TempSeries> = {}
  let changed = false
  for (const [name, series] of Object.entries(s.temps)) {
    const st = s.status[name]
    if (!st || typeof st.temperature !== 'number') {
      temps[name] = series
      continue
    }
    changed = true
    temps[name] = {
      temperatures: [...series.temperatures.slice(-(TEMP_POINTS - 1)), st.temperature],
      targets: series.targets && typeof st.target === 'number' ? [...series.targets.slice(-(TEMP_POINTS - 1)), st.target] : series.targets,
    }
  }
  if (changed) printer.set({ ...s, temps })
}, 1000)

// ---- console ----
let lineId = 0
const CONSOLE_MAX = 300
export function pushConsole(text: string, kind: ConsoleLine['kind']) {
  printer.set((s) => ({ ...s, console: [...s.console.slice(-(CONSOLE_MAX - 1)), { id: ++lineId, time: Date.now(), text, kind }] }))
}

/** Runs G-code and records it, and any error, in the console. */
export async function runGcode(script: string) {
  pushConsole(script, 'command')
  try {
    return await client.gcode(script)
  } catch (e) {
    pushConsole(e instanceof Error ? e.message : String(e), 'error')
    throw e
  }
}

// ---- lifecycle ----
let klippyPoll: ReturnType<typeof setTimeout> | null = null

async function initKlippy() {
  if (klippyPoll) clearTimeout(klippyPoll)
  let info: { klippy_state: KlippyState }
  try {
    info = await client.call('server.info')
  } catch {
    return
  }
  if (info.klippy_state !== 'ready') {
    let message = ''
    try {
      const pi = await client.call<{ state_message: string }>('printer.info')
      message = pi.state_message
    } catch {}
    printer.set((s) => ({ ...s, klippy: info.klippy_state, klippyMessage: message }))
    klippyPoll = setTimeout(initKlippy, 2000)
    return
  }
  const { objects } = await client.call<{ objects: string[] }>('printer.objects.list')
  const sub: Record<string, null> = {}
  for (const o of objects) if (wanted(o)) sub[o] = null
  const res = await client.call<{ status: Status }>('printer.objects.subscribe', { objects: sub })
  let temps: Record<string, TempSeries> = {}
  try {
    const store = await client.call<Record<string, TempSeries>>('server.temperature_store', { include_monitors: false })
    temps = Object.fromEntries(Object.entries(store).filter(([k]) => isTempObject(k)))
  } catch {}
  for (const o of objects) if (isTempObject(o) && !temps[o]) temps[o] = { temperatures: [] }
  // Large, rarely-changing fields are queried once rather than subscribed.
  let commands: string[] = []
  let heaterSettings: PrinterState['heaterSettings'] = {}
  try {
    const q = await client.call<{ status: Status }>('printer.objects.query', { objects: { gcode: ['commands'], configfile: ['settings'] } })
    commands = Object.keys(q.status.gcode?.commands ?? {}).map((c) => c.toUpperCase())
    const settings = q.status.configfile?.settings ?? {}
    for (const [k, v] of Object.entries(settings)) if (k === 'extruder' || k === 'heater_bed' || k.startsWith('heater_generic ')) heaterSettings[k] = v as PrinterState['heaterSettings'][string]
  } catch {}
  // Recent console history, so a Box failure that happened before this page opened still shows its error.
  let consoleLines = printer.get().console
  try {
    const g = await client.call<{ gcode_store: { message: string; time: number; type: 'command' | 'response' }[] }>('server.gcode_store', { count: 200 })
    consoleLines = g.gcode_store.map((l) => ({ id: ++lineId, time: l.time * 1000, text: l.message, kind: l.type === 'command' ? 'command' : l.message.startsWith('!!') ? 'error' : 'response' }))
  } catch {}
  printer.set((s) => ({ ...s, klippy: 'ready', klippyMessage: '', objects, status: res.status, temps, commands, heaterSettings, console: consoleLines }))
  client
    .call<ProcStat>('machine.proc_stats')
    .then((p) => applyHost(p, true))
    .catch(() => {})
  for (const fn of readyHandlers) fn()
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type ProcStat = Record<string, any>
function applyHost(p: ProcStat, initial: boolean) {
  const mr = initial ? p.moonraker_stats?.[p.moonraker_stats.length - 1] : p.moonraker_stats
  printer.set((s) => ({
    ...s,
    host: {
      cpuTemp: typeof p.cpu_temp === 'number' ? p.cpu_temp : (s.host?.cpuTemp ?? null),
      cpu: typeof p.system_cpu_usage?.cpu === 'number' ? p.system_cpu_usage.cpu : (s.host?.cpu ?? null),
      memory: p.system_memory ?? s.host?.memory ?? null,
      uptime: typeof p.system_uptime === 'number' ? p.system_uptime : s.host?.uptime != null ? s.host.uptime + 1 : null,
      moonrakerCpu: typeof mr?.cpu_usage === 'number' ? mr.cpu_usage : (s.host?.moonrakerCpu ?? null),
    },
  }))
}

const readyHandlers = new Set<() => void>()
/** Runs after each Klipper (re)connect once status is loaded. */
export function onKlippyReady(fn: () => void) {
  readyHandlers.add(fn)
  return () => readyHandlers.delete(fn)
}

let started = false
export function startPrinterConnection() {
  if (started) return
  started = true
  client.onState((conn) => {
    printer.set((s) => ({ ...s, conn, ...(conn !== 'open' ? { klippy: 'unknown' as const } : {}) }))
    if (conn === 'open') {
      client
        .call('server.connection.identify', { client_name: 'Valve Oil UI', version: __APP_VERSION__, type: 'web', url: 'https://github.com/thelegendtubaguy/Valve-Oil' })
        .catch(() => {})
        .finally(initKlippy)
    }
  })
  client.on('notify_status_update', (p) => queueStatus(p[0] as Status))
  client.on('notify_klippy_ready', () => initKlippy())
  client.on('notify_klippy_shutdown', () => initKlippy())
  client.on('notify_klippy_disconnected', () => {
    printer.set((s) => ({ ...s, klippy: 'disconnected' }))
    klippyPoll = setTimeout(initKlippy, 2000)
  })
  client.on('notify_gcode_response', (p) => {
    const text = String(p[0] ?? '')
    pushConsole(text, text.startsWith('!!') ? 'error' : 'response')
  })
  client.on('notify_proc_stat_update', (p) => applyHost(p[0] as ProcStat, false))
  client.on('notify_filelist_changed', () => printer.set((s) => ({ ...s, filesRev: s.filesRev + 1 })))
  client.on('notify_history_changed', () => printer.set((s) => ({ ...s, historyRev: s.historyRev + 1 })))
  client.connect()
}

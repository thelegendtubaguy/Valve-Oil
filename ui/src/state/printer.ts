import { MoonrakerClient, resolveMoonrakerOrigin, type ConnectionState } from '../moonraker/client'
import { createStore, useStore } from '../lib/store'
import { readLocal, saveLocal } from '../lib/browser'
import { misreadGcodeNumber } from '../components/control/gcode'
import { t } from '../i18n'

export type KlippyState = 'startup' | 'ready' | 'error' | 'shutdown' | 'disconnected' | 'unknown'
// Klipper object status: object name -> field -> value. Values are whatever Klipper reports.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Status = Record<string, Record<string, any>>

export type ConsoleLine = { id: number; time: number; text: string; kind: 'command' | 'response' | 'error'; origin: 'live' | 'history' }

/** `end` counts every sample this page has had for the series, so the chart can tell samples apart as old ones drop off. */
export type TempSeries = { temperatures: number[]; targets?: number[]; end?: number }

export type HostStats = {
  cpuTemp: number | null
  cpu: number | null
  memory: { total: number; available: number; used: number } | null
  uptime: number | null
  moonrakerCpu: number | null
}

export type PrinterState = {
  conn: ConnectionState
  /** The connection has dropped or failed since it was last open, so a new attempt isn't a first load. */
  connLost: boolean
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
  /** All of configfile.settings (lower-case section names), queried once per Klipper start. */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  settings: Record<string, Record<string, any>>
  /** Moonraker's server.info: loaded components and registered file roots. */
  server: { components: string[]; registered_directories: string[]; warnings: string[] } | null
  /** Moonraker's host statistics (machine.proc_stats / notify_proc_stat_update). */
  host: HostStats | null
  /** Bumped when Moonraker reports a file-list change. */
  filesRev: number
  /** Bumped when Moonraker reports a print-history change. */
  historyRev: number
}

export const client = new MoonrakerClient(resolveMoonrakerOrigin())

// ---- the last state this browser saw, to start from: a page shows the printer as it last was while Klipper's live
// state is read again (about a second and a half on a Max 4), as it does while Klipper restarts. Screens draw at
// once and keep their shape, and what needs Klipper waits for it to be ready. ----

type Snapshot = Pick<PrinterState, 'objects' | 'status' | 'commands'>
const SNAPSHOT_KEY = `vo-printer-snapshot:${client.httpBase}`

function loadSnapshot(): Snapshot | null {
  try {
    const v = JSON.parse(readLocal(SNAPSHOT_KEY) ?? 'null')
    if (!v || !Array.isArray(v.objects) || !Array.isArray(v.commands) || typeof v.status !== 'object' || !v.status) return null
    return { objects: v.objects, status: v.status, commands: v.commands }
  } catch {
    return null
  }
}

function saveSnapshot() {
  const s = printer.get()
  if (s.klippy !== 'ready' || !s.objects.length) return
  saveLocal(SNAPSHOT_KEY, JSON.stringify({ objects: s.objects, status: s.status, commands: s.commands } satisfies Snapshot))
}

export const printer = createStore<PrinterState>({
  conn: 'closed',
  connLost: false,
  klippy: 'unknown',
  klippyMessage: '',
  objects: [],
  status: {},
  temps: {},
  console: [],
  commands: [],
  heaterSettings: {},
  settings: {},
  server: null,
  host: null,
  filesRev: 0,
  historyRev: 0,
  ...loadSnapshot(),
})

export function usePrinter<S>(selector: (s: PrinterState) => S): S {
  return useStore(printer, selector)
}

export const useConnected = () => usePrinter((s) => s.conn === 'open')

/** Whether Klipper knows a G-code command. */
export const useHasCommand = (name: string) => usePrinter((s) => s.commands.includes(name.toUpperCase()))

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
  'vo_',
  'mcu',
  'mcu ',
  'configfile',
  'bed_mesh',
  'probe',
  'heaters',
  'system_stats',
  'stepper_enable',
  'query_endstops',
  'z_tilt',
  'quad_gantry_level',
  'screws_tilt_adjust',
  'manual_probe',
  'firmware_retraction',
  'input_shaper',
  'smart_output_pin ',
  'neopixel ',
  'led ',
  'dotstar ',
  'pwm_cycle_time ',
  'tmc2209 ',
  'tmc2240 ',
  'tmc5160 ',
  'closed_loop ',
  'gcode_macro TIMELAPSE_TAKE_FRAME',
]
const extraMatchers: ((name: string) => boolean)[] = []
// Objects subscribed to only some fields: configfile's settings are large and queried once instead.
const FIELDS: Record<string, string[]> = { configfile: ['save_config_pending', 'save_config_pending_items', 'warnings'] }

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
      end: (series.end ?? series.temperatures.length) + 1,
    }
  }
  if (changed) printer.set({ ...s, temps })
}, 1000)

// ---- console ----
let lineId = 0
const CONSOLE_MAX = 300
export function pushConsole(text: string, kind: ConsoleLine['kind']) {
  printer.set((s) => ({ ...s, console: [...s.console.slice(-(CONSOLE_MAX - 1)), { id: ++lineId, time: Date.now(), text, kind, origin: 'live' }] }))
}

/** Replaces the bounded diagnostic projection from the canonical console store. */
export function replaceConsoleHistory(entries: readonly { message: string; time: number; type: 'command' | 'response' }[]) {
  const console = entries.slice(-CONSOLE_MAX).map((entry) => ({
    id: ++lineId,
    time: entry.time * 1000,
    text: entry.message,
    kind: entry.type === 'command' ? ('command' as const) : entry.message.startsWith('!!') ? ('error' as const) : ('response' as const),
    origin: 'history' as const,
  }))
  printer.set((state) => ({ ...state, console }))
}

/**
 * Runs G-code and records it, and any error, in the console. A command with a number Klipper would misread, such as
 * `G1 X1e-7` (X1 and an extruder move), is refused instead of sent; `typed` sends what the operator typed, in the
 * console or a macro's parameters, as they typed it.
 */
export async function runGcode(script: string, { typed = false }: { typed?: boolean } = {}) {
  pushConsole(script, 'command')
  try {
    const misread = typed ? null : misreadGcodeNumber(script)
    if (misread) throw new Error(t('app.moonraker.misread', { command: misread }))
    return await client.gcode(script)
  } catch (e) {
    pushConsole(e instanceof Error ? e.message : String(e), 'error')
    throw e
  }
}

// ---- lifecycle ----
let klippyPoll: ReturnType<typeof setTimeout> | null = null
// A Klipper restart can start a second load while one is still waiting on Moonraker; only the newest may store
// what it read.
let klippyLoad = 0

async function initKlippy() {
  if (klippyPoll) clearTimeout(klippyPoll)
  if (printer.get().conn !== 'open') return
  const load = ++klippyLoad
  const stale = () => load !== klippyLoad
  if (printer.get().klippy === 'ready') printer.set((s) => ({ ...s, klippy: 'unknown', klippyMessage: '' }))
  try {
    const info = await client.call<{ klippy_state: KlippyState; components?: string[]; registered_directories?: string[]; warnings?: string[] }>('server.info')
    if (stale()) return
    printer.set((s) => ({ ...s, server: { components: info.components ?? [], registered_directories: info.registered_directories ?? [], warnings: info.warnings ?? [] } }))
    if (info.klippy_state !== 'ready') {
      // Show a confirmed failure immediately, even if its detailed message is slow to arrive.
      printer.set((s) => ({ ...s, klippy: info.klippy_state }))
      markAnswered()
      klippyPoll = setTimeout(initKlippy, 2000)
      const pi = await client.call<{ state_message: string }>('printer.info')
      if (!stale()) printer.set((s) => ({ ...s, klippyMessage: pi.state_message }))
      return
    }
    printer.set((s) => ({ ...s, klippy: 'unknown', klippyMessage: '' }))
    const { objects } = await client.call<{ objects: string[] }>('printer.objects.list')
    if (stale()) return
    const sub: Record<string, string[] | null> = {}
    for (const o of objects) if (FIELDS[o]) sub[o] = FIELDS[o]
    else if (wanted(o)) sub[o] = null
    // Status and Klipper's settings are independent reads; neither history nor client identification gates readiness.
    // The settings (heater limits, known commands) don't gate it either: Klipper enforces its own limits, and a query
    // that keeps failing would otherwise hold every screen at Loading. They load in the background until they arrive.
    const [, q] = await Promise.all([
      client.call<{ status: Status }>('printer.objects.subscribe', { objects: sub }).then((res) => {
        if (stale()) return
        // Apply the snapshot immediately so later notifications cannot be overwritten by it.
        queued = null
        printer.set((s) => ({ ...s, objects, status: res.status }))
      }),
      querySettings().catch(() => null),
    ])
    if (stale()) return
    const temps = Object.fromEntries(objects.filter(isTempObject).map((o) => [o, { temperatures: [], ...(typeof printer.get().status[o]?.target === 'number' ? { targets: [] } : {}) }]))
    printer.set((s) => ({ ...s, klippy: 'ready', klippyMessage: '', temps, ...(q ? settingsFrom(q) : { commands: [], heaterSettings: {}, settings: {} }) }))
    if (!q) retrySettings(stale)
    saveSnapshot()
    markAnswered()

    void client.call<Record<string, TempSeries>>('server.temperature_store', { include_monitors: false }).then((store) => {
      if (stale()) return
      // Moonraker's store already holds every sample taken since ready, so it replaces the live series rather than
      // being joined to them.
      printer.set((s) => {
        const temps = { ...s.temps }
        for (const [name, history] of Object.entries(store)) if (isTempObject(name)) temps[name] = history
        return { ...s, temps }
      })
    }).catch(() => {})
    void client.call<ProcStat>('machine.proc_stats').then((p) => {
      if (!stale()) applyHost(p, true)
    }).catch(() => {})
    for (const fn of readyHandlers) fn()
  } catch (error) {
    if (stale()) return
    ++klippyLoad
    if (klippyPoll) clearTimeout(klippyPoll)
    printer.set((s) => ({ ...s, klippyMessage: error instanceof Error ? error.message : String(error) }))
    markAnswered()
    klippyPoll = setTimeout(initKlippy, 2000)
  }
}

// Large, rarely-changing fields are queried once per Klipper start rather than subscribed.
const querySettings = () => client.call<{ status: Status }>('printer.objects.query', { objects: { gcode: ['commands'], configfile: ['settings'] } })

function settingsFrom(q: { status: Status }): Pick<PrinterState, 'commands' | 'heaterSettings' | 'settings'> {
  const settings = q.status.configfile?.settings ?? {}
  const commands = Object.keys(q.status.gcode?.commands ?? {}).map((c) => c.toUpperCase())
  const heaterSettings: PrinterState['heaterSettings'] = {}
  for (const [k, v] of Object.entries(settings)) if (k === 'extruder' || k === 'heater_bed' || k.startsWith('heater_generic ')) heaterSettings[k] = v as PrinterState['heaterSettings'][string]
  return { commands, heaterSettings, settings }
}

/** Retries Klipper's settings every 2 s until they arrive or this Klipper session ends. */
function retrySettings(stale: () => boolean) {
  setTimeout(() => {
    if (stale()) return
    querySettings().then(
      (q) => {
        if (stale()) return
        printer.set((s) => ({ ...s, ...settingsFrom(q) }))
        saveSnapshot()
      },
      () => retrySettings(stale),
    )
  }, 2000)
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type ProcStat = Record<string, any>
// The printer's clock minus this browser's, in seconds. Moonraker stamps its stored console lines with the printer's
// time; its live host statistics carry that time as they're sent, which measures the difference.
let hostClockOffsetSeconds = 0
export const hostClockOffset = () => hostClockOffsetSeconds

function applyHost(p: ProcStat, initial: boolean) {
  const mr = initial ? p.moonraker_stats?.[p.moonraker_stats.length - 1] : p.moonraker_stats
  // A stored sample can be a second old; only a live one is sent when it's taken.
  if (!initial && typeof mr?.time === 'number') hostClockOffsetSeconds = mr.time - Date.now() / 1000
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

// Klipper has answered on this connection: it's ready, with its state and settings, or has said why it isn't.
// Requests that can wait, such as the console's history and Home's files and print history, wait for it, so they
// don't queue at Moonraker ahead of what the screens and their buttons need.
let answered = false
let answerWaiters: (() => void)[] = []
function markAnswered() {
  answered = true
  for (const wake of answerWaiters.splice(0)) wake()
}

/** Null once Klipper has answered on this connection; until then, a promise of its answer or of `maxMs` passing. */
export function afterKlipperAnswers(maxMs = 3000): Promise<void> | null {
  if (answered) return null
  return new Promise((resolve) => {
    answerWaiters.push(resolve)
    setTimeout(resolve, maxMs)
  })
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
    ++klippyLoad
    if (klippyPoll) clearTimeout(klippyPoll)
    queued = null
    answered = false
    printer.set((s) => ({ ...s, conn, connLost: conn === 'open' ? false : conn === 'closed' ? true : s.connLost, klippy: 'unknown', klippyMessage: '' }))
    if (conn === 'open') {
      client
        .call('server.connection.identify', { client_name: 'Valve Oil UI', version: __APP_VERSION__, type: 'web', url: 'https://github.com/thelegendtubaguy/Valve-Oil' })
        .catch(() => {})
      void initKlippy()
    }
  })
  client.on('notify_status_update', (p) => queueStatus(p[0] as Status))
  // The newest state for the next page, as this one closes or reloads.
  if (typeof window !== 'undefined') window.addEventListener('pagehide', saveSnapshot)
  client.on('notify_klippy_ready', () => initKlippy())
  client.on('notify_klippy_shutdown', () => {
    printer.set((s) => ({ ...s, klippy: 'shutdown', klippyMessage: '' }))
    void initKlippy()
  })
  client.on('notify_klippy_disconnected', () => {
    ++klippyLoad
    if (klippyPoll) clearTimeout(klippyPoll)
    queued = null
    printer.set((s) => ({ ...s, klippy: 'disconnected', klippyMessage: '' }))
    klippyPoll = setTimeout(initKlippy, 2000)
  })
  client.on('notify_proc_stat_update', (p) => applyHost(p[0] as ProcStat, false))
  client.on('notify_filelist_changed', () => printer.set((s) => ({ ...s, filesRev: s.filesRev + 1 })))
  client.on('notify_history_changed', () => printer.set((s) => ({ ...s, historyRev: s.historyRev + 1 })))
  client.connect()
}

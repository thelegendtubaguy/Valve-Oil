import { useEffect, useMemo } from 'react'
import { createStore, useStore } from '../../lib/store'
import { afterKlipperAnswers, client, hostClockOffset, printer, pushConsole, replaceConsoleHistory, runGcode, useConnected, usePrinter, type ConsoleLine } from '../../state/printer'
import { FLUIDD, useDbItem } from '../../state/database'
import { CONSOLE_RETENTION, EXTRA_KNOWN, addToHistory, fromStore, normalizeFilter, visibleEntries, type ConsoleEntry, type ConsoleFilter } from './model'

// The console owns Moonraker history. Printer state keeps a 300-line projection for QIDI Box diagnostics.

type ConsoleState = { entries: ConsoleEntry[]; loaded: boolean }
export const consoleStore = createStore<ConsoleState>({ entries: [], loaded: false })

let nextId = 1
function append(lines: Omit<ConsoleEntry, 'id'>[]) {
  if (!lines.length) return
  consoleStore.set((s) => ({ ...s, entries: [...s.entries, ...lines.map((l) => ({ ...l, id: nextId++ }))].slice(-CONSOLE_RETENTION) }))
}

type PendingEntry = Omit<ConsoleEntry, 'id'>
type ConsoleLifecycle = {
  loadRevision: number
  pending: PendingEntry[] | null
  detach: (() => void)[]
}

let lifecycle: ConsoleLifecycle | null = null
// Moonraker timestamps stored lines but sends live responses as text only, so receive time bounds the overlap. Both are
// in the printer's time: the browser's clock, shifted by the difference Moonraker's host statistics measure.
const EVENT_TIME_TOLERANCE_SECONDS = 1
const printerNow = () => Date.now() / 1000 + hostClockOffset()

function mergeSnapshot(entries: ConsoleEntry[], pending: PendingEntry[], startedAt: number) {
  const matched = new Map<number, number>()
  let snapshotIndex = 0
  for (let pendingIndex = 0; pendingIndex < pending.length; pendingIndex++) {
    const live = pending[pendingIndex]
    for (let search = snapshotIndex; search < entries.length; search++) {
      const entry = entries[search]
      if (entry.time < startedAt) continue
      if (entry.type !== live.type || entry.message !== live.message || Math.abs(entry.time - live.time) >= EVENT_TIME_TOLERANCE_SECONDS) continue
      matched.set(pendingIndex, search)
      snapshotIndex = search + 1
      break
    }
  }

  const before = new Map<number, PendingEntry[]>()
  const trailing: PendingEntry[] = []
  for (let pendingIndex = 0; pendingIndex < pending.length; pendingIndex++) {
    if (matched.has(pendingIndex)) continue
    let nextSnapshot: number | undefined
    for (let next = pendingIndex + 1; next < pending.length; next++) {
      nextSnapshot = matched.get(next)
      if (nextSnapshot !== undefined) break
    }
    if (nextSnapshot === undefined) trailing.push(pending[pendingIndex])
    else {
      const group = before.get(nextSnapshot) ?? []
      group.push(pending[pendingIndex])
      before.set(nextSnapshot, group)
    }
  }

  const merged: ConsoleEntry[] = []
  for (let index = 0; index < entries.length; index++) {
    for (const entry of before.get(index) ?? []) merged.push({ ...entry, id: nextId++ })
    merged.push(entries[index])
  }
  for (const entry of trailing) merged.push({ ...entry, id: nextId++ })
  return merged.slice(-CONSOLE_RETENTION)
}

function finishPending(active: ConsoleLifecycle) {
  const pending = active.pending
  active.pending = null
  if (pending?.length) append(pending)
}

async function loadStore(active: ConsoleLifecycle) {
  finishPending(active)
  const loadRevision = ++active.loadRevision
  const startedAt = printerNow()
  const pending: PendingEntry[] = []
  active.pending = pending
  try {
    // Lines that arrive meanwhile wait in `pending`; the history comes after Klipper's state, which draws the screens.
    const wait = afterKlipperAnswers()
    if (wait) {
      await wait
      if (lifecycle !== active || active.loadRevision !== loadRevision) return
    }
    const r = await client.call<{ gcode_store: { message: string; time: number; type: string }[] }>('server.gcode_store', { count: CONSOLE_RETENTION })
    if (lifecycle !== active || active.loadRevision !== loadRevision) return
    const entries = fromStore(r.gcode_store, nextId)
    nextId += entries.length
    const merged = mergeSnapshot(entries, pending, startedAt)
    active.pending = null
    consoleStore.set({ entries: merged, loaded: true })
    replaceConsoleHistory(merged)
  } catch {
    if (lifecycle !== active || active.loadRevision !== loadRevision) return
    finishPending(active)
    consoleStore.set((s) => ({ ...s, loaded: true }))
  }
}

function onResponse(active: ConsoleLifecycle, params: unknown[]) {
  const message = String(params[0] ?? '')
  const time = printerNow()
  pushConsole(message, message.startsWith('!!') ? 'error' : 'response')
  if (active.pending) active.pending.push({ message, time, type: 'response' })
  else append([{ message, time, type: 'response' }])
}

// Commands any screen sends go through runGcode, which records them in printer state; the console picks them up
// from there. Its error lines are the RPC's error, which Klipper has usually already sent as a `!!` reply.
let lastSeen = 0
function mirrorPrinterConsole(active: ConsoleLifecycle, prev: { lines: ConsoleLine[] }) {
  const s = printer.get()
  if (s.console === prev.lines) return
  prev.lines = s.console
  // Background history is already loaded here; only mirror newly sent commands and live RPC errors.
  const fresh = s.console.filter((l) => l.id > lastSeen && l.origin === 'live')
  lastSeen = Math.max(lastSeen, ...s.console.map((l) => l.id))
  const now = Date.now()
  const offset = hostClockOffset()
  const recent = [...consoleStore.get().entries.slice(-30), ...(active.pending ?? [])]
  const add: Omit<ConsoleEntry, 'id'>[] = []
  for (const l of fresh) {
    if (now - l.time > 5000) continue
    const time = l.time / 1000 + offset
    if (l.kind === 'command') add.push({ message: l.text, time, type: 'command' })
    else if (l.kind === 'error' && !l.text.startsWith('!!') && !recent.some((e) => e.message === `!! ${l.text}` && now / 1000 + offset - e.time < 10)) add.push({ message: `!! ${l.text}`, time, type: 'response' })
  }
  if (active.pending) active.pending.push(...add)
  else append(add)
}

/** Attaches the one console history/stream lifecycle. Repeated starts keep the existing attachment. */
export function startConsoleStore() {
  if (lifecycle) return
  const active: ConsoleLifecycle = { loadRevision: 0, pending: null, detach: [] }
  lifecycle = active
  const mirrored = { lines: printer.get().console }
  lastSeen = mirrored.lines.length ? mirrored.lines[mirrored.lines.length - 1].id : 0
  active.detach = [
    client.on('notify_gcode_response', (params) => onResponse(active, params)),
    client.onState((state) => {
      if (state === 'open') void loadStore(active)
      else {
        ++active.loadRevision
        finishPending(active)
      }
    }),
    printer.subscribe(() => mirrorPrinterConsole(active, mirrored)),
  ]
  if (client.state === 'open') void loadStore(active)
}

/** Detaches the console and fences any history request that is still resolving. */
export function stopConsoleStore() {
  const active = lifecycle
  if (!active) return
  lifecycle = null
  ++active.loadRevision
  finishPending(active)
  for (const detach of active.detach) detach()
}

startConsoleStore()

// ---- G-code help: every command Klipper knows, with its help text ----
const helpStore = createStore<Record<string, string>>({})
async function loadHelp() {
  try {
    const h = await client.call<Record<string, string>>('printer.gcode.help')
    helpStore.set(Object.fromEntries(Object.entries(h).map(([k, v]) => [k.toUpperCase(), v ?? ''])))
  } catch {
    /* Klipper isn't running; printer.commands may still have names */
  }
}

/** Every command Klipper knows, upper case, with help; reloaded each time Klipper becomes ready. */
export function useGcodeHelp() {
  const klippy = usePrinter((s) => s.klippy)
  const connected = useConnected()
  useEffect(() => {
    if (connected) loadHelp()
  }, [klippy, connected])
  const help = useStore(helpStore, (h) => h)
  const commands = usePrinter((s) => s.commands)
  return useMemo(() => {
    const all: Record<string, string> = Object.fromEntries(commands.map((c) => [c, '']))
    Object.assign(all, help)
    const names = Object.keys(all).sort()
    return { help: all, names, known: new Set([...names, ...EXTRA_KNOWN]) }
  }, [help, commands])
}

// ---- settings shared with Fluidd ----

/** Fluidd's console settings, read and written in its namespace with its keys. */
export function useConsoleSettings() {
  const [hideTempWaits, setHideTempWaits] = useDbItem(FLUIDD, 'uiSettings.general.hideTempWaits', true)
  const [flip, setFlip] = useDbItem(FLUIDD, 'uiSettings.general.flipConsoleLayout', false)
  const [rawFilters, setFilters] = useDbItem<ConsoleFilter[]>(FLUIDD, 'console.consoleFilters', [])
  const [lastCleared, setLastCleared] = useDbItem(FLUIDD, 'console.lastCleared', 0)
  const filters = useMemo(() => (Array.isArray(rawFilters) ? rawFilters.map(normalizeFilter) : []), [rawFilters])
  return { hideTempWaits, setHideTempWaits, flip, setFlip, filters, setFilters, lastCleared, clear: () => setLastCleared(Date.now()) }
}

/** The console lines to show, after Clear and the filters. */
export function useConsoleEntries(search?: string) {
  const entries = useStore(consoleStore, (s) => s.entries)
  const loaded = useStore(consoleStore, (s) => s.loaded)
  const { hideTempWaits, filters, lastCleared } = useConsoleSettings()
  const shown = useMemo(() => visibleEntries(entries, { hideTempWaits, filters, lastCleared, search }), [entries, hideTempWaits, filters, lastCleared, search])
  return { shown, loaded }
}

/** Command history, newest first, shared with Fluidd. */
export function useCommandHistory() {
  const [history, setHistory] = useDbItem<string[]>(FLUIDD, 'console.commandHistory', [])
  return [Array.isArray(history) ? history : [], setHistory] as const
}

// ---- the command being typed, shared so clicking a command in any console puts it in the input ----
export const draftStore = createStore<{ text: string; focus: number }>({ text: '', focus: 0 })
export function fillCommand(text: string) {
  draftStore.set((d) => ({ text, focus: d.focus + 1 }))
}

/** Sends what was typed, as Fluidd does: M112 also calls the emergency stop endpoint. */
export async function sendCommand(command: string, history: string[], setHistory: (h: string[]) => Promise<void>) {
  const text = command.trim()
  if (!text) return
  setHistory(addToHistory(history, text)).catch(() => {})
  if (text.toLowerCase() === 'm112') client.call('printer.emergency_stop').catch(() => {})
  try {
    await runGcode(text, { typed: true })
  } catch {
    /* the error shows in the console */
  }
}

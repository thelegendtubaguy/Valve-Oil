import { useEffect, useMemo } from 'react'
import { createStore, useStore } from '../../lib/store'
import { client, printer, runGcode, useConnected, usePrinter, type ConsoleLine } from '../../state/printer'
import { FLUIDD, useDbItem } from '../../state/database'
import { CONSOLE_RETENTION, EXTRA_KNOWN, addToHistory, fromStore, normalizeFilter, visibleEntries, type ConsoleEntry, type ConsoleFilter } from './model'

// The console's own scrollback. Printer state keeps the last 300 lines and reloads them only once Klipper is ready;
// this keeps Fluidd's 1000 and loads Moonraker's gcode store as soon as Moonraker answers, so the reason Klipper
// stopped can be read while it's stopped.

type ConsoleState = { entries: ConsoleEntry[]; loaded: boolean }
export const consoleStore = createStore<ConsoleState>({ entries: [], loaded: false })

let nextId = 1
function append(lines: Omit<ConsoleEntry, 'id'>[]) {
  if (!lines.length) return
  consoleStore.set((s) => ({ ...s, entries: [...s.entries, ...lines.map((l) => ({ ...l, id: nextId++ }))].slice(-CONSOLE_RETENTION) }))
}

// Replies that arrive while the gcode store loads, which it may or may not already hold.
let pending: string[] | null = null

async function loadStore() {
  pending = []
  try {
    const r = await client.call<{ gcode_store: { message: string; time: number; type: string }[] }>('server.gcode_store', { count: CONSOLE_RETENTION })
    const entries = fromStore(r.gcode_store, nextId)
    nextId += entries.length
    const tail = new Set(entries.slice(-50).map((e) => e.message))
    const late = (pending ?? []).filter((m) => !tail.has(m))
    pending = null
    consoleStore.set({ entries: entries.slice(-CONSOLE_RETENTION), loaded: true })
    append(late.map((message) => ({ message, time: Date.now() / 1000, type: 'response' })))
  } catch {
    pending = null
    consoleStore.set((s) => ({ ...s, loaded: true }))
  }
}

function onResponse(params: unknown[]) {
  const message = String(params[0] ?? '')
  if (pending) pending.push(message)
  else append([{ message, time: Date.now() / 1000, type: 'response' }])
}

// Commands any screen sends go through runGcode, which records them in printer state; the console picks them up
// from there. Its error lines are the RPC's error, which Klipper has usually already sent as a `!!` reply.
let lastSeen = 0
function mirrorPrinterConsole(prev: { klippy: string; lines: ConsoleLine[] }) {
  const s = printer.get()
  const reloaded = s.klippy === 'ready' && prev.klippy !== 'ready'
  prev.klippy = s.klippy
  if (s.console === prev.lines) return
  prev.lines = s.console
  const fresh = s.console.filter((l) => l.id > lastSeen)
  if (s.console.length) lastSeen = s.console[s.console.length - 1].id
  // Printer state reloads its own lines from the gcode store when Klipper becomes ready; those are already here.
  if (reloaded) return
  const now = Date.now()
  const recent = consoleStore.get().entries.slice(-30)
  const add: Omit<ConsoleEntry, 'id'>[] = []
  for (const l of fresh) {
    if (now - l.time > 5000) continue
    if (l.kind === 'command') add.push({ message: l.text, time: l.time / 1000, type: 'command' })
    else if (l.kind === 'error' && !l.text.startsWith('!!') && !recent.some((e) => e.message === `!! ${l.text}` && now / 1000 - e.time < 10)) add.push({ message: `!! ${l.text}`, time: l.time / 1000, type: 'response' })
  }
  append(add)
}

// The console follows the printer from the moment this module loads.
client.on('notify_gcode_response', onResponse)
client.onState((c) => c === 'open' && loadStore())
if (client.state === 'open') loadStore()
const mirrored = { klippy: printer.get().klippy, lines: printer.get().console }
lastSeen = mirrored.lines.length ? mirrored.lines[mirrored.lines.length - 1].id : 0
printer.subscribe(() => mirrorPrinterConsole(mirrored))

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

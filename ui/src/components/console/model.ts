// Console logic shared by the Console screen and Home's mini console, ported from Fluidd's console store
// (src/store/console/ in the Fluidd fork) so filters and command history stored in the `fluidd` namespace mean the
// same thing in both UIs.

export type ConsoleEntry = { id: number; /** epoch seconds */ time: number; message: string; type: 'command' | 'response' }

export type ConsoleFilterType = 'contains' | 'starts-with' | 'expression'
/** Fluidd's stored shape (`console.consoleFilters`). Older Fluidd releases stored the type as 0, 1, or 2. */
export type ConsoleFilter = { id: string; name: string; type: ConsoleFilterType; value: string; enabled: boolean }

/** Fluidd keeps this many console lines and this many sent commands. */
export const CONSOLE_RETENTION = 1000
export const COMMAND_HISTORY = 20

const escapeRegExp = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

export function normalizeFilter(f: ConsoleFilter | (Omit<ConsoleFilter, 'type'> & { type: number | string })): ConsoleFilter {
  const t = f.type
  const type: ConsoleFilterType = t === 1 || t === 'starts-with' ? 'starts-with' : t === 2 || t === 'expression' ? 'expression' : 'contains'
  return { ...f, type }
}

/** Fluidd's compileExpression. A filter whose expression doesn't compile matches nothing. */
export function compileFilter(f: ConsoleFilter): RegExp {
  try {
    if (f.type === 'starts-with') return new RegExp(`^${escapeRegExp(f.value)}.*`, 'i')
    if (f.type === 'expression') return new RegExp(f.value)
    return new RegExp(`.*${escapeRegExp(f.value)}.*`, 'i')
  } catch {
    return /(?!)/
  }
}

export function validExpression(value: string) {
  try {
    new RegExp(value)
    return true
  } catch {
    return false
  }
}

// Fluidd's "Hide temp waits": the temperature reports M105 and M109/M190 print while heating.
const TEMP_WAIT = /^(?:ok\s+)?(?:b|t\d+):\d+\.\d+ \/\d+\.+\d+/i

export type ViewOptions = { hideTempWaits: boolean; filters: ConsoleFilter[]; lastCleared: number; search?: string }

/** The entries the console shows: after the last Clear, without temperature waits or lines a filter hides. */
export function visibleEntries(entries: ConsoleEntry[], o: ViewOptions): ConsoleEntry[] {
  const active = o.filters.filter((f) => f.enabled && f.value).map(compileFilter)
  const search = o.search?.trim().toLowerCase()
  return entries.filter(
    (e) =>
      (!e.time || e.time * 1000 > o.lastCleared) &&
      // Klipper answers every command with ok; Fluidd never shows a bare one.
      e.message !== 'ok' &&
      (!o.hideTempWaits || !TEMP_WAIT.test(e.message)) &&
      !active.some((r) => r.test(e.message)) &&
      (!search || e.message.toLowerCase().includes(search)),
  )
}

export type LineTone = 'command' | 'error' | 'info' | 'plain'
/** Errors (`!!`) stand out, Klipper's `//` messages are quieter than other replies. */
export function lineTone(e: ConsoleEntry, line = e.message): LineTone {
  if (e.type === 'command') return 'command'
  if (e.message.startsWith('!!')) return 'error'
  if (line.startsWith('//')) return 'info'
  return 'plain'
}

export type Segment = { text: string; command?: string; mark?: boolean }

/**
 * Splits a reply into text and the known commands in it, which Fluidd makes clickable, and marks search matches.
 * `known` holds upper-case command names.
 */
export function segments(text: string, known: Set<string>, search?: string): Segment[] {
  const out: Segment[] = []
  const re = /[A-Z_][A-Z0-9_.]+/g
  let at = 0
  for (let m = re.exec(text); m; m = re.exec(text)) {
    // A sentence's full stop isn't part of the command.
    const word = m[0].replace(/\.+$/, '')
    if (!known.has(word)) continue
    if (m.index > at) out.push({ text: text.slice(at, m.index) })
    out.push({ text: word, command: word })
    at = m.index + word.length
  }
  if (at < text.length) out.push({ text: text.slice(at) })
  return search ? out.flatMap((s) => markSearch(s, search)) : out
}

function markSearch(s: Segment, search: string): Segment[] {
  const q = search.trim().toLowerCase()
  if (!q) return [s]
  const out: Segment[] = []
  const lower = s.text.toLowerCase()
  let at = 0
  for (let i = lower.indexOf(q); i >= 0; i = lower.indexOf(q, i + q.length)) {
    if (i > at) out.push({ ...s, text: s.text.slice(at, i) })
    out.push({ ...s, text: s.text.slice(i, i + q.length), mark: true })
    at = i + q.length
  }
  if (at < s.text.length) out.push({ ...s, text: s.text.slice(at) })
  return out
}

/** Fluidd's always-known words, which Klipper's prompts mention but `help` doesn't list. */
export const EXTRA_KNOWN = ['TESTZ', 'ABORT', 'ACCEPT', 'ADJUSTED']

/**
 * Commands that complete the word being typed: the first word of the last line, with the caret at the end.
 * Returns the prefix typed (upper case) and the matches, shortest first.
 */
export function completions(input: string, caret: number, commands: string[]): { word: string; matches: string[] } {
  if (caret !== input.length) return { word: '', matches: [] }
  const line = input.slice(input.lastIndexOf('\n') + 1)
  if (!line || /\s/.test(line)) return { word: '', matches: [] }
  const word = line.toUpperCase()
  const matches = commands.filter((c) => c.startsWith(word) && c !== word).sort((a, b) => a.length - b.length || a.localeCompare(b))
  return { word, matches }
}

/** Replaces the word being completed (the last line's first word) with a command. */
export function applyCompletion(input: string, command: string) {
  return input.slice(0, input.lastIndexOf('\n') + 1) + command + ' '
}

/** The command the caret's line starts with, for showing its help. */
export function currentCommand(input: string, caret: number) {
  const start = input.lastIndexOf('\n', Math.max(0, caret - 1)) + 1
  return (input.slice(start).trimStart().split(/\s/)[0] ?? '').toUpperCase()
}

/** Fluidd's history: newest first, at most 20, kept even when repeated. */
export function addToHistory(history: string[], command: string) {
  return [command, ...history].slice(0, COMMAND_HISTORY)
}

/** Moonraker gcode_store entries as console entries, numbered from `firstId`. */
export function fromStore(store: { message: string; time: number; type: string }[], firstId: number): ConsoleEntry[] {
  return store.map((e, i) => ({ id: firstId + i, time: e.time, message: e.message, type: e.type === 'command' ? 'command' : 'response' }))
}

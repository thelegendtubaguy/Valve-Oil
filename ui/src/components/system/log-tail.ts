// Turns the end of a log, read from a byte offset, into whole lines for the log viewer.

export type LogTail = { lines: string[]; shown: number; total: number; truncated: boolean }

/** At most this many lines are kept, so a long log stays quick to render and search. */
export const MAX_LINES = 20000

export function tailText(text: string, start: number, total: number): LogTail {
  let body = text
  // Reading from the middle of the file starts part way through a line; drop it.
  if (start > 0) {
    const nl = body.indexOf('\n')
    body = nl >= 0 ? body.slice(nl + 1) : ''
  }
  if (body.endsWith('\n')) body = body.slice(0, -1)
  let lines = body ? body.split(/\r?\n/) : []
  const cut = lines.length > MAX_LINES
  if (cut) lines = lines.slice(-MAX_LINES)
  return { lines, shown: cut ? new TextEncoder().encode(lines.join('\n')).length : text.length, total, truncated: start > 0 || cut }
}

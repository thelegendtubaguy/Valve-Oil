// The Diagnostics check: Valve Oil's status service reads the printer's logs for its known fault signatures
// (installer/runtime/diagnostics.py, run by installer/system/vo_status.py at GET and POST api/diagnostics). This file
// reads what the service sends, defensively, as src/qidi/vo-status.ts does: a body it doesn't understand is no check,
// a finding without an id or title is left out, and an unknown severity reads as "info". The service's text (titles,
// meanings, what to do, details, log lines, errors) is English and is shown as it arrives.
//
// Kept free of the printer store, so its tests don't load src/state/printer.ts.

export type FaultCheckSince = 'all' | 'day' | 'start'
export type FaultCheckState = 'idle' | 'running' | 'done' | 'failed'
/** fault: needs the operator; fixed: fixed by the release installed now; repaired: Valve Oil repaired it each time. */
export type FaultSeverity = 'fault' | 'fixed' | 'repaired' | 'info'

export const FAULT_CHECK_SINCE: readonly FaultCheckSince[] = ['start', 'day', 'all']
export const FAULT_SEVERITIES: readonly FaultSeverity[] = ['fault', 'fixed', 'repaired', 'info']
/** How often the card asks again while a check runs. */
export const FAULT_CHECK_POLL_MS = 2000

export interface FaultFinding {
  id: string
  title: string
  severity: FaultSeverity
  meaning: string
  action: string
  fixed_in: string | null
  /** A web address, only when it is one. */
  docs: string | null
  count: number
  /** Log times, "YYYY-MM-DD HH:MM:SS" in the printer's time. */
  first: string | null
  last: string | null
  details: string[]
  evidence: string[]
}

export interface FaultCheckedFile {
  name: string
  size: number
  read_from: number
  bytes_read: number
}

export interface FaultChecked {
  since: FaultCheckSince | null
  files: FaultCheckedFile[]
  /** The first and last log times read, in the printer's time. */
  from: string | null
  to: string | null
  /** Whether the whole period asked for was read. */
  complete: boolean
  /** "time": the check stopped at its time limit. */
  stopped: 'time' | null
  elapsed: number | null
}

export interface FaultResult {
  checked: FaultChecked
  installed: string | null
  /** Most severe first. */
  findings: FaultFinding[]
}

export interface FaultCheck {
  state: FaultCheckState
  since: FaultCheckSince | null
  started_at: string | null
  finished_at: string | null
  error: string | null
  /** null while running or failed, and when the service sent a result this page doesn't read. */
  result: FaultResult | null
  report: string | null
}

const STATES: readonly string[] = ['idle', 'running', 'done', 'failed']

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)
const optionalText = (value: unknown): string | null => (typeof value === 'string' && value !== '' ? value : null)
const text = (value: unknown): string => (typeof value === 'string' ? value : '')
const texts = (value: unknown): string[] => (Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item !== '') : [])
const count = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) && value >= 0 ? Math.floor(value) : 0)
const since = (value: unknown): FaultCheckSince | null => (FAULT_CHECK_SINCE.includes(value as FaultCheckSince) ? (value as FaultCheckSince) : null)

const webAddress = (value: unknown): string | null => {
  const url = optionalText(value)
  if (!url) return null
  try {
    const protocol = new URL(url).protocol
    return protocol === 'https:' || protocol === 'http:' ? url : null
  } catch {
    return null
  }
}

const parseFinding = (value: unknown): FaultFinding | null => {
  if (!isRecord(value)) return null
  const id = optionalText(value.id)
  const title = optionalText(value.title)
  if (!id || !title) return null
  return {
    id,
    title,
    severity: FAULT_SEVERITIES.includes(value.severity as FaultSeverity) ? (value.severity as FaultSeverity) : 'info',
    meaning: text(value.meaning),
    action: text(value.action),
    fixed_in: optionalText(value.fixed_in),
    docs: webAddress(value.docs),
    count: count(value.count),
    first: optionalText(value.first),
    last: optionalText(value.last),
    details: texts(value.details),
    evidence: texts(value.evidence),
  }
}

const parseChecked = (value: unknown): FaultChecked => {
  const checked = isRecord(value) ? value : {}
  const files = Array.isArray(checked.files)
    ? checked.files.filter(isRecord).flatMap((file) => {
        const name = optionalText(file.name)
        return name ? [{ name, size: count(file.size), read_from: count(file.read_from), bytes_read: count(file.bytes_read) }] : []
      })
    : []
  return {
    since: since(checked.since),
    files,
    from: optionalText(checked.from),
    to: optionalText(checked.to),
    // A result that doesn't say it read everything didn't.
    complete: checked.complete === true,
    stopped: checked.stopped === 'time' ? 'time' : null,
    elapsed: typeof checked.elapsed === 'number' && Number.isFinite(checked.elapsed) ? checked.elapsed : null,
  }
}

/** The checker's result (its schema 1), or null for anything else. */
const parseResult = (value: unknown): FaultResult | null => {
  if (!isRecord(value) || value.schema !== 1) return null
  const findings = (Array.isArray(value.findings) ? value.findings : []).map(parseFinding).filter((f): f is FaultFinding => f !== null)
  // Array.prototype.sort is stable, so findings of one severity keep the checker's order.
  findings.sort((a, b) => FAULT_SEVERITIES.indexOf(a.severity) - FAULT_SEVERITIES.indexOf(b.severity))
  return { checked: parseChecked(value.checked), installed: optionalText(value.installed), findings }
}

/** GET or POST api/diagnostics's body, or null when it isn't a check this page understands. */
export const parseFaultCheck = (value: unknown): FaultCheck | null => {
  if (!isRecord(value) || !STATES.includes(value.state as string)) return null
  const state = value.state as FaultCheckState
  return {
    state,
    since: since(value.since),
    started_at: optionalText(value.started_at),
    finished_at: optionalText(value.finished_at),
    error: optionalText(value.error),
    result: state === 'done' ? parseResult(value.result) : null,
    report: state === 'done' ? optionalText(value.report) : null,
  }
}

/** The report's file name: valve-oil-diagnostics-<UTC yyyymmddThhmmssZ>.txt, from when the check finished. */
export const faultReportName = (finishedAt: string | null, now: Date = new Date()): string => {
  const finished = finishedAt ? new Date(finishedAt) : null
  const when = finished && Number.isFinite(finished.getTime()) ? finished : now
  return `valve-oil-diagnostics-${when.toISOString().replace(/[-:]|\.\d+/g, '')}.txt`
}

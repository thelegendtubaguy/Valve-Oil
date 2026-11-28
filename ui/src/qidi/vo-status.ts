// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/util/vo-status.ts at
// github.com/thelegendtubaguy/fluidd qidi@6405679. Unchanged from the fork; keep it in step. Valve Oil UI passes its
// Moonraker's origin as apiUrl, and no token, as it has no Moonraker login.
//
// Client for Valve Oil's status service (vo-status), which the nginx serving Fluidd on the printer exposes at
// /vo-status/api. It reports the installed Valve Oil release, the printer's QIDI firmware, each board's firmware,
// and the work waiting for the printer, switches automatic updates on or off, sets the hours they run in, and starts
// "update now". nginx authorizes each request through Moonraker, so requests carry Fluidd's Moonraker access token when
// there is one.
//
// Kept free of Vue and the store: Valve Oil UI ports this file and its spec.
//
// Valve Oil UI: the status's klipper block (which Klipper runs, and switching between QIDI's and Valve Oil's) was
// added here first; the Fluidd fork doesn't have it yet.
//
// Valve Oil UI: the few texts this file writes itself, rather than passing on the printer's, are looked up with t()
// when they're used. The service's own text (messages, step labels) is English and is shown as it arrives.
import { t } from '../i18n'

export type VoPendingKind = 'release' | 'firmware' | 'board'

export type VoBoardState =
  | 'current'
  | 'needs_image'
  | 'unrecognized_firmware'
  | 'unrecognized_bootloader'
  | 'held'
  | 'unknown'

export type VoRunState = 'running' | 'succeeded' | 'failed' | 'interrupted'

export type VoJobState = 'running' | 'succeeded' | 'failed'

/** Which run an update result came from: Update now, the automatic update, or the installer run by hand. */
export type VoRunKind = 'update_now' | 'automatic' | 'install' | 'other'
/** What the status service ran for a browser: Update now, or Uninstall. */
export type VoJobKind = 'update_now' | 'uninstall'

/**
 * Where the update window's timezone came from: looked up from the printer's public address by the installer, this or
 * another browser's when a window was set, or set over SSH.
 */
export type VoTimezoneSource = 'lookup' | 'browser' | 'operator'

/** Whole hours automatic updates install in: from start up to, not including, end; past midnight when end < start. */
export interface VoUpdateWindowHours {
  start: number;
  end: number;
}

export interface VoStatusUpdateWindow {
  /** null: any time. */
  hours: VoUpdateWindowHours | null;
  /** The IANA timezone the hours are read in; null reads them in UTC. */
  timezone: string | null;
  timezone_source: VoTimezoneSource | null;
  /** Whether it's within the hours now; true when any time. */
  in_window: boolean;
}

export interface VoStatusBlocker {
  code: string;
  /** Why the work hasn't run, written to follow "It hasn't been installed yet: ". */
  message: string;
}

export interface VoStatusPending {
  kind: VoPendingKind;
  /** The release, the QIDI firmware, or the board image it would install. */
  version: string | null;
  /** What the printer or board has now. */
  current: string | null;
  /** For a board: "thr", "mcu", or "box<N>". */
  board: string | null;
  /** A release that installs the installed one again, as its installed files are missing or changed; version is the installed release. */
  reinstall: boolean;
  /** An experimental build returning explicitly to a numerically older stable release. */
  downgrade: boolean;
  /** Empty when automatic updates are off, or when it can run at the next check. */
  blocked_by: VoStatusBlocker[];
}

export type VoProgressState = 'running' | 'succeeded' | 'failed' | 'interrupted' | 'handed_off'
export type VoProgressOrigin = 'ssh' | 'usb' | 'automatic' | 'firmware_update'

/** An install's steps (installer/runtime/progress.py), however it was started. */
export interface VoStatusProgress {
  /** interrupted: recorded as running, but its process is gone; handed_off: a QIDI firmware update carries it on. */
  state: VoProgressState;
  origin: VoProgressOrigin | null;
  steps: string[];
  /** 1-based place of the current step; 0 before the first. */
  index: number;
  /** What the current step is doing now, such as "restarting Moonraker". */
  detail: string | null;
  from_version: string | null;
  to_version: string | null;
  started_at: string | null;
  finished_at: string | null;
  /** The run's log in Moonraker's logs folder, only when its name is a run log's. */
  log: string | null;
}

const PROGRESS_STATES: VoProgressState[] = ['running', 'succeeded', 'failed', 'interrupted', 'handed_off']
const PROGRESS_ORIGINS: VoProgressOrigin[] = ['ssh', 'usb', 'automatic', 'firmware_update']
/** installer/runtime/run_log.py names a run's log valve-oil-<mode>-<YYYYmmdd-HHMMSS>[-<n>].log. */
export const VO_RUN_LOG_NAME = /^valve-oil-[a-z-]+-\d{8}-\d{6}(-\d+)?\.log$/

const parseProgress = (value: unknown): VoStatusProgress | null => {
  if (!isRecord(value) || !PROGRESS_STATES.includes(value.state as VoProgressState)) {
    return null
  }

  const steps = Array.isArray(value.steps) ? value.steps.filter((step): step is string => typeof step === 'string' && step !== '') : []
  const index = typeof value.index === 'number' && Number.isInteger(value.index) && value.index >= 0 && value.index <= steps.length ? value.index : 0
  const log = optionalText(value.log)

  return {
    state: value.state as VoProgressState,
    origin: PROGRESS_ORIGINS.includes(value.origin as VoProgressOrigin) ? value.origin as VoProgressOrigin : null,
    steps,
    index,
    detail: optionalText(value.detail),
    from_version: optionalText(value.from_version),
    to_version: optionalText(value.to_version),
    started_at: optionalText(value.started_at),
    finished_at: optionalText(value.finished_at),
    log: log != null && VO_RUN_LOG_NAME.test(log) ? log : null
  }
}

export interface VoStatusBoard {
  id: string;
  state: VoBoardState;
  name: string;
  firmware: string | null;
  /** "patched", "stock", "superseded", "qidi", "unrecognized", or null when not read. */
  build: string | null;
  pinned: string | null;
  bootloader: string | null;
  /** A prefilled Valve Oil issue form, only when it is one (see voIssueUrlValid). */
  issue_url: string | null;
  report: string | null;
  /** The printer's own explanation, such as why it wasn't read or how to update it. */
  detail: string | null;
  checked_at: string | null;
  /** Left as it is until the QIDI firmware update is installed; null from a service that doesn't say. */
  waiting_for_firmware_update: boolean | null;
}

export interface VoStatusRun {
  id: string;
  kind: VoRunKind;
  state: VoRunState;
  step: string;
  step_label: string;
  started_at: string | null;
  finished_at: string | null;
  message: string;
  resumed: boolean;
  /** A QIDI firmware update, or Update now continuing one; null from a service that doesn't say. */
  firmware_update: boolean | null;
  /** A failed firmware update that Update now or the installer continues; null from a service that doesn't say. */
  can_continue: boolean | null;
}

export interface VoStatusJob {
  id: string;
  /** update_now from a service that doesn't say. */
  kind: VoJobKind;
  state: VoJobState;
  started_at: string | null;
  finished_at: string | null;
  log: string[];
  message: string;
  /** The run this job started or continued, once it has begun. */
  run_id: string | null;
}

export interface VoStatusUpdateNow {
  /** Whether Update now would start a run now. */
  allowed: boolean;
  /** Every reason it wouldn't, read live by the printer. */
  blocked_by: VoStatusBlocker[];
}

/** A Klipper tree: Valve Oil's (upstream Klipper with QIDI's changes, opt-in) or QIDI's. */
export type VoKlipperTree = 'valve_oil' | 'qidi'

export interface VoKlipperSwitch {
  /** The Klipper a switch chooses: the one not chosen now. */
  to: VoKlipperTree;
  /** Whether POST api/klipper would switch to it now. */
  allowed: boolean;
  /** Every reason it wouldn't, read live (see VO_KLIPPER_SWITCH_BLOCKERS). */
  blocked_by: VoStatusBlocker[];
}

/** Which Klipper runs, and switching between QIDI's and Valve Oil's. */
export interface VoKlipper {
  /** Valve Oil's Klipper is installed and can start. */
  available: boolean;
  /** Why it can't, as the printer says it ("not installed" when it isn't). */
  unavailable_reason: string | null;
  /** The operator's choice; QIDI's unless Valve Oil's was chosen. */
  selected: VoKlipperTree;
  /** null while Klipper starts, or when the printer can't tell. */
  running: VoKlipperTree | null;
  /** Why QIDI's runs although Valve Oil's is chosen. */
  fallback_reason: string | null;
  /** Valve Oil's Klipper's version. */
  version: string | null;
  /** The last switch's Klipper restart failed. */
  restart_error: string | null;
  switch: VoKlipperSwitch;
}

export interface VoStatus {
  updated_at: string | null;
  release: {
    installed: string | null;
    /** The channel of the bundle that installed it, "release" or "dev"; null when the printer doesn't know or say. */
    channel: string | null;
    /** That bundle's short commit. */
    commit: string | null;
  };
  printer: {
    id: string | null;
    name: string | null;
    firmware: string | null;
    target_firmware: string | null;
  };
  auto_update: {
    /** null when the printer didn't say. */
    enabled: boolean | null;
    /** Whether this printer follows the mutable development prerelease feed. */
    experimental: boolean | null;
    /** Verified archive checksum, or the successful manual install's build ID. */
    installed_build: string | null;
    /** Verified checksum from the feed's last successful check. */
    available_build: string | null;
    scheduled: boolean | null;
    /** null from a service without update windows. */
    window: VoStatusUpdateWindow | null;
  };
  check: {
    at: string | null;
    result: 'ok' | 'failed' | null;
    message: string;
    /** When the next automatic check is due. */
    next_at: string | null;
  };
  pending: VoStatusPending[];
  boards: VoStatusBoard[];
  run: VoStatusRun | null;
  /** The install running now, or the last one, step by step; null from a service that doesn't say. */
  progress: VoStatusProgress | null;
  job: VoStatusJob | null;
  busy: boolean;
  printer_busy: boolean;
  /** null from a printer whose service doesn't say; voStatusView then works it out from busy and printer_busy. */
  update_now: VoStatusUpdateNow | null;
  /** Whether Uninstall would start now, in the same shape; null from a service without it. */
  uninstall: VoStatusUpdateNow | null;
  /** The prefilled support issue, for the support bundle to be attached to; null from a service without the bundle. */
  support_issue_url: string | null;
  /** Which Klipper runs; null from a service without Valve Oil's Klipper. */
  klipper: VoKlipper | null;
}

/** Why the waiting work hasn't been installed. */
export type VoWaitingNotice =
  /** Nothing is waiting. */
  | 'none'
  /** Automatic updates are off: the operator has to install it. */
  | 'auto_off'
  /** Automatic updates are on, but something stops it; see reasons. */
  | 'blocked'
  /** Automatic updates are on, and it installs at the next check. */
  | 'next_check'
  /** Automatic updates are on, and it installs at a check in the update window, which it's outside of now. */
  | 'window'

/**
 * What Update now would do: continue a QIDI firmware update that failed part way, install what's waiting, or finish an
 * update that stopped.
 */
export type VoUpdateNowAction = 'continue' | 'install' | 'finish'

export interface VoUpdateNowView {
  /** Offered only when something is waiting, or an update failed or stopped part way. */
  offered: boolean;
  action: VoUpdateNowAction;
  /** Whether it can start now. */
  allowed: boolean;
  /** Why it can't start now, by code (see VO_UPDATE_NOW_BLOCKERS); empty when it can, or when the printer didn't say. */
  blockedBy: VoStatusBlocker[];
}

/**
 * What a board's state means for the printer, beyond its state's name:
 * - unrecognized_bootloader(_box), unrecognized_firmware, unknown: as the state says, for any board or for a Box.
 * - held_box: a Box put back on QIDI's firmware, kept there until the next release.
 * - held_closed_loop: the closed-loop motor drivers, left on their firmware by a firmware update; the touchscreen
 *   updates them.
 * - held: another board kept as it is.
 * - after_firmware: a board that is updated once the QIDI firmware update waiting has run, in the same update.
 * Null when the state's name says enough, or when the printer's own detail explains a held board.
 */
export type VoBoardNote =
  | 'unrecognized_bootloader'
  | 'unrecognized_bootloader_box'
  | 'unrecognized_firmware'
  | 'unknown'
  | 'held_box'
  | 'held_closed_loop'
  | 'held'
  | 'after_firmware'

/** The short state shown beside a board: its state, or one of the per-board cases of a held or waiting board. */
export type VoBoardBadge = VoBoardState | 'after_firmware' | 'touchscreen'

export interface VoBoardView extends VoStatusBoard {
  badge: VoBoardBadge;
  note: VoBoardNote | null;
  /** The version a note names: the QIDI firmware an after_firmware board waits for. */
  noteVersion: string | null;
  /** The printer's detail to show, after the note or alone; null when there is none or the note says the same. */
  shownDetail: string | null;
  /** The issue link to offer, for a board that needs reporting. */
  reportUrl: string | null;
}

export interface VoUpdateView {
  /** The run's id, or the job's when no run is recorded for it; a result is dismissed by it. */
  id: string;
  kind: VoRunKind;
  state: VoRunState;
  /** A QIDI firmware update, or Update now continuing one. */
  firmware: boolean;
  /** A failed firmware update that Update now or the installer continues. */
  canContinue: boolean;
  /** The step a running update has reached, or where a failed or interrupted one stopped. */
  stepLabel: string;
  message: string;
  resumed: boolean;
  startedAt: number | null;
  finishedAt: number | null;
  /** Update now's output, when this update is the one it started. */
  log: string[];
}

export interface VoStatusView {
  sourceChanged: boolean;
  release: string | null;
  /** A dev build's short commit, or '' for a dev build that doesn't say; null for a release. */
  devBuild: string | null;
  printerName: string | null;
  firmware: string | null;
  /** The QIDI firmware the installed release brings the printer to, when it isn't on it already. */
  targetFirmware: string | null;
  autoUpdate: boolean | null;
  /** The update window, or null when the printer doesn't have them. */
  updateWindow: VoStatusUpdateWindow | null;
  waiting: VoStatusPending[];
  notice: VoWaitingNotice;
  /** Every distinct reason the waiting work hasn't run, while automatic updates are on, being outside the window included. */
  reasons: string[];
  /** All that's waiting is installing the installed Valve Oil release again. */
  reinstall: boolean;
  /** Whether waiting work includes QIDI or board firmware, which takes the printer out of use for longer. */
  firmwareWaiting: boolean;
  checkedAt: number | null;
  /** When the next automatic check is due, only while that's still ahead. */
  nextCheckAt: number | null;
  /**
   * The last check's error, or null when it didn't fail. A dev build's check fails until a release publishes what it
   * looks for, so it isn't reported there.
   */
  checkFailed: string | null;
  boards: VoBoardView[];
  updating: boolean;
  update: VoUpdateView | null;
  updateNow: VoUpdateNowView;
  /** The prefilled support issue, offered with the support bundle; null when the printer can't make the bundle. */
  supportIssueUrl: string | null;
}

/** What to do after a status request fails. */
export type VoStatusFailure =
  /** Keep the last status and say the printer is being reconnected to, as while an update restarts services. */
  | 'reconnecting'
  /** Keep the last status quietly and try again. */
  | 'retry'
  /** Say the status isn't available. */
  | 'unavailable'

export const VO_STATUS_SCHEMA = 1

export const VO_STATUS_POLL_BUSY_MS = 2000
export const VO_STATUS_POLL_IDLE_MS = 10000
export const VO_STATUS_POLL_RECONNECT_MS = 3000

/** Failed polls in a row before a status shown is dropped, when no update is running. */
export const VO_STATUS_FAILURES_BEFORE_UNAVAILABLE = 3

/** How long without an answer during an update before the page says the printer is taking unusually long. */
export const VO_STATUS_RECONNECT_PATIENCE_MS = 30 * 60 * 1000

/** The only issue links passed on: this project's issue form, prefilled. */
export const VO_ISSUE_URL_PREFIX = 'https://github.com/thelegendtubaguy/valve-oil/issues/new?'

const MAX_ISSUE_URL = 8000

/**
 * The reasons the printer gives for refusing Update now that are shown in plain words; any other is shown as the printer
 * says it, as recovery_required is, whose message names what to run.
 */
export const VO_UPDATE_NOW_BLOCKERS: readonly string[] = [
  'run_in_progress',
  'installer_busy',
  'printing',
  'heaters_on',
  'printer_state_unknown',
  'installer_missing'
]

// What the printer says for each, for a printer that doesn't send update_now.
const RUN_IN_PROGRESS_CODE = 'run_in_progress'
const runInProgress = (): VoStatusBlocker => ({ code: RUN_IN_PROGRESS_CODE, message: t('updates.status.run_in_progress') })
const printing = (): VoStatusBlocker => ({ code: 'printing', message: t('updates.status.printing') })

// Fallback for a service without run.firmware_update: the steps only a QIDI firmware update reports
// (installer/runtime/firmware_update.py STEPS), and Update now's "resume", which continues one. Before stop_services
// nothing has changed, and a failure there isn't kept.
const FIRMWARE_STEPS: readonly string[] = ['stage', 'closed_loop', 'stop_services', 'soc', 'update_folder', 'finish', 'resume']

/**
 * The reasons the printer gives for refusing a Klipper switch that are shown in plain words; any other is shown as the
 * printer says it, as recovery_required and valve_oil_unavailable are, whose messages say what to do.
 */
export const VO_KLIPPER_SWITCH_BLOCKERS: readonly string[] = [
  'printing',
  'printer_state_unknown',
  'run_in_progress',
  'installer_busy',
  'firmware_update_pending'
]

/**
 * What the Klipper switch shows first:
 * - off: QIDI's Klipper is chosen and runs (the default);
 * - on: Valve Oil's Klipper is chosen and runs;
 * - fallback: Valve Oil's is chosen, but QIDI's runs, for fallback_reason;
 * - starting: Klipper is starting, so which runs isn't known yet;
 * - leaving: QIDI's is chosen, but Valve Oil's still runs, until Klipper restarts.
 */
export type VoKlipperState = 'off' | 'on' | 'fallback' | 'starting' | 'leaving'

export const voKlipperState = (klipper: VoKlipper): VoKlipperState => {
  if (klipper.running == null) {
    return 'starting'
  }

  if (klipper.selected === 'valve_oil') {
    return klipper.running === 'valve_oil' ? 'on' : 'fallback'
  }

  return klipper.running === 'qidi' ? 'off' : 'leaving'
}

/** The blocker a waiting item carries while automatic updates wait for the update window. */
export const VO_OUTSIDE_WINDOW = 'outside_window'

const PENDING_KINDS: readonly string[] = ['release', 'firmware', 'board']
const TIMEZONE_SOURCES: readonly string[] = ['lookup', 'browser', 'operator']
const BOARD_STATES: readonly string[] = ['current', 'needs_image', 'unrecognized_firmware', 'unrecognized_bootloader', 'held', 'unknown']
const RUN_STATES: readonly string[] = ['running', 'succeeded', 'failed', 'interrupted']
const RUN_KINDS: readonly string[] = ['update_now', 'automatic', 'install']
const JOB_STATES: readonly string[] = ['running', 'succeeded', 'failed']
const boardNames = (): Record<string, string> => ({ thr: 'THR', mcu: t('updates.boards.mainboard'), closed_loop: t('updates.boards.closed_loop') })

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' &&
  value !== null &&
  !Array.isArray(value)
)

const record = (value: unknown): Record<string, unknown> => isRecord(value) ? value : {}

const records = (value: unknown): Record<string, unknown>[] => Array.isArray(value) ? value.filter(isRecord) : []

const text = (value: unknown): string => typeof value === 'string' ? value : ''

const optionalText = (value: unknown): string | null => typeof value === 'string' && value !== '' ? value : null

const optionalBoolean = (value: unknown): boolean | null => typeof value === 'boolean' ? value : null

/** The printer's text as a sentence: capitalized, ending in a full stop. */
export const voSentence = (value: string): string => {
  const sentence = value.trim()

  if (sentence === '') {
    return ''
  }

  return sentence.charAt(0).toUpperCase() + sentence.slice(1) + (/[.!?]$/.test(sentence) ? '' : '.')
}

/** An hour of the day as the update window shows it: 02:00. */
export const voHourLabel = (hour: number): string => `${String(hour).padStart(2, '0')}:00`

/** An update window's hours as the printer words them: 02:00–05:00. */
export const voUpdateWindowLabel = (hours: VoUpdateWindowHours): string => `${voHourLabel(hours.start)}–${voHourLabel(hours.end)}`

/** Whether hours can be saved as an update window: whole hours of the day, starting and ending at different hours. */
export const voUpdateWindowValid = (hours: VoUpdateWindowHours): boolean => {
  const hour = (value: number) => Number.isInteger(value) && value >= 0 && value <= 23

  return hour(hours.start) && hour(hours.end) && hours.start !== hours.end
}

/** This browser's IANA timezone, or null when it doesn't say. */
export const voBrowserTimezone = (): string | null => {
  try {
    return optionalText(Intl.DateTimeFormat().resolvedOptions().timeZone)
  } catch {
    return null
  }
}

const time = (value: string | null): number | null => {
  const parsed = value ? Date.parse(value) : NaN

  return Number.isFinite(parsed) ? parsed : null
}

/**
 * The status API base, on the nginx that served this Fluidd. Only used while Fluidd talks to that same printer's
 * Moonraker, so the access token and the status shown both belong to the printer in use.
 */
export const voStatusApiBase = (apiUrl: string, location: Pick<Location, 'origin' | 'hostname'>): string | null => {
  if (!apiUrl) {
    return null
  }

  try {
    if (new URL(apiUrl).hostname !== location.hostname) {
      return null
    }
  } catch {
    return null
  }

  return `${location.origin}/vo-status/api`
}

/** Whether a link is a prefilled Valve Oil issue form, the only kind a board's issue link may open. */
export const voIssueUrlValid = (value: unknown): value is string => {
  if (typeof value !== 'string' || !value.startsWith(VO_ISSUE_URL_PREFIX) || value.length > MAX_ISSUE_URL) {
    return false
  }

  try {
    const url = new URL(value)

    return url.origin === 'https://github.com' && url.pathname === '/thelegendtubaguy/valve-oil/issues/new'
  } catch {
    return false
  }
}

/** A board's display name when the printer doesn't send one: THR, Mainboard, Closed-loop drivers, or Box N. */
export const voBoardName = (id: string): string => {
  const box = /^box(\d+)$/.exec(id)

  return box ? `Box ${box[1]}` : boardNames()[id] ?? id
}

/** Whether a board is a USB Box. */
export const voBoardIsBox = (id: string | null): boolean => id != null && /^box\d+$/.test(id)

const parseBlockers = (value: unknown): VoStatusBlocker[] => records(value)
  .filter(blocker => optionalText(blocker.code) != null)
  .map(blocker => ({
    code: text(blocker.code),
    message: optionalText(blocker.message) ?? text(blocker.code)
  }))

/**
 * The update window, or null from a service without them, which leaves the window out of auto_update, or one that sends
 * hours this page can't read.
 */
const parseUpdateWindow = (autoUpdate: Record<string, unknown>): VoStatusUpdateWindow | null => {
  if (!('window' in autoUpdate)) {
    return null
  }

  let hours: VoUpdateWindowHours | null = null

  if (autoUpdate.window != null) {
    const window = record(autoUpdate.window)

    if (typeof window.start !== 'number' || typeof window.end !== 'number') {
      return null
    }

    hours = { start: window.start, end: window.end }

    if (!voUpdateWindowValid(hours)) {
      return null
    }
  }

  const timezone = optionalText(autoUpdate.timezone)
  const source = autoUpdate.timezone_source

  return {
    hours,
    timezone,
    timezone_source: timezone != null && typeof source === 'string' && TIMEZONE_SOURCES.includes(source)
      ? source as VoTimezoneSource
      : null,
    in_window: hours == null || autoUpdate.in_window !== false
  }
}

const parsePending = (value: Record<string, unknown>): VoStatusPending | null => {
  if (typeof value.kind !== 'string' || !PENDING_KINDS.includes(value.kind)) {
    return null
  }

  return {
    kind: value.kind as VoPendingKind,
    version: optionalText(value.version),
    current: optionalText(value.current),
    board: optionalText(value.board),
    reinstall: value.kind === 'release' && value.reinstall === true,
    downgrade: value.kind === 'release' && value.downgrade === true,
    blocked_by: parseBlockers(value.blocked_by)
  }
}

const KLIPPER_TREES: readonly string[] = ['valve_oil', 'qidi']

const klipperTree = (value: unknown): VoKlipperTree | null => KLIPPER_TREES.includes(value as string) ? value as VoKlipperTree : null

const parseKlipper = (value: unknown): VoKlipper | null => {
  const selected = isRecord(value) ? klipperTree(value.selected) : null

  if (!isRecord(value) || selected == null) {
    return null
  }

  const change = record(value.switch)
  const blockedBy = parseBlockers(change.blocked_by)

  return {
    available: value.available === true,
    unavailable_reason: optionalText(value.unavailable_reason),
    selected,
    running: klipperTree(value.running),
    fallback_reason: optionalText(value.fallback_reason),
    version: optionalText(value.version),
    restart_error: optionalText(value.restart_error),
    switch: {
      to: klipperTree(change.to) ?? (selected === 'valve_oil' ? 'qidi' : 'valve_oil'),
      // A refusal with reasons is never allowed, whatever else it says.
      allowed: change.allowed === true && blockedBy.length === 0,
      blocked_by: blockedBy
    }
  }
}

/** Reads the Klipper block a POST api/klipper answers with. */
export const parseVoKlipperAnswer = (value: unknown): VoKlipper | null => (
  isRecord(value)
    ? parseKlipper(value.klipper)
    : null
)

const parseUpdateNow = (value: unknown): VoStatusUpdateNow | null => (
  isRecord(value) && typeof value.allowed === 'boolean'
    ? { allowed: value.allowed, blocked_by: parseBlockers(value.blocked_by) }
    : null
)

const parseBoard = (value: Record<string, unknown>): VoStatusBoard | null => {
  const id = optionalText(value.id)

  if (id == null) {
    return null
  }

  return {
    id,
    state: typeof value.state === 'string' && BOARD_STATES.includes(value.state)
      ? value.state as VoBoardState
      : 'unknown',
    name: optionalText(value.name) ?? voBoardName(id),
    firmware: optionalText(value.firmware),
    build: optionalText(value.build),
    pinned: optionalText(value.pinned),
    bootloader: optionalText(value.bootloader),
    issue_url: voIssueUrlValid(value.issue_url) ? value.issue_url : null,
    report: optionalText(value.report),
    detail: optionalText(value.detail),
    checked_at: optionalText(value.checked_at),
    waiting_for_firmware_update: optionalBoolean(value.waiting_for_firmware_update)
  }
}

const parseRun = (value: unknown): VoStatusRun | null => {
  if (
    !isRecord(value) ||
    optionalText(value.id) == null ||
    typeof value.state !== 'string' ||
    !RUN_STATES.includes(value.state)
  ) {
    return null
  }

  return {
    id: text(value.id),
    kind: typeof value.kind === 'string' && RUN_KINDS.includes(value.kind) ? value.kind as VoRunKind : 'other',
    state: value.state as VoRunState,
    step: text(value.step),
    step_label: text(value.step_label),
    started_at: optionalText(value.started_at),
    finished_at: optionalText(value.finished_at),
    message: text(value.message),
    resumed: value.resumed === true,
    firmware_update: optionalBoolean(value.firmware_update),
    can_continue: optionalBoolean(value.can_continue)
  }
}

const parseJob = (value: unknown): VoStatusJob | null => {
  if (
    !isRecord(value) ||
    optionalText(value.id) == null ||
    typeof value.state !== 'string' ||
    !JOB_STATES.includes(value.state)
  ) {
    return null
  }

  return {
    id: text(value.id),
    kind: value.kind === 'uninstall' ? 'uninstall' : 'update_now',
    state: value.state as VoJobState,
    started_at: optionalText(value.started_at),
    finished_at: optionalText(value.finished_at),
    log: Array.isArray(value.log)
      ? value.log.filter((line): line is string => typeof line === 'string')
      : [],
    message: text(value.message),
    run_id: optionalText(value.run_id)
  }
}

/**
 * Reads the status, or null when the response isn't one this page understands, as from a printer without the service
 * or with a schema it doesn't know. Missing or null fields read as unknown, and entries it can't read are skipped.
 */
export const parseVoStatus = (value: unknown): VoStatus | null => {
  if (!isRecord(value) || value.schema !== VO_STATUS_SCHEMA) {
    return null
  }

  const release = record(value.release)
  const printer = record(value.printer)
  const autoUpdate = record(value.auto_update)
  const check = record(value.check)
  const run = parseRun(value.run)
  const progress = parseProgress(value.progress)
  const job = parseJob(value.job)
  const support = record(value.support)
  const seen = new Set<string>()

  return {
    updated_at: optionalText(value.updated_at),
    release: {
      installed: optionalText(release.installed),
      channel: optionalText(release.channel),
      commit: optionalText(release.commit)
    },
    printer: {
      id: optionalText(printer.id),
      name: optionalText(printer.name),
      firmware: optionalText(printer.firmware),
      target_firmware: optionalText(printer.target_firmware)
    },
    auto_update: {
      enabled: optionalBoolean(autoUpdate.enabled),
      experimental: optionalBoolean(autoUpdate.experimental),
      installed_build: optionalText(autoUpdate.installed_build),
      available_build: optionalText(autoUpdate.available_build),
      scheduled: optionalBoolean(autoUpdate.scheduled),
      window: parseUpdateWindow(autoUpdate)
    },
    check: {
      at: optionalText(check.at),
      result: check.result === 'ok' || check.result === 'failed' ? check.result : null,
      message: text(check.message),
      next_at: optionalText(check.next_at)
    },
    pending: records(value.pending)
      .map(parsePending)
      .filter((item): item is VoStatusPending => item != null),
    boards: records(value.boards)
      .map(parseBoard)
      .filter((board): board is VoStatusBoard => {
        if (board == null || seen.has(board.id)) {
          return false
        }

        seen.add(board.id)

        return true
      }),
    run,
    progress,
    job,
    busy: value.busy === true || run?.state === 'running' || job?.state === 'running' || progress?.state === 'running',
    printer_busy: value.printer_busy === true,
    update_now: parseUpdateNow(value.update_now),
    uninstall: parseUpdateNow(value.uninstall),
    support_issue_url: voIssueUrlValid(support.issue_url) ? support.issue_url : null,
    klipper: parseKlipper(value.klipper)
  }
}

/** Reads the job an Update now or Uninstall request started. */
export const parseVoStatusJob = (value: unknown): VoStatusJob | null => (
  isRecord(value)
    ? parseJob(value.job)
    : null
)

/**
 * Whether a run is a QIDI firmware update, as the service says. Fallback for a service that doesn't say: read from the
 * step, the steps only a firmware update has, its "install" step (which names the QIDI firmware), and its "boards" step
 * unless that's the update check's own board update, whose failures don't send the operator to the installer.
 */
const isFirmwareRun = (run: VoStatusRun): boolean => run.firmware_update ?? (
  FIRMWARE_STEPS.includes(run.step) ||
  (run.step === 'install' && run.step_label.includes('QIDI firmware')) ||
  (run.step === 'boards' && (run.kind !== 'automatic' || run.message.includes('./install.sh')))
)

/**
 * Whether a failed firmware update is kept for Update now or the installer to continue, as the service says. Fallback
 * for a service that doesn't say: any failed firmware update, as a failure from stop_services on is kept.
 */
const canContinueRun = (run: VoStatusRun): boolean => run.state === 'failed' && (run.can_continue ?? isFirmwareRun(run))

/**
 * The update to show: one running, or the last one's result. The run record says what an update is doing and how it
 * ended; Update now's job adds its output, and stands in for the run until the run record is written. The job names
 * its run once the run has begun; before that, or for an older service, their times match them.
 */
const updateView = (status: VoStatus): VoUpdateView | null => {
  const { run } = status
  // An uninstall isn't an update; it has a screen of its own.
  const job = status.job?.kind === 'uninstall' ? null : status.job
  const jobStarted = time(job?.started_at ?? null)
  const jobFinished = time(job?.finished_at ?? null)
  const runStarted = time(run?.started_at ?? null)
  const named = run != null && job?.run_id === run.id
  // A run that started before Update now's job is an older one; the job is the newer update.
  const current = run && (named || !(job && jobStarted != null && runStarted != null && runStarted < jobStarted)) ? run : null
  // The job's output is this update's when the job names the run, or the run started while the job was running.
  const ownLog = job != null && (
    named ||
    current == null ||
    runStarted == null ||
    jobStarted == null ||
    (runStarted >= jobStarted && (jobFinished == null || runStarted <= jobFinished))
  )

  if (current) {
    const state: VoRunState = status.busy ? 'running' : current.state
    // A run that has recorded success has no step left, though its process may still be finishing.
    const stopped = state !== 'running' && (current.state === 'interrupted' || current.state === 'failed')

    return {
      id: current.id,
      kind: current.kind,
      state,
      firmware: isFirmwareRun(current),
      canContinue: state === 'failed' && canContinueRun(current),
      stepLabel: current.state === 'running' || stopped ? current.step_label : '',
      message: current.message || (ownLog && job?.state === 'failed' ? job.message : ''),
      resumed: current.resumed,
      startedAt: runStarted,
      finishedAt: state === 'running' ? null : time(current.finished_at),
      log: ownLog && job ? job.log : []
    }
  }

  if (job) {
    const state: VoRunState = status.busy ? 'running' : job.state

    return {
      id: job.id,
      kind: 'update_now',
      state,
      firmware: false,
      canContinue: false,
      stepLabel: '',
      message: state === 'failed' ? job.message : '',
      resumed: false,
      startedAt: jobStarted,
      finishedAt: state === 'running' ? null : jobFinished,
      log: job.log
    }
  }

  return null
}

const updateSourceChanged = (status: VoStatus): boolean =>
  status.auto_update.experimental != null && status.auto_update.experimental !== (status.release.channel === 'dev')

/** What Update now would do, and whether it can start; the printer's own answer, or for an older service, busy and printing. */
const updateNowView = (status: VoStatus, update: VoUpdateView | null): VoUpdateNowView => {
  const waiting = status.pending.length > 0 || updateSourceChanged(status)
  const unfinished = update?.state === 'failed' || update?.state === 'interrupted'
  const live = status.update_now
  const blockedBy = live
    ? [...live.blocked_by]
    : [...(status.busy ? [runInProgress()] : []), ...(status.printer_busy ? [printing()] : [])]

  // An update this page knows is running, as just after Update now, blocks another whatever the last answer said.
  if (status.busy && !blockedBy.some(blocker => blocker.code === RUN_IN_PROGRESS_CODE)) {
    blockedBy.unshift(runInProgress())
  }

  let action: VoUpdateNowAction = 'finish'

  if (update?.canContinue) {
    action = 'continue'
  } else if (waiting) {
    action = 'install'
  }

  return {
    offered: waiting || unfinished,
    action,
    allowed: (live?.allowed ?? true) && blockedBy.length === 0,
    blockedBy
  }
}

/** A board with what its state means for this printer; see VoBoardNote. */
const boardView = (board: VoStatusBoard, status: VoStatus): VoBoardView => {
  const box = voBoardIsBox(board.id)
  const firmware = status.pending.find(item => item.kind === 'firmware')
  let badge: VoBoardBadge = board.state
  let note: VoBoardNote | null = null

  switch (board.state) {
    case 'unrecognized_bootloader':
      note = box ? 'unrecognized_bootloader_box' : 'unrecognized_bootloader'
      break
    case 'unrecognized_firmware':
    case 'unknown':
      note = board.state
      break
    case 'held':
      if (box) {
        note = 'held_box'
      } else if (board.id === 'closed_loop') {
        badge = 'touchscreen'
        // The printer's detail names the drivers' firmware and what to do; without it, say what it would.
        note = board.detail ? null : 'held_closed_loop'
      } else {
        note = board.detail ? null : 'held'
      }
      break
  }

  // As the service says; fallback for a service that doesn't say: a board waiting with no update of its own while a
  // QIDI firmware update waits gets its firmware in that update, once it has run.
  const afterFirmware = board.waiting_for_firmware_update ?? (
    board.state === 'needs_image' &&
    firmware != null &&
    !status.pending.some(item => item.kind === 'board' && item.board === board.id)
  )

  if (afterFirmware) {
    badge = 'after_firmware'
    note = 'after_firmware'
  }

  return {
    ...board,
    badge,
    note,
    noteVersion: note === 'after_firmware' ? firmware?.version ?? status.printer.target_firmware : null,
    shownDetail: note === 'after_firmware' ? null : board.detail,
    // A support issue for a board this release doesn't recognize, or for closed-loop drivers left on their firmware.
    reportUrl: board.state === 'unrecognized_bootloader' || board.state === 'unrecognized_firmware' || board.id === 'closed_loop'
      ? board.issue_url
      : null
  }
}

/** What the Valve Oil section shows for a status, at `now`. */
export const voStatusView = (status: VoStatus, now: number = Date.now()): VoStatusView => {
  const autoUpdate = status.auto_update.enabled
  const updateWindow = status.auto_update.window
  const waiting = status.pending
  const reasons: string[] = []
  let otherReasons = false
  let outsideWindow = updateWindow?.hours != null && !updateWindow.in_window

  if (autoUpdate === true) {
    for (const item of waiting) {
      for (const blocker of item.blocked_by) {
        if (blocker.code === VO_OUTSIDE_WINDOW) {
          outsideWindow = true
        } else {
          otherReasons = true
        }

        if (blocker.message && !reasons.includes(blocker.message)) {
          reasons.push(blocker.message)
        }
      }
    }
  }

  let notice: VoWaitingNotice = 'none'

  if (waiting.length > 0) {
    if (autoUpdate !== true) {
      notice = 'auto_off'
    } else if (otherReasons) {
      notice = 'blocked'
    } else if (outsideWindow && updateWindow?.hours != null) {
      // Only the window stops it, and the page knows the window to name.
      notice = 'window'
    } else if (reasons.length > 0) {
      notice = 'blocked'
    } else {
      notice = 'next_check'
    }
  }

  const updating = status.busy
  const update = updateView(status)
  const nextCheckAt = time(status.check.next_at)
  const { firmware, target_firmware: target } = status.printer
  const dev = status.release.channel === 'dev'

  return {
    sourceChanged: updateSourceChanged(status),
    release: status.release.installed,
    devBuild: dev ? status.release.commit ?? '' : null,
    printerName: status.printer.name,
    firmware,
    targetFirmware: target && target !== firmware ? target : null,
    autoUpdate,
    updateWindow,
    waiting,
    notice,
    reasons,
    reinstall: waiting.length > 0 && waiting.every(item => item.reinstall),
    firmwareWaiting: waiting.some(item => item.kind !== 'release'),
    checkedAt: time(status.check.at),
    nextCheckAt: nextCheckAt != null && nextCheckAt > now ? nextCheckAt : null,
    checkFailed: status.check.result === 'failed' && (!dev || status.auto_update.experimental != null) ? status.check.message : null,
    boards: status.boards.map(board => boardView(board, status)),
    updating,
    update,
    updateNow: updateNowView(status, update),
    supportIssueUrl: status.support_issue_url
  }
}

/** How long a finished update's success is shown; a failed or stopped one is shown until dismissed or replaced. */
export const VO_UPDATE_SUCCESS_SHOWN_MS = 24 * 60 * 60 * 1000

/** The update to show, at `now`: one running, or a result not dismissed, and a success only for a day after it finished. */
export const voShownUpdate = (update: VoUpdateView | null, dismissed: string[], now: number = Date.now()): VoUpdateView | null => {
  if (update == null || update.state === 'running') {
    return update
  }

  if (dismissed.includes(update.id)) {
    return null
  }

  return update.state === 'succeeded' && update.finishedAt != null && now - update.finishedAt > VO_UPDATE_SUCCESS_SHOWN_MS
    ? null
    : update
}

/**
 * What to do after a status request fails. While an update runs, or was just started, the printer restarts the
 * services that answer, so failures mean "reconnecting" for as long as they last. Otherwise a status already shown is
 * kept through a few failures before it's dropped.
 */
export const voStatusPollFailure = (options: { hasStatus: boolean, failures: number, updating: boolean }): VoStatusFailure => {
  if (options.updating) {
    return 'reconnecting'
  }

  return options.hasStatus && options.failures < VO_STATUS_FAILURES_BEFORE_UNAVAILABLE
    ? 'retry'
    : 'unavailable'
}

export const voStatusPollInterval = (status: VoStatus | null, reconnecting = false): number => {
  if (reconnecting) {
    return VO_STATUS_POLL_RECONNECT_MS
  }

  return status?.busy
    ? VO_STATUS_POLL_BUSY_MS
    : VO_STATUS_POLL_IDLE_MS
}

export class VoStatusRequestError extends Error {
  readonly status: number
  /** Why Update now or a Klipper switch was refused (409), as the printer listed it. */
  readonly blockedBy: VoStatusBlocker[]

  constructor (status: number, message: string, blockedBy: VoStatusBlocker[] = []) {
    super(message)
    this.name = 'VoStatusRequestError'
    this.status = status
    this.blockedBy = blockedBy
  }
}

export interface VoStatusRequestOptions {
  method?: 'GET' | 'POST';
  token?: string | null;
  signal?: AbortSignal;
  /** A POST's JSON body, such as {"enabled": true} or {"window": {"start": 2, "end": 5}}. */
  body?: Record<string, unknown>;
}

/**
 * Calls the status API and returns its JSON body. Throws VoStatusRequestError with the server's error text, and the
 * reasons it lists, on an error status, and with status 0 when the printer can't be reached or doesn't answer with JSON.
 * POSTs carry the header and body the service requires of them.
 */
export const voStatusRequest = async (base: string, path: string, options: VoStatusRequestOptions = {}): Promise<unknown> => {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = {
    Accept: 'application/json'
  }

  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`
  }

  if (method === 'POST') {
    headers['X-Valve-Oil'] = 'status'
    headers['Content-Type'] = 'application/json'
  }

  let response: Response

  try {
    response = await fetch(`${base}${path}`, {
      method,
      headers,
      body: method === 'POST' ? JSON.stringify(options.body ?? {}) : undefined,
      cache: 'no-store',
      credentials: 'same-origin',
      signal: options.signal
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw e
    }

    throw new VoStatusRequestError(0, e instanceof Error ? e.message : String(e))
  }

  let body: unknown = null

  try {
    body = await response.json()
  } catch {
    body = null
  }

  if (!response.ok) {
    const error = isRecord(body) ? text(body.error) : ''

    throw new VoStatusRequestError(
      response.status,
      error || `${response.status} ${response.statusText}`.trim(),
      isRecord(body) ? parseBlockers(body.blocked_by) : []
    )
  }

  if (body === null) {
    throw new VoStatusRequestError(0, t('updates.status.invalid_response'))
  }

  return body
}

/** The support bundle's file name, as the printer names it, or a name of the same shape. */
const supportBundleName = (disposition: string | null): string => {
  const name = /filename="([\w.-]+)"/.exec(disposition ?? '')?.[1]

  return name ?? `valve-oil-support-${new Date().toISOString().replace(/[-:]|\.\d+/g, '')}.zip`
}

/**
 * Fetches the support bundle (GET api/support-bundle): the printer's logs and Valve Oil's status, zipped. Throws
 * VoStatusRequestError as voStatusRequest does.
 */
export const voSupportBundle = async (base: string, options: { token?: string | null } = {}): Promise<{ blob: Blob, name: string }> => {
  let response: Response

  try {
    response = await fetch(`${base}/support-bundle`, {
      headers: options.token ? { Authorization: `Bearer ${options.token}` } : {},
      cache: 'no-store',
      credentials: 'same-origin'
    })
  } catch (e) {
    throw new VoStatusRequestError(0, e instanceof Error ? e.message : String(e))
  }

  if (!response.ok) {
    let error = ''

    try {
      const body: unknown = await response.json()

      error = isRecord(body) ? text(body.error) : ''
    } catch {
      error = ''
    }

    throw new VoStatusRequestError(response.status, error || `${response.status} ${response.statusText}`.trim())
  }

  return { blob: await response.blob(), name: supportBundleName(response.headers.get('Content-Disposition')) }
}

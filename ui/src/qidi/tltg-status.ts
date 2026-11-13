// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/util/tltg-status.ts on
// github.com/thelegendtubaguy/fluidd qidi, ported with the section before the fork release that adds it. Keep behavior
// in step with the fork. Unchanged from the fork; Valve Oil UI passes its Moonraker's origin as apiUrl, and no token,
// as it has no Moonraker login.
//
// Client for Valve Oil's status service (tltg-status), which the nginx serving Fluidd on the printer exposes at
// /tltg-status/api. It reports the installed Valve Oil release, the printer's QIDI firmware, each board's firmware,
// and the work waiting for the printer, switches automatic updates on or off, and starts "update now". nginx
// authorizes each request through Moonraker, so requests carry Fluidd's Moonraker access token when there is one.
//
// Kept free of Vue and the store: Valve Oil UI ports this file and its spec.

export type TltgPendingKind = 'release' | 'firmware' | 'board'

export type TltgBoardState =
  | 'current'
  | 'needs_image'
  | 'unrecognized_firmware'
  | 'unrecognized_bootloader'
  | 'held'
  | 'unknown'

export type TltgRunState = 'running' | 'succeeded' | 'failed' | 'interrupted'

export type TltgJobState = 'running' | 'succeeded' | 'failed'

/** Which run an update result came from: Update now, the automatic update, or the installer run by hand. */
export type TltgRunKind = 'update_now' | 'automatic' | 'install' | 'other'

export interface TltgStatusBlocker {
  code: string;
  /** Why the work hasn't run, written to follow "It hasn't been installed yet: ". */
  message: string;
}

export interface TltgStatusPending {
  kind: TltgPendingKind;
  /** The release, the QIDI firmware, or the board image it would install. */
  version: string | null;
  /** What the printer or board has now. */
  current: string | null;
  /** For a board: "thr", "mcu", or "box<N>". */
  board: string | null;
  /** A release that installs the installed one again, as its installed files are missing or changed; version is the installed release. */
  reinstall: boolean;
  /** Empty when automatic updates are off, or when it can run at the next check. */
  blocked_by: TltgStatusBlocker[];
}

export interface TltgStatusBoard {
  id: string;
  state: TltgBoardState;
  name: string;
  firmware: string | null;
  /** "patched", "stock", "superseded", "qidi", "unrecognized", or null when not read. */
  build: string | null;
  pinned: string | null;
  bootloader: string | null;
  /** A prefilled Valve Oil issue form, only when it is one (see tltgIssueUrlValid). */
  issue_url: string | null;
  report: string | null;
  /** The printer's own explanation, such as why it wasn't read or how to update it. */
  detail: string | null;
  checked_at: string | null;
  /** Left as it is until the QIDI firmware update is installed; null from a service that doesn't say. */
  waiting_for_firmware_update: boolean | null;
}

export interface TltgStatusRun {
  id: string;
  kind: TltgRunKind;
  state: TltgRunState;
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

export interface TltgStatusJob {
  id: string;
  state: TltgJobState;
  started_at: string | null;
  finished_at: string | null;
  log: string[];
  message: string;
  /** The run this job started or continued, once it has begun. */
  run_id: string | null;
}

export interface TltgStatusUpdateNow {
  /** Whether Update now would start a run now. */
  allowed: boolean;
  /** Every reason it wouldn't, read live by the printer. */
  blocked_by: TltgStatusBlocker[];
}

export interface TltgStatus {
  updated_at: string | null;
  release: {
    installed: string | null;
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
    scheduled: boolean | null;
  };
  check: {
    at: string | null;
    result: 'ok' | 'failed' | null;
    message: string;
    /** When the next automatic check is due. */
    next_at: string | null;
  };
  pending: TltgStatusPending[];
  boards: TltgStatusBoard[];
  run: TltgStatusRun | null;
  job: TltgStatusJob | null;
  busy: boolean;
  printer_busy: boolean;
  /** null from a printer whose service doesn't say; tltgStatusView then works it out from busy and printer_busy. */
  update_now: TltgStatusUpdateNow | null;
}

/** Why the waiting work hasn't been installed. */
export type TltgWaitingNotice =
  /** Nothing is waiting. */
  | 'none'
  /** Automatic updates are off: the operator has to install it. */
  | 'auto_off'
  /** Automatic updates are on, but something stops it; see reasons. */
  | 'blocked'
  /** Automatic updates are on, and it installs at the next check. */
  | 'next_check'

/**
 * What Update now would do: continue a QIDI firmware update that failed part way, install what's waiting, or finish an
 * update that stopped.
 */
export type TltgUpdateNowAction = 'continue' | 'install' | 'finish'

export interface TltgUpdateNowView {
  /** Offered only when something is waiting, or an update failed or stopped part way. */
  offered: boolean;
  action: TltgUpdateNowAction;
  /** Whether it can start now. */
  allowed: boolean;
  /** Why it can't start now, by code (see TLTG_UPDATE_NOW_BLOCKERS); empty when it can, or when the printer didn't say. */
  blockedBy: TltgStatusBlocker[];
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
export type TltgBoardNote =
  | 'unrecognized_bootloader'
  | 'unrecognized_bootloader_box'
  | 'unrecognized_firmware'
  | 'unknown'
  | 'held_box'
  | 'held_closed_loop'
  | 'held'
  | 'after_firmware'

/** The short state shown beside a board: its state, or one of the per-board cases of a held or waiting board. */
export type TltgBoardBadge = TltgBoardState | 'after_firmware' | 'touchscreen'

export interface TltgBoardView extends TltgStatusBoard {
  badge: TltgBoardBadge;
  note: TltgBoardNote | null;
  /** The version a note names: the QIDI firmware an after_firmware board waits for. */
  noteVersion: string | null;
  /** The printer's detail to show, after the note or alone; null when there is none or the note says the same. */
  shownDetail: string | null;
  /** The issue link to offer, for a board that needs reporting. */
  reportUrl: string | null;
}

export interface TltgUpdateView {
  /** The run's id, or the job's when no run is recorded for it; a result is dismissed by it. */
  id: string;
  kind: TltgRunKind;
  state: TltgRunState;
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

export interface TltgStatusView {
  release: string | null;
  printerName: string | null;
  firmware: string | null;
  /** The QIDI firmware the installed release brings the printer to, when it isn't on it already. */
  targetFirmware: string | null;
  autoUpdate: boolean | null;
  waiting: TltgStatusPending[];
  notice: TltgWaitingNotice;
  /** Every distinct reason the waiting work hasn't run, while automatic updates are on. */
  reasons: string[];
  /** All that's waiting is installing the installed Valve Oil release again. */
  reinstall: boolean;
  /** Whether waiting work includes QIDI or board firmware, which takes the printer out of use for longer. */
  firmwareWaiting: boolean;
  checkedAt: number | null;
  /** When the next automatic check is due, only while that's still ahead. */
  nextCheckAt: number | null;
  /** The last check's error, or null when it didn't fail. */
  checkFailed: string | null;
  boards: TltgBoardView[];
  updating: boolean;
  update: TltgUpdateView | null;
  updateNow: TltgUpdateNowView;
}

/** What to do after a status request fails. */
export type TltgStatusFailure =
  /** Keep the last status and say the printer is being reconnected to, as while an update restarts services. */
  | 'reconnecting'
  /** Keep the last status quietly and try again. */
  | 'retry'
  /** Say the status isn't available. */
  | 'unavailable'

export const TLTG_STATUS_SCHEMA = 1

export const TLTG_STATUS_POLL_BUSY_MS = 2000
export const TLTG_STATUS_POLL_IDLE_MS = 10000
export const TLTG_STATUS_POLL_RECONNECT_MS = 3000

/** Failed polls in a row before a status shown is dropped, when no update is running. */
export const TLTG_STATUS_FAILURES_BEFORE_UNAVAILABLE = 3

/** How long without an answer during an update before the page says the printer is taking unusually long. */
export const TLTG_STATUS_RECONNECT_PATIENCE_MS = 30 * 60 * 1000

/** The only issue links passed on: this project's issue form, prefilled. */
export const TLTG_ISSUE_URL_PREFIX = 'https://github.com/thelegendtubaguy/valve-oil/issues/new?'

const MAX_ISSUE_URL = 8000

/**
 * The reasons the printer gives for refusing Update now that are shown in plain words; any other is shown as the printer
 * says it, as recovery_required is, whose message names what to run.
 */
export const TLTG_UPDATE_NOW_BLOCKERS: readonly string[] = [
  'run_in_progress',
  'installer_busy',
  'printing',
  'heaters_on',
  'printer_state_unknown',
  'installer_missing'
]

// What the printer says for each, for a printer that doesn't send update_now.
const RUN_IN_PROGRESS: TltgStatusBlocker = { code: 'run_in_progress', message: 'another update is still running' }
const PRINTING: TltgStatusBlocker = { code: 'printing', message: 'a print is running or paused' }

// Fallback for a service without run.firmware_update: the steps only a QIDI firmware update reports
// (installer/runtime/firmware_update.py STEPS), and Update now's "resume", which continues one. Before stop_services
// nothing has changed, and a failure there isn't kept.
const FIRMWARE_STEPS: readonly string[] = ['stage', 'closed_loop', 'stop_services', 'soc', 'update_folder', 'finish', 'resume']

const PENDING_KINDS: readonly string[] = ['release', 'firmware', 'board']
const BOARD_STATES: readonly string[] = ['current', 'needs_image', 'unrecognized_firmware', 'unrecognized_bootloader', 'held', 'unknown']
const RUN_STATES: readonly string[] = ['running', 'succeeded', 'failed', 'interrupted']
const RUN_KINDS: readonly string[] = ['update_now', 'automatic', 'install']
const JOB_STATES: readonly string[] = ['running', 'succeeded', 'failed']
const BOARD_NAMES: Record<string, string> = { thr: 'THR', mcu: 'Mainboard', closed_loop: 'Closed-loop drivers' }

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
export const tltgSentence = (value: string): string => {
  const sentence = value.trim()

  if (sentence === '') {
    return ''
  }

  return sentence.charAt(0).toUpperCase() + sentence.slice(1) + (/[.!?]$/.test(sentence) ? '' : '.')
}

const time = (value: string | null): number | null => {
  const parsed = value ? Date.parse(value) : NaN

  return Number.isFinite(parsed) ? parsed : null
}

/**
 * The status API base, on the nginx that served this Fluidd. Only used while Fluidd talks to that same printer's
 * Moonraker, so the access token and the status shown both belong to the printer in use.
 */
export const tltgStatusApiBase = (apiUrl: string, location: Pick<Location, 'origin' | 'hostname'>): string | null => {
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

  return `${location.origin}/tltg-status/api`
}

/** Whether a link is a prefilled Valve Oil issue form, the only kind a board's issue link may open. */
export const tltgIssueUrlValid = (value: unknown): value is string => {
  if (typeof value !== 'string' || !value.startsWith(TLTG_ISSUE_URL_PREFIX) || value.length > MAX_ISSUE_URL) {
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
export const tltgBoardName = (id: string): string => {
  const box = /^box(\d+)$/.exec(id)

  return box ? `Box ${box[1]}` : BOARD_NAMES[id] ?? id
}

/** Whether a board is a USB Box. */
export const tltgBoardIsBox = (id: string | null): boolean => id != null && /^box\d+$/.test(id)

const parseBlockers = (value: unknown): TltgStatusBlocker[] => records(value)
  .filter(blocker => optionalText(blocker.code) != null)
  .map(blocker => ({
    code: text(blocker.code),
    message: optionalText(blocker.message) ?? text(blocker.code)
  }))

const parsePending = (value: Record<string, unknown>): TltgStatusPending | null => {
  if (typeof value.kind !== 'string' || !PENDING_KINDS.includes(value.kind)) {
    return null
  }

  return {
    kind: value.kind as TltgPendingKind,
    version: optionalText(value.version),
    current: optionalText(value.current),
    board: optionalText(value.board),
    reinstall: value.kind === 'release' && value.reinstall === true,
    blocked_by: parseBlockers(value.blocked_by)
  }
}

const parseUpdateNow = (value: unknown): TltgStatusUpdateNow | null => (
  isRecord(value) && typeof value.allowed === 'boolean'
    ? { allowed: value.allowed, blocked_by: parseBlockers(value.blocked_by) }
    : null
)

const parseBoard = (value: Record<string, unknown>): TltgStatusBoard | null => {
  const id = optionalText(value.id)

  if (id == null) {
    return null
  }

  return {
    id,
    state: typeof value.state === 'string' && BOARD_STATES.includes(value.state)
      ? value.state as TltgBoardState
      : 'unknown',
    name: optionalText(value.name) ?? tltgBoardName(id),
    firmware: optionalText(value.firmware),
    build: optionalText(value.build),
    pinned: optionalText(value.pinned),
    bootloader: optionalText(value.bootloader),
    issue_url: tltgIssueUrlValid(value.issue_url) ? value.issue_url : null,
    report: optionalText(value.report),
    detail: optionalText(value.detail),
    checked_at: optionalText(value.checked_at),
    waiting_for_firmware_update: optionalBoolean(value.waiting_for_firmware_update)
  }
}

const parseRun = (value: unknown): TltgStatusRun | null => {
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
    kind: typeof value.kind === 'string' && RUN_KINDS.includes(value.kind) ? value.kind as TltgRunKind : 'other',
    state: value.state as TltgRunState,
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

const parseJob = (value: unknown): TltgStatusJob | null => {
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
    state: value.state as TltgJobState,
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
export const parseTltgStatus = (value: unknown): TltgStatus | null => {
  if (!isRecord(value) || value.schema !== TLTG_STATUS_SCHEMA) {
    return null
  }

  const release = record(value.release)
  const printer = record(value.printer)
  const autoUpdate = record(value.auto_update)
  const check = record(value.check)
  const run = parseRun(value.run)
  const job = parseJob(value.job)
  const seen = new Set<string>()

  return {
    updated_at: optionalText(value.updated_at),
    release: {
      installed: optionalText(release.installed)
    },
    printer: {
      id: optionalText(printer.id),
      name: optionalText(printer.name),
      firmware: optionalText(printer.firmware),
      target_firmware: optionalText(printer.target_firmware)
    },
    auto_update: {
      enabled: optionalBoolean(autoUpdate.enabled),
      scheduled: optionalBoolean(autoUpdate.scheduled)
    },
    check: {
      at: optionalText(check.at),
      result: check.result === 'ok' || check.result === 'failed' ? check.result : null,
      message: text(check.message),
      next_at: optionalText(check.next_at)
    },
    pending: records(value.pending)
      .map(parsePending)
      .filter((item): item is TltgStatusPending => item != null),
    boards: records(value.boards)
      .map(parseBoard)
      .filter((board): board is TltgStatusBoard => {
        if (board == null || seen.has(board.id)) {
          return false
        }

        seen.add(board.id)

        return true
      }),
    run,
    job,
    busy: value.busy === true || run?.state === 'running' || job?.state === 'running',
    printer_busy: value.printer_busy === true,
    update_now: parseUpdateNow(value.update_now)
  }
}

/** Reads the job an Update now request started. */
export const parseTltgStatusJob = (value: unknown): TltgStatusJob | null => (
  isRecord(value)
    ? parseJob(value.job)
    : null
)

/**
 * Whether a run is a QIDI firmware update, as the service says. Fallback for a service that doesn't say: read from the
 * step, the steps only a firmware update has, its "install" step (which names the QIDI firmware), and its "boards" step
 * unless that's the update check's own board update, whose failures don't send the operator to the installer.
 */
const isFirmwareRun = (run: TltgStatusRun): boolean => run.firmware_update ?? (
  FIRMWARE_STEPS.includes(run.step) ||
  (run.step === 'install' && run.step_label.includes('QIDI firmware')) ||
  (run.step === 'boards' && (run.kind !== 'automatic' || run.message.includes('./install.sh')))
)

/**
 * Whether a failed firmware update is kept for Update now or the installer to continue, as the service says. Fallback
 * for a service that doesn't say: any failed firmware update, as a failure from stop_services on is kept.
 */
const canContinueRun = (run: TltgStatusRun): boolean => run.state === 'failed' && (run.can_continue ?? isFirmwareRun(run))

/**
 * The update to show: one running, or the last one's result. The run record says what an update is doing and how it
 * ended; Update now's job adds its output, and stands in for the run until the run record is written. The job names
 * its run once the run has begun; before that, or for an older service, their times match them.
 */
const updateView = (status: TltgStatus): TltgUpdateView | null => {
  const { run, job } = status
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
    const state: TltgRunState = status.busy ? 'running' : current.state
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
    const state: TltgRunState = status.busy ? 'running' : job.state

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

/** What Update now would do, and whether it can start; the printer's own answer, or for an older service, busy and printing. */
const updateNowView = (status: TltgStatus, update: TltgUpdateView | null): TltgUpdateNowView => {
  const waiting = status.pending.length > 0
  const unfinished = update?.state === 'failed' || update?.state === 'interrupted'
  const live = status.update_now
  const blockedBy = live
    ? [...live.blocked_by]
    : [...(status.busy ? [RUN_IN_PROGRESS] : []), ...(status.printer_busy ? [PRINTING] : [])]

  // An update this page knows is running, as just after Update now, blocks another whatever the last answer said.
  if (status.busy && !blockedBy.some(blocker => blocker.code === RUN_IN_PROGRESS.code)) {
    blockedBy.unshift(RUN_IN_PROGRESS)
  }

  let action: TltgUpdateNowAction = 'finish'

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

/** A board with what its state means for this printer; see TltgBoardNote. */
const boardView = (board: TltgStatusBoard, status: TltgStatus): TltgBoardView => {
  const box = tltgBoardIsBox(board.id)
  const firmware = status.pending.find(item => item.kind === 'firmware')
  let badge: TltgBoardBadge = board.state
  let note: TltgBoardNote | null = null

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
export const tltgStatusView = (status: TltgStatus, now: number = Date.now()): TltgStatusView => {
  const autoUpdate = status.auto_update.enabled
  const waiting = status.pending
  const reasons: string[] = []

  if (autoUpdate === true) {
    for (const item of waiting) {
      for (const blocker of item.blocked_by) {
        if (blocker.message && !reasons.includes(blocker.message)) {
          reasons.push(blocker.message)
        }
      }
    }
  }

  let notice: TltgWaitingNotice = 'none'

  if (waiting.length > 0) {
    if (autoUpdate !== true) {
      notice = 'auto_off'
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

  return {
    release: status.release.installed,
    printerName: status.printer.name,
    firmware,
    targetFirmware: target && target !== firmware ? target : null,
    autoUpdate,
    waiting,
    notice,
    reasons,
    reinstall: waiting.length > 0 && waiting.every(item => item.reinstall),
    firmwareWaiting: waiting.some(item => item.kind !== 'release'),
    checkedAt: time(status.check.at),
    nextCheckAt: nextCheckAt != null && nextCheckAt > now ? nextCheckAt : null,
    checkFailed: status.check.result === 'failed' ? status.check.message : null,
    boards: status.boards.map(board => boardView(board, status)),
    updating,
    update,
    updateNow: updateNowView(status, update)
  }
}

/**
 * What to do after a status request fails. While an update runs, or was just started, the printer restarts the
 * services that answer, so failures mean "reconnecting" for as long as they last. Otherwise a status already shown is
 * kept through a few failures before it's dropped.
 */
export const tltgStatusPollFailure = (options: { hasStatus: boolean, failures: number, updating: boolean }): TltgStatusFailure => {
  if (options.updating) {
    return 'reconnecting'
  }

  return options.hasStatus && options.failures < TLTG_STATUS_FAILURES_BEFORE_UNAVAILABLE
    ? 'retry'
    : 'unavailable'
}

export const tltgStatusPollInterval = (status: TltgStatus | null, reconnecting = false): number => {
  if (reconnecting) {
    return TLTG_STATUS_POLL_RECONNECT_MS
  }

  return status?.busy
    ? TLTG_STATUS_POLL_BUSY_MS
    : TLTG_STATUS_POLL_IDLE_MS
}

export class TltgStatusRequestError extends Error {
  readonly status: number
  /** Why Update now was refused (409), as the printer listed it. */
  readonly blockedBy: TltgStatusBlocker[]

  constructor (status: number, message: string, blockedBy: TltgStatusBlocker[] = []) {
    super(message)
    this.name = 'TltgStatusRequestError'
    this.status = status
    this.blockedBy = blockedBy
  }
}

export interface TltgStatusRequestOptions {
  method?: 'GET' | 'POST';
  token?: string | null;
  signal?: AbortSignal;
  /** A POST's JSON body, such as {"enabled": true}. */
  body?: Record<string, unknown>;
}

/**
 * Calls the status API and returns its JSON body. Throws TltgStatusRequestError with the server's error text, and the
 * reasons it lists, on an error status, and with status 0 when the printer can't be reached or doesn't answer with JSON.
 * POSTs carry the header and body the service requires of them.
 */
export const tltgStatusRequest = async (base: string, path: string, options: TltgStatusRequestOptions = {}): Promise<unknown> => {
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

    throw new TltgStatusRequestError(0, e instanceof Error ? e.message : String(e))
  }

  let body: unknown = null

  try {
    body = await response.json()
  } catch {
    body = null
  }

  if (!response.ok) {
    const error = isRecord(body) ? text(body.error) : ''

    throw new TltgStatusRequestError(
      response.status,
      error || `${response.status} ${response.statusText}`.trim(),
      isRecord(body) ? parseBlockers(body.blocked_by) : []
    )
  }

  if (body === null) {
    throw new TltgStatusRequestError(0, 'Invalid response')
  }

  return body
}

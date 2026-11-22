// Ported from the Valve Oil Fluidd fork, src/util/__tests__/vo-status.spec.ts at qidi@6405679. Unchanged but for the vitest imports
// (the fork's tests use globals), and the klipper block's tests at the end, which the fork doesn't have yet.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import {
  parseVoKlipperAnswer,
  parseVoStatus,
  parseVoStatusJob,
  voKlipperState,
  voIssueUrlValid,
  voSentence,
  voStatusApiBase,
  voStatusPollFailure,
  voStatusPollInterval,
  voShownUpdate,
  voStatusRequest,
  VoStatusRequestError,
  voStatusView,
  voSupportBundle
} from '../vo-status'

const issue = 'https://github.com/thelegendtubaguy/valve-oil/issues/new?template=printer-support.yml&board=box1'

// A status as the service sends it, with nothing waiting.
const base = {
  schema: 1,
  updated_at: '2026-11-11T08:00:00Z',
  release: { installed: '26.11.11.3' },
  printer: { id: 'max4', name: 'QIDI Max 4', firmware: '01.01.07.01', target_firmware: '01.01.07.01' },
  auto_update: { enabled: true, scheduled: true },
  check: { at: '2026-11-11T08:00:00Z', result: 'ok', message: '', next_at: '2026-11-11T09:05:00Z' },
  pending: [],
  boards: [
    { id: 'thr', state: 'current', name: 'THR', firmware: '1.0.4', build: 'patched', pinned: '1.0.4', bootloader: 'abc', issue_url: null, report: null, detail: null, checked_at: '2026-11-11T08:00:00Z' }
  ],
  run: null,
  job: null,
  busy: false,
  printer_busy: false,
  update_now: { allowed: true, blocked_by: [] }
}

// The time the views below are taken at: after the last check, before the next.
const now = Date.parse('2026-11-11T08:30:00Z')

const release = { kind: 'release', version: '26.11.12.1', current: '26.11.11.3', board: null, reinstall: false, blocked_by: [] }
const reinstall = { ...release, version: '26.11.11.3', reinstall: true }
const firmware = { kind: 'firmware', version: '01.01.08.01', current: '01.01.07.01', board: null, reinstall: false, blocked_by: [] }
const printing = { code: 'printing', message: 'a print is running or paused' }
const heaters = { code: 'heaters_on', message: 'a heater is set to a temperature' }

const job = {
  id: 'job-1',
  state: 'running',
  started_at: '2026-11-11T09:00:00Z',
  finished_at: null,
  log: ['Downloading the latest Valve Oil release...'],
  message: 'Downloading the latest Valve Oil release...',
  run_id: null
}

const run = {
  id: 'run-1',
  kind: 'update_now',
  state: 'running',
  step: 'download',
  step_label: 'Downloading the latest Valve Oil release',
  started_at: '2026-11-11T09:00:01Z',
  finished_at: null,
  message: '',
  resumed: false
}

// A QIDI firmware update that failed part way and is kept for Update now or the installer to continue, from a service
// that doesn't say so (firmware_update and can_continue are added where a test needs them).
const firmwareFailed = {
  id: 'run-fw',
  kind: 'automatic',
  state: 'failed',
  step: 'boards',
  step_label: 'Updating the board firmware',
  started_at: '2026-11-11T03:00:00Z',
  finished_at: '2026-11-11T03:20:00Z',
  message: "A board firmware write could not be verified (THR: no answer after the write). Don't turn the printer off; run ./mainboard-firmware.sh rescue, then run ./install.sh to finish the update.",
  resumed: false
}

const blocker = (code: string, message = code) => ({ code, message })

// Automatic updates with an update window, as a service that has them sends them.
const nightly = { start: 2, end: 5 }
const windowed = (changes: Record<string, unknown> = {}) => ({
  auto_update: { enabled: true, scheduled: true, window: nightly, timezone: 'America/Chicago', timezone_source: 'lookup', in_window: false, ...changes }
})
const outsideWindow = blocker('outside_window', "it's outside the update window, 02:00–05:00 America/Chicago")

const view = (changes: Record<string, unknown>) => {
  const status = parseVoStatus({ ...base, ...changes })

  if (!status) {
    throw new Error('status did not parse')
  }

  return voStatusView(status, now)
}

describe('voStatusApiBase', () => {
  const location = { origin: 'http://192.168.1.166', hostname: '192.168.1.166' }

  it.each([
    ['the page', 'http://192.168.1.166'],
    ["Moonraker's own port", 'http://192.168.1.166:7125']
  ])('uses the nginx that served Fluidd when Fluidd talks to %s', (_, apiUrl) => {
    expect(voStatusApiBase(apiUrl, location)).toBe('http://192.168.1.166/tltg-status/api')
  })

  it.each([
    ['another printer', 'http://192.168.1.200'],
    ['no printer', ''],
    ['an invalid address', 'not a url']
  ])('is unavailable when Fluidd talks to %s', (_, apiUrl) => {
    expect(voStatusApiBase(apiUrl, location)).toBeNull()
  })
})

describe('parseVoStatus', () => {
  const install = {
    state: 'running',
    origin: 'usb',
    steps: ['Check the printer', 'System changes', 'Finish'],
    index: 2,
    detail: null,
    from_version: '26.11.15.1',
    to_version: '26.11.18.2',
    started_at: '2026-11-18T09:14:02Z',
    finished_at: null,
    log: 'valve-oil-usb-20261118-091402.log'
  }

  it.each([
    ['an HTML page', '<!doctype html>'],
    ['null', null],
    ['an error', { error: 'Not found.' }],
    ['a status without a schema', { ...base, schema: undefined }],
    ['a schema this page does not know', { ...base, schema: 2 }]
  ])('is unavailable for %s', (_, body) => {
    expect(parseVoStatus(body)).toBeNull()
  })

  it('reads a status whose fields are all missing or null as unknown', () => {
    expect(parseVoStatus({ schema: 1, release: null, printer: null, auto_update: null, check: null, pending: null, boards: null })).toEqual({
      updated_at: null,
      release: { installed: null, channel: null, commit: null },
      printer: { id: null, name: null, firmware: null, target_firmware: null },
      auto_update: { enabled: null, scheduled: null, window: null },
      check: { at: null, result: null, message: '', next_at: null },
      pending: [],
      boards: [],
      run: null,
      progress: null,
      job: null,
      busy: false,
      printer_busy: false,
      update_now: null,
      support_issue_url: null,
      klipper: null
    })
  })

  it('skips waiting work and blockers it cannot read, and ignores keys it does not know', () => {
    const status = parseVoStatus({
      ...base,
      future: { anything: true },
      pending: [
        { ...firmware, blocked_by: [heaters, { code: 'free_space' }, { message: 'no code' }, 'printing'], extra: 1 },
        { kind: 'macros', version: '1' },
        'release'
      ]
    })

    expect(status?.pending).toEqual([{
      ...firmware,
      blocked_by: [heaters, { code: 'free_space', message: 'free_space' }]
    }])
  })

  it.each([
    ['an unknown state as unknown', { id: 'box1', state: 'melted' }, { state: 'unknown', name: 'Box 1' }],
    ['a missing name from its id', { id: 'mcu', state: 'current', name: null }, { name: 'Mainboard' }],
    ["the closed-loop drivers' name from their id", { id: 'closed_loop', state: 'held' }, { name: 'Closed-loop drivers' }],
    ["only this project's issue form as a link", { id: 'box1', state: 'unrecognized_bootloader', issue_url: issue }, { issue_url: issue }],
    ['any other link as none', { id: 'box1', state: 'unrecognized_bootloader', issue_url: 'javascript:alert(1)' }, { issue_url: null }]
  ])('reads a board with %s', (_, board, expected) => {
    expect(parseVoStatus({ ...base, boards: [board] })?.boards[0]).toMatchObject(expected)
  })

  it('skips boards without an id and repeats of one', () => {
    const boards = parseVoStatus({ ...base, boards: [{ state: 'current' }, { id: 'thr', state: 'current' }, { id: 'thr', state: 'held' }] })?.boards

    expect(boards?.map(board => [board.id, board.state])).toEqual([['thr', 'current']])
  })

  it.each([
    ['a run in a state it does not know', { run: { ...run, state: 'paused' } }, { run: null, busy: false }],
    ['a run of a kind it does not know', { run: { ...run, kind: 'nightly' } }, { run: { kind: 'other' }, busy: true }],
    ['a job in a state it does not know', { job: { ...job, state: 'queued' } }, { job: null, busy: false }],
    ['a running job as busy', { job }, { busy: true }],
    ['the run a job began', { job: { ...job, run_id: 'run-1' } }, { job: { run_id: 'run-1' } }],
    [
      'whether Update now can start, skipping reasons without a code',
      { update_now: { allowed: false, blocked_by: [heaters, { message: 'no code' }, { code: 'installer_busy' }] } },
      { update_now: { allowed: false, blocked_by: [heaters, { code: 'installer_busy', message: 'installer_busy' }] } }
    ],
    ['an Update now answer it cannot read as not sent', { update_now: { blocked_by: [] } }, { update_now: null }],
    ['a firmware update the service names, and whether it can be continued', { run: { ...firmwareFailed, firmware_update: true, can_continue: false } }, { run: { firmware_update: true, can_continue: false } }],
    ['a run from a service that does not say whether it is a firmware update', { run }, { run: { firmware_update: null, can_continue: null } }],
    ['a release to install again', { pending: [reinstall] }, { pending: [{ reinstall: true }] }],
    ['only a release as one to install again', { pending: [{ ...firmware, reinstall: true }] }, { pending: [{ reinstall: false }] }],
    ['a board waiting for the QIDI firmware update', { boards: [{ id: 'thr', state: 'needs_image', waiting_for_firmware_update: true }] }, { boards: [{ waiting_for_firmware_update: true }] }],
    ['a board from a service that does not say whether it waits', {}, { boards: [{ waiting_for_firmware_update: null }] }],
    ['when the next check is due', {}, { check: { next_at: '2026-11-11T09:05:00Z' } }],
    [
      'the update window and the timezone it is read in',
      windowed(),
      { auto_update: { window: { hours: nightly, timezone: 'America/Chicago', timezone_source: 'lookup', in_window: false } } }
    ],
    [
      'a window past midnight',
      windowed({ window: { start: 22, end: 3 } }),
      { auto_update: { window: { hours: { start: 22, end: 3 } } } }
    ],
    [
      'no window as any time, in UTC',
      windowed({ window: null, timezone: null, timezone_source: null, in_window: true }),
      { auto_update: { window: { hours: null, timezone: null, timezone_source: null, in_window: true } } }
    ],
    ['a timezone from a source it does not know', windowed({ timezone_source: 'guess' }), { auto_update: { window: { timezone_source: null } } }],
    ['a service without update windows', {}, { auto_update: { window: null } }],
    [
      'an install running as busy, with its steps and log',
      { progress: install },
      { progress: { state: 'running', origin: 'usb', steps: install.steps, index: 2, log: install.log }, busy: true }
    ],
    // The page opens the log from Moonraker's logs folder by this name.
    ['an install log that is not a run log as none', { progress: { ...install, log: '../config/printer.cfg' } }, { progress: { log: null } }],
    ['an install in a state it does not know as none', { progress: { ...install, state: 'paused' } }, { progress: null, busy: false }],
    ['a step past the last as before the first', { progress: { ...install, index: 9 } }, { progress: { index: 0 } }]
  ])('reads %s', (_, changes, expected) => {
    expect(parseVoStatus({ ...base, ...changes })).toMatchObject(expected)
  })

  it.each([
    ['hours it cannot read', { start: '2', end: 5 }],
    ['an hour out of range', { start: 2, end: 24 }],
    ['a window starting and ending at the same hour', { start: 3, end: 3 }],
    ['part hours', { start: 2.5, end: 5 }]
  ])('reads %s as no update window', (_, window) => {
    expect(parseVoStatus({ ...base, ...windowed({ window }) })?.auto_update.window).toBeNull()
  })
})

describe('parseVoStatusJob', () => {
  it('reads the job Update now started', () => {
    expect(parseVoStatusJob({ job })).toEqual(job)
    expect(parseVoStatusJob({ error: 'An update is already running.' })).toBeNull()
  })
})

describe('voSentence', () => {
  it.each([
    ["a mainboard firmware write wasn't verified; run ./mainboard-firmware.sh rescue first", "A mainboard firmware write wasn't verified; run ./mainboard-firmware.sh rescue first."],
    ['Already a sentence.', 'Already a sentence.'],
    ['  ', '']
  ])('makes a sentence of %j', (value, expected) => {
    expect(voSentence(value)).toBe(expected)
  })
})

describe('voIssueUrlValid', () => {
  it.each([
    issue,
    'https://github.com/thelegendtubaguy/valve-oil/issues/new?template=printer-support.yml'
  ])('accepts %s', (url) => {
    expect(voIssueUrlValid(url)).toBe(true)
  })

  it.each([
    ['another site', 'https://github.com.example.com/thelegendtubaguy/valve-oil/issues/new?x=1'],
    ['another repository', 'https://github.com/someone/valve-oil/issues/new?x=1'],
    ['an issue page', 'https://github.com/thelegendtubaguy/valve-oil/issues/1'],
    ['http', 'http://github.com/thelegendtubaguy/valve-oil/issues/new?x=1'],
    ['a script', 'javascript:alert(1)'],
    ['an oversized link', `${issue}&report=${'x'.repeat(8000)}`],
    ['not text', 7]
  ])('refuses %s', (_, url) => {
    expect(voIssueUrlValid(url)).toBe(false)
  })
})

describe('voStatusView', () => {
  it.each([
    ['nothing waiting', {}, { notice: 'none', reasons: [], firmwareWaiting: false }],
    [
      'a release waiting with automatic updates off',
      { auto_update: { enabled: false, scheduled: true }, pending: [release] },
      { notice: 'auto_off', reasons: [], firmwareWaiting: false }
    ],
    [
      'a release waiting for the next check',
      { pending: [release] },
      { notice: 'next_check', reasons: [] }
    ],
    [
      'firmware waiting for a print to finish',
      { printer_busy: true, pending: [{ ...firmware, blocked_by: [printing, heaters] }, { ...release, blocked_by: [printing] }] },
      { notice: 'blocked', reasons: [printing.message, heaters.message], firmwareWaiting: true }
    ],
    [
      'blockers left over after automatic updates were switched off',
      { auto_update: { enabled: false }, pending: [{ ...firmware, blocked_by: [printing] }] },
      { notice: 'auto_off', reasons: [] }
    ],
    [
      'automatic updates in an unknown state',
      { auto_update: null, pending: [release] },
      { autoUpdate: null, notice: 'auto_off' }
    ],
    [
      'an update running',
      { pending: [release], job, run },
      { updating: true }
    ],
    ['Valve Oil to install again', { pending: [reinstall] }, { notice: 'next_check', reinstall: true, firmwareWaiting: false }],
    ['Valve Oil to install again and QIDI firmware', { pending: [reinstall, firmware] }, { reinstall: false, firmwareWaiting: true }],
    ['a newer release', { pending: [release] }, { reinstall: false }],
    [
      'a release waiting for the update window',
      { ...windowed(), pending: [{ ...release, blocked_by: [outsideWindow] }] },
      { notice: 'window', reasons: [outsideWindow.message] }
    ],
    [
      'a release waiting outside the window, from a printer that does not list the window as a reason',
      { ...windowed(), pending: [release] },
      { notice: 'window' }
    ],
    [
      'a release waiting outside the window while printing, with both reasons',
      { ...windowed(), pending: [{ ...release, blocked_by: [outsideWindow, printing] }] },
      { notice: 'blocked', reasons: [outsideWindow.message, printing.message] }
    ],
    [
      'a release waiting in the update window',
      { ...windowed({ in_window: true }), pending: [release] },
      { notice: 'next_check' }
    ],
    [
      'a release waiting for a window this page cannot read',
      { ...windowed({ window: { start: 3, end: 3 } }), pending: [{ ...release, blocked_by: [outsideWindow] }] },
      { notice: 'blocked', reasons: [outsideWindow.message] }
    ],
    [
      'a release waiting outside the window with automatic updates off',
      { ...windowed({ enabled: false }), pending: [release] },
      { notice: 'auto_off' }
    ]
  ])('with %s', (_, changes, expected) => {
    expect(view(changes)).toMatchObject(expected)
  })

  it.each([
    ['when it is still ahead', base.check.next_at, Date.parse(base.check.next_at)],
    ['not when it has passed', '2026-11-11T08:00:00Z', null],
    ['not when the printer does not say', null, null],
    ['not when it cannot be read', 'soon', null]
  ])('says when the next check is due %s', (_, nextAt, expected) => {
    expect(view({ check: { ...base.check, next_at: nextAt } }).nextCheckAt).toBe(expected)
  })

  it.each([
    ['on its target firmware', '01.01.07.01', null],
    ['on an older firmware', '01.01.06.02', '01.01.07.01']
  ])('names the target firmware only for a printer %s', (_, current, expected) => {
    expect(view({ printer: { ...base.printer, firmware: current } }).targetFirmware).toBe(expected)
  })

  it('says why a check failed', () => {
    expect(view({ check: { at: null, result: 'failed', message: 'The release list could not be reached.' } }).checkFailed)
      .toBe('The release list could not be reached.')
    expect(view({}).checkFailed).toBeNull()
  })

  it("names a dev build and leaves out its check's failure, which lasts until a release publishes what it looks for", () => {
    const failed = { at: null, result: 'failed', message: 'Could not fetch latest release checksum.' }
    const dev = view({ release: { installed: '26.11.15.1', channel: 'dev', commit: 'a8dda52' }, check: failed })

    expect([dev.devBuild, dev.checkFailed]).toEqual(['a8dda52', null])
    expect(view({ release: { installed: '26.11.15.1', channel: 'release', commit: 'a8dda52' }, check: failed }).devBuild).toBeNull()
  })

  it('offers the support issue only as a link to this project', () => {
    expect(view({ support: { issue_url: issue } }).supportIssueUrl).toBe(issue)
    expect(view({ support: { issue_url: 'https://example.com/issues/new?x=1' } }).supportIssueUrl).toBeNull()
    expect(view({}).supportIssueUrl).toBeNull()
  })
})

describe('voStatusView updateNow', () => {
  it.each([
    ['nothing waiting', {}, { offered: false }],
    ['nothing waiting while printing', { printer_busy: true, update_now: { allowed: false, blocked_by: [printing] } }, { offered: false }],
    ['a release waiting', { pending: [release] }, { offered: true, action: 'install', allowed: true, blockedBy: [] }],
    [
      'a release waiting while the printer refuses, with every reason it gives',
      { pending: [release], update_now: { allowed: false, blocked_by: [blocker('installer_busy'), heaters, blocker('recovery_required')] } },
      { offered: true, allowed: false, blockedBy: [blocker('installer_busy'), heaters, blocker('recovery_required')] }
    ],
    [
      "a release waiting while an update this page started runs, before the printer's answer says so",
      { pending: [release], job },
      { allowed: false, blockedBy: [blocker('run_in_progress', 'another update is still running')] }
    ],
    [
      'a release waiting while printing, from a printer that does not say',
      { pending: [release], printer_busy: true, update_now: undefined },
      { allowed: false, blockedBy: [printing] }
    ],
    [
      'a release waiting while an update runs, from a printer that does not say',
      { pending: [release], run, update_now: undefined },
      { allowed: false, blockedBy: [blocker('run_in_progress', 'another update is still running')] }
    ],
    ['a release waiting, from a printer that does not say', { pending: [release], update_now: undefined }, { allowed: true, blockedBy: [] }],
    ['nothing waiting but an interrupted update to finish', { run: { ...run, state: 'interrupted' } }, { offered: true, action: 'finish' }],
    ['nothing waiting but a failed update', { run: { ...run, state: 'failed' } }, { offered: true, action: 'finish' }],
    ['Valve Oil to install again', { pending: [reinstall] }, { offered: true, action: 'install', allowed: true }],
    [
      'a failed QIDI firmware update the service says can be continued',
      { run: { ...firmwareFailed, firmware_update: true, can_continue: true }, pending: [release] },
      { offered: true, action: 'continue', allowed: true }
    ],
    [
      'a failed QIDI firmware update the service says cannot be continued',
      { run: { ...firmwareFailed, firmware_update: true, can_continue: false } },
      { offered: true, action: 'finish' }
    ],
    [
      'a failed QIDI firmware update to continue, from a service that does not say',
      { run: firmwareFailed, pending: [release] },
      { offered: true, action: 'continue', allowed: true }
    ]
  ])('with %s', (_, changes, expected) => {
    expect(view(changes).updateNow).toMatchObject(expected)
  })
})

describe('voStatusView boards', () => {
  const board = (id: string, state: string, extra: Record<string, unknown> = {}) => ({ id, state, firmware: '1.0.2', ...extra })
  const closedLoopDetail = 'The closed-loop motor drivers were not updated: Valve Oil doesn\'t update them yet, so they keep their firmware (X 03.01.01, Y 03.01.01). Update the motor drivers from the touchscreen\'s firmware page to install it.'
  const boxNote = "QIDI firmware 01.01.08.01 carries Box firmware 1.0.5; update this Box from the touchscreen's firmware page."
  const thrItem = { kind: 'board', version: '1.0.4', current: '1.0.2', board: 'thr', blocked_by: [] }

  const waitingDetail = 'will be updated after the QIDI firmware update to 01.01.08.01'

  it.each([
    ['an up-to-date board', board('thr', 'current'), {}, { badge: 'current', note: null, shownDetail: null, reportUrl: null }],
    [
      'a THR the service says waits for the QIDI firmware update',
      board('thr', 'needs_image', { waiting_for_firmware_update: true, detail: waitingDetail }),
      { pending: [firmware] },
      { badge: 'after_firmware', note: 'after_firmware', noteVersion: firmware.version, shownDetail: null }
    ],
    [
      'a Box the service says waits for the QIDI firmware update, named by its target when the update is not listed',
      board('box1', 'needs_image', { waiting_for_firmware_update: true, detail: waitingDetail }),
      { printer: { ...base.printer, target_firmware: '01.01.08.01' } },
      { badge: 'after_firmware', note: 'after_firmware', noteVersion: '01.01.08.01' }
    ],
    [
      'a THR the service says does not wait, whatever else is waiting',
      board('thr', 'needs_image', { waiting_for_firmware_update: false }),
      { pending: [firmware] },
      { badge: 'needs_image', note: null }
    ],
    ['a Box put back on QIDI\'s firmware', board('box2', 'held'), {}, { badge: 'held', note: 'held_box' }],
    [
      'the closed-loop drivers kept on their firmware, in the printer\'s words',
      board('closed_loop', 'held', { detail: closedLoopDetail, issue_url: issue }),
      {},
      { name: 'Closed-loop drivers', badge: 'touchscreen', note: null, shownDetail: closedLoopDetail, reportUrl: issue }
    ],
    ['the closed-loop drivers kept on their firmware, without a detail', board('closed_loop', 'held'), {}, { badge: 'touchscreen', note: 'held_closed_loop', shownDetail: null }],
    ['another board kept as it is', board('thr', 'held'), {}, { badge: 'held', note: 'held' }],
    [
      'a THR waiting for the QIDI firmware update, from a service that does not say',
      board('thr', 'needs_image'),
      { pending: [firmware] },
      { badge: 'after_firmware', note: 'after_firmware', noteVersion: firmware.version }
    ],
    [
      'a mainboard waiting for the QIDI firmware update, without repeating the printer\'s detail, from a service that does not say',
      board('mcu', 'needs_image', { detail: 'will be updated after the QIDI firmware update to 01.01.08.01' }),
      { pending: [firmware] },
      { note: 'after_firmware', shownDetail: null }
    ],
    ['a THR with its own update waiting', board('thr', 'needs_image'), { pending: [firmware, thrItem] }, { badge: 'needs_image', note: null }],
    ['a THR waiting with no firmware update', board('thr', 'needs_image'), { pending: [thrItem] }, { badge: 'needs_image', note: null }],
    [
      'a Box whose bootloader is not supported, with its report link and how to update it',
      board('box1', 'unrecognized_bootloader', { issue_url: issue, detail: boxNote }),
      {},
      { note: 'unrecognized_bootloader_box', shownDetail: boxNote, reportUrl: issue }
    ],
    ['a THR whose bootloader is not supported', board('thr', 'unrecognized_bootloader', { issue_url: issue }), {}, { note: 'unrecognized_bootloader', reportUrl: issue }],
    ['a board with firmware it does not recognize', board('mcu', 'unrecognized_firmware'), {}, { note: 'unrecognized_firmware' }],
    ['a board not read, and why', board('mcu', 'unknown', { detail: 'Klipper was not ready.' }), {}, { note: 'unknown', shownDetail: 'Klipper was not ready.' }]
  ])('explains %s', (_, entry, changes, expected) => {
    expect(view({ ...changes, boards: [entry] }).boards[0]).toMatchObject(expected)
  })
})

describe('voStatusView update', () => {
  const finishedJob = { ...job, state: 'succeeded', finished_at: '2026-11-11T09:05:00Z' }

  it.each([
    [
      'Update now before its run is recorded',
      { job, run: { ...run, id: 'old', kind: 'automatic', state: 'succeeded', started_at: '2026-11-10T03:00:00Z', finished_at: '2026-11-10T03:10:00Z' } },
      { id: 'job-1', kind: 'update_now', state: 'running', stepLabel: '', log: job.log }
    ],
    [
      'Update now at a step',
      { job, run },
      { id: 'run-1', kind: 'update_now', state: 'running', stepLabel: run.step_label, log: job.log, finishedAt: null }
    ],
    [
      'Update now still finishing after its run recorded success',
      { job, run: { ...run, state: 'succeeded', finished_at: '2026-11-11T09:04:59Z' } },
      { state: 'running', stepLabel: '' }
    ],
    [
      'a finished Update now',
      { job: finishedJob, run: { ...run, state: 'succeeded', finished_at: '2026-11-11T09:04:59Z', message: 'The latest Valve Oil release is installed.' } },
      { state: 'succeeded', message: 'The latest Valve Oil release is installed.', log: job.log, finishedAt: Date.parse('2026-11-11T09:04:59Z') }
    ],
    [
      'a failed Update now with no run message',
      { job: { ...finishedJob, state: 'failed', message: 'The update stopped before it finished.' }, run: { ...run, state: 'failed', finished_at: '2026-11-11T09:04:59Z' } },
      { state: 'failed', message: 'The update stopped before it finished.' }
    ],
    [
      'an Update now that failed before its run was recorded',
      { job: { ...finishedJob, state: 'failed', message: 'Permission denied' } },
      { id: 'job-1', state: 'failed', message: 'Permission denied', log: job.log }
    ],
    [
      'an automatic update after an earlier Update now',
      { job: finishedJob, run: { ...run, id: 'auto', kind: 'automatic', started_at: '2026-11-12T03:00:00Z' } },
      { id: 'auto', kind: 'automatic', state: 'running', log: [] }
    ],
    [
      'an interrupted update and the step it stopped at',
      { run: { ...run, kind: 'automatic', state: 'interrupted', step_label: "Installing QIDI's firmware package", resumed: true } },
      { state: 'interrupted', stepLabel: "Installing QIDI's firmware package", resumed: true, log: [] }
    ],
    [
      'Update now continuing a firmware update that started before it, named by the job',
      { job: { ...job, run_id: 'run-fw' }, run: { ...firmwareFailed, state: 'running', step: 'soc', step_label: 'Installing QIDI firmware 01.01.08.01', started_at: '2026-11-11T08:55:00Z', finished_at: null, message: '' } },
      { id: 'run-fw', state: 'running', firmware: true, stepLabel: 'Installing QIDI firmware 01.01.08.01', log: job.log }
    ],
    [
      'a later run than the one the job names',
      { job: { ...finishedJob, run_id: 'run-1' }, run: { ...run, id: 'auto', kind: 'automatic', state: 'succeeded', started_at: '2026-11-12T03:00:00Z' } },
      { id: 'auto', log: [] }
    ],
    [
      'a failed QIDI firmware update with the step it stopped at and its whole message',
      { run: firmwareFailed },
      { state: 'failed', firmware: true, stepLabel: 'Updating the board firmware', message: firmwareFailed.message }
    ],
    [
      'a failed Update now with the step it stopped at',
      { run: { ...run, state: 'failed', message: "The release couldn't be downloaded." } },
      { state: 'failed', firmware: false, stepLabel: run.step_label }
    ],
    [
      'the update check\'s own failed board update as not a firmware update, from a service that does not say',
      { run: { ...firmwareFailed, message: 'THR: failed (no answer)' } },
      { state: 'failed', firmware: false, canContinue: false }
    ],
    [
      'a failed firmware update the service says can be continued',
      { run: { ...firmwareFailed, firmware_update: true, can_continue: true } },
      { firmware: true, canContinue: true }
    ],
    [
      'a failed firmware update the service says cannot be continued, as another release has to finish it',
      { run: { ...firmwareFailed, firmware_update: true, can_continue: false } },
      { firmware: true, canContinue: false }
    ],
    [
      'the update check\'s own failed board update, which the service says is not a firmware update',
      { run: { ...firmwareFailed, firmware_update: false, can_continue: false } },
      { firmware: false, canContinue: false }
    ],
    [
      'a firmware update at a step the fallback would not recognize, as the service says',
      { run: { ...firmwareFailed, step: 'check', step_label: 'Checking that the printer can be updated', firmware_update: true, can_continue: true } },
      { firmware: true, canContinue: true }
    ],
    [
      'Update now still finishing a firmware update the service says can be continued',
      { job, run: { ...firmwareFailed, started_at: '2026-11-11T09:00:01Z', firmware_update: true, can_continue: true } },
      { state: 'running', canContinue: false }
    ]
  ])('shows %s', (_, changes, expected) => {
    const status = parseVoStatus({ ...base, ...changes })

    expect(status && voStatusView(status, now).update).toMatchObject(expected)
  })

  it.each([
    ['Unpacking QIDI firmware 01.01.08.01', 'stage', 'automatic'],
    ['Installing QIDI firmware 01.01.08.01', 'soc', 'install'],
    ['Installing Valve Oil for QIDI firmware 01.01.08.01', 'install', 'update_now'],
    ['Updating the board firmware', 'boards', 'update_now'],
    ['Continuing the firmware update', 'resume', 'update_now']
  ])('reads a failed run at "%s" as a QIDI firmware update, from a service that does not say', (label, step, kind) => {
    expect(view({ run: { ...firmwareFailed, kind, step, step_label: label, message: 'It failed.' } }).update?.firmware).toBe(true)
  })

  it.each([
    ['Installing the latest Valve Oil release', 'install', 'update_now'],
    ['Checking that the printer is idle', 'check', 'update_now'],
    ['Updating the board firmware', 'boards', 'automatic']
  ])('reads a failed run at "%s" as not a QIDI firmware update, from a service that does not say', (label, step, kind) => {
    expect(view({ run: { ...firmwareFailed, kind, step, step_label: label, message: 'It failed.' } }).update?.firmware).toBe(false)
  })

  it('shows nothing when no update has run', () => {
    expect(view({}).update).toBeNull()
  })
})

describe('voShownUpdate', () => {
  const finished = Date.parse('2026-11-11T09:05:00Z')
  const hours = (count: number) => finished + count * 60 * 60 * 1000
  const update = (state: string) => view({ run: { ...run, state, finished_at: '2026-11-11T09:05:00Z' } }).update

  it.each([
    ['a success, for a day', 'succeeded', hours(23), true],
    ['a success, not after a day', 'succeeded', hours(25), false],
    ['a failure, until dismissed', 'failed', hours(24 * 30), true],
    ['an interrupted update, until dismissed', 'interrupted', hours(24 * 30), true]
  ])('shows %s', (_, state, at, shown) => {
    expect(voShownUpdate(update(state), [], at) != null).toBe(shown)
    expect(voShownUpdate(update(state), ['run-1'], hours(1))).toBeNull()
  })

  it('shows a running update even when an earlier result with its id was dismissed', () => {
    expect(voShownUpdate(view({ run, busy: true }).update, ['run-1'], hours(48))?.state).toBe('running')
  })
})

describe('voSupportBundle', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("names the bundle as the printer does, and reports the server's error", async () => {
    const zip = new Blob(['zip'])
    const fetch = vi.fn()
      .mockResolvedValueOnce(new Response(zip, { headers: { 'Content-Disposition': 'attachment; filename="valve-oil-support-max4-20261116T150300Z.zip"' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: 'A support bundle is already being made.' }), { status: 409 }))

    vi.stubGlobal('fetch', fetch)

    const bundle = await voSupportBundle('/tltg-status/api', { token: 'abc' })

    expect(bundle.name).toBe('valve-oil-support-max4-20261116T150300Z.zip')
    expect(fetch.mock.calls[0][0]).toBe('/tltg-status/api/support-bundle')
    expect(fetch.mock.calls[0][1].headers).toEqual({ Authorization: 'Bearer abc' })
    await expect(voSupportBundle('/tltg-status/api')).rejects.toMatchObject({ status: 409, message: 'A support bundle is already being made.' })
  })
})

describe('voStatusPollFailure', () => {
  it.each([
    ['during an update, however long it lasts', { hasStatus: true, failures: 40, updating: true }, 'reconnecting'],
    ['right after Update now, before any status', { hasStatus: false, failures: 1, updating: true }, 'reconnecting'],
    ['once, with a status shown', { hasStatus: true, failures: 1, updating: false }, 'retry'],
    ['repeatedly, with a status shown', { hasStatus: true, failures: 3, updating: false }, 'unavailable'],
    ['before any status, as on a printer without the service', { hasStatus: false, failures: 1, updating: false }, 'unavailable']
  ] as const)('after a failure %s: %s', (_, options, expected) => {
    expect(voStatusPollFailure(options)).toBe(expected)
  })

  it('polls faster while an update runs and while reconnecting', () => {
    const idle = parseVoStatus(base)
    const busy = parseVoStatus({ ...base, job })

    expect(voStatusPollInterval(idle)).toBe(10000)
    expect(voStatusPollInterval(null)).toBe(10000)
    expect(voStatusPollInterval(busy)).toBe(2000)
    expect(voStatusPollInterval(busy, true)).toBe(3000)
  })
})

describe('voStatusRequest', () => {
  const fetchMock = vi.fn()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    fetchMock.mockReset()
    vi.unstubAllGlobals()
  })

  const respond = (status: number, body: string) => {
    fetchMock.mockResolvedValueOnce(new Response(body, { status }))
  }

  it('sends the headers and body the service requires of a POST', async () => {
    respond(200, JSON.stringify(base))

    await voStatusRequest('http://printer/tltg-status/api', '/auto-update', { method: 'POST', token: 'abc', body: { enabled: false } })

    const [url, init] = fetchMock.mock.calls[0]

    expect(url).toBe('http://printer/tltg-status/api/auto-update')
    expect(init).toMatchObject({
      method: 'POST',
      body: '{"enabled":false}',
      cache: 'no-store',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        Authorization: 'Bearer abc',
        'X-Valve-Oil': 'status',
        'Content-Type': 'application/json'
      }
    })
  })

  it('sends Update now an empty body, and a GET neither a body nor the POST header', async () => {
    respond(202, JSON.stringify({ job }))
    respond(200, JSON.stringify(base))

    await voStatusRequest('http://printer/tltg-status/api', '/update-now', { method: 'POST' })
    await voStatusRequest('http://printer/tltg-status/api', '/status', { token: null })

    expect(fetchMock.mock.calls[0][1].body).toBe('{}')
    expect(fetchMock.mock.calls[1][1].body).toBeUndefined()
    expect(fetchMock.mock.calls[1][1].headers).toEqual({ Accept: 'application/json' })
  })

  it("reports the server's error text with its status, and why Update now was refused", async () => {
    respond(409, JSON.stringify({
      error: "Update now can't start: a print is running or paused; another installer run is in progress.",
      blocked_by: [printing, { code: 'installer_busy', message: 'another installer run is in progress' }, 'bad']
    }))
    respond(400, JSON.stringify({ error: 'Update now takes no options.' }))

    await expect(voStatusRequest('http://printer/tltg-status/api', '/update-now', { method: 'POST' }))
      .rejects.toMatchObject({
        status: 409,
        message: "Update now can't start: a print is running or paused; another installer run is in progress.",
        blockedBy: [printing, { code: 'installer_busy', message: 'another installer run is in progress' }]
      })
    await expect(voStatusRequest('http://printer/tltg-status/api', '/update-now', { method: 'POST' }))
      .rejects.toMatchObject({ status: 400, blockedBy: [] })
  })

  it.each([
    ['answers with an HTML page', () => respond(200, '<!doctype html>')],
    ["can't be reached", () => fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'))]
  ])('fails without a status when the printer %s', async (_, arrange) => {
    arrange()

    const request = voStatusRequest('http://printer/tltg-status/api', '/status')

    await expect(request).rejects.toBeInstanceOf(VoStatusRequestError)
    await expect(request).rejects.toMatchObject({ status: 0 })
  })

  it('fails with the status when nginx answers without JSON', async () => {
    respond(502, '<html>Bad Gateway</html>')

    await expect(voStatusRequest('http://printer/tltg-status/api', '/status')).rejects.toMatchObject({ status: 502 })
  })
})

describe('klipper', () => {
  const klipper = {
    available: true,
    unavailable_reason: null,
    selected: 'qidi',
    running: 'qidi',
    fallback_reason: null,
    version: 'v0.14.0-vo1',
    restart_error: null,
    switch: { to: 'valve_oil', allowed: true, blocked_by: [] }
  }

  it('is missing from a service without it, or with a choice this page does not know', () => {
    expect(parseVoStatus(base)?.klipper).toBeNull()
    expect(parseVoStatus({ ...base, klipper: { ...klipper, selected: 'upstream' } })?.klipper).toBeNull()
  })

  it('says off, on, fallen back, starting, or leaving', () => {
    const state = (changes: object) => voKlipperState(parseVoStatus({ ...base, klipper: { ...klipper, ...changes } })!.klipper!)

    expect(state({})).toBe('off')
    expect(state({ selected: 'valve_oil', running: 'valve_oil' })).toBe('on')
    expect(state({ selected: 'valve_oil', running: 'qidi', fallback_reason: "Valve Oil's Klipper didn't become ready" })).toBe('fallback')
    expect(state({ selected: 'valve_oil', running: null })).toBe('starting')
    expect(state({ running: 'upstream' })).toBe('starting')
    expect(state({ running: 'valve_oil' })).toBe('leaving')
  })

  it('never allows a switch the printer gave reasons against, and works out where it goes', () => {
    const parsed = parseVoStatus({
      ...base,
      klipper: { ...klipper, selected: 'valve_oil', switch: { allowed: true, blocked_by: [printing, { message: 'no code' }] } }
    })?.klipper

    expect(parsed?.switch).toEqual({ to: 'qidi', allowed: false, blocked_by: [printing] })
  })

  it("reads a switch's answer", () => {
    expect(parseVoKlipperAnswer({ klipper: { ...klipper, selected: 'valve_oil', running: null } })).toMatchObject({ selected: 'valve_oil', running: null })
    expect(parseVoKlipperAnswer({ job: {} })).toBeNull()
  })
})

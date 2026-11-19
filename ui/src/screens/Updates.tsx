import { useEffect, useRef, useState } from 'react'
import { ChevronDown, CircleCheck, Cpu, Download, ExternalLink, Info, LoaderCircle, TriangleAlert } from 'lucide-react'
import { Card, Dialog, PageHeader, Toggle } from '../components/ui'
import { Notice } from '../components/settings/common'
import { LanOnly } from '../components/settings/LanOnly'
import { Log } from '../components/settings/Addons'
import { InstallStepsCard, InstallerLogsCard } from '../components/updates/Install'
import { client } from '../state/printer'
import {
  VO_UPDATE_NOW_BLOCKERS,
  voBrowserTimezone,
  voHourLabel,
  voSentence,
  voUpdateWindowLabel,
  voUpdateWindowValid,
  type VoBoardBadge,
  type VoBoardNote,
  type VoBoardView,
  type VoStatusBlocker,
  type VoStatusBoard,
  type VoStatusPending,
  type VoStatusUpdateWindow,
  type VoStatusView,
  type VoUpdateView,
  type VoUpdateWindowHours,
} from '../qidi/vo-status'
import {
  autoUpdateDisabled,
  clearAutoUpdateError,
  clearSupportError,
  clearUpdateNowError,
  clearWindowError,
  dismissUpdate,
  reconnecting,
  reconnectingLongMinutes,
  reportProblem,
  setAutoUpdate,
  setUpdateWindow,
  shownUpdate,
  updateNow,
  updateNowDisabled,
  useValveOil,
  useValveOilView,
  watchValveOil,
  type ValveOilState,
} from '../state/valve-oil'

// The wording is the Fluidd fork's (app.vo_valve_oil in src/locales/en.yaml there); keep the two in step.

const fmtTime = (ms: number) => new Date(ms).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
/** A time later today as just the time, and one on another day with its date. */
const fmtSoon = (ms: number) => (new Date(ms).toDateString() === new Date().toDateString() ? new Date(ms).toLocaleTimeString([], { timeStyle: 'short' }) : fmtTime(ms))

const RECONNECTING = 'Waiting for the printer to answer. It restarts its services while it updates, so this is expected. This page reconnects by itself.'
const reconnectingLong = (minutes: number) => `The printer hasn't answered for ${minutes} minutes. Don't turn it off. Check its screen to see what it's doing.`
export const DONT_POWER_OFF = "Don't turn the printer off until it finishes."

export function reconnectText(s: ValveOilState) {
  const minutes = reconnectingLongMinutes(s)
  return minutes != null ? reconnectingLong(minutes) : RECONNECTING
}

const RUNNING: Record<VoUpdateView['kind'], string> = {
  update_now: 'Updating…',
  automatic: 'Automatic update running…',
  install: 'The Valve Oil installer is running…',
  other: 'Updating…',
}
const RESULT: Record<Exclude<VoUpdateView['state'], 'running'>, Record<VoUpdateView['kind'], string>> = {
  succeeded: { update_now: 'The update finished', automatic: 'The automatic update finished', install: 'The Valve Oil installer finished', other: 'The update finished' },
  failed: { update_now: 'The update failed', automatic: 'The automatic update failed', install: 'The Valve Oil installer failed', other: 'The update failed' },
  interrupted: {
    update_now: 'The update stopped before it finished',
    automatic: 'The automatic update stopped before it finished',
    install: 'The Valve Oil installer stopped before it finished',
    other: 'The update stopped before it finished',
  },
}

const BOARD_BADGE: Record<VoBoardBadge, string> = {
  current: 'Up to date',
  needs_image: 'Update waiting',
  after_firmware: 'Waiting for QIDI firmware',
  unrecognized_firmware: 'Firmware not recognized',
  unrecognized_bootloader: 'Not supported yet',
  held: "Kept on QIDI's firmware",
  touchscreen: 'Update from the touchscreen',
  unknown: 'Not checked',
}
const BOARD_TONE: Record<VoBoardBadge, string> = {
  current: 'tone-good',
  needs_image: 'tone-accent',
  after_firmware: 'tone-accent',
  unrecognized_firmware: 'tone-warn',
  unrecognized_bootloader: 'tone-warn',
  held: 'tone-muted',
  touchscreen: 'tone-muted',
  unknown: 'tone-muted',
}
const BOARD_BUILD: Record<string, string> = {
  patched: "Valve Oil's build",
  qidi: "QIDI's build",
  stock: "QIDI's build",
  superseded: 'An earlier Valve Oil build',
}

function boardFirmwareText(board: VoStatusBoard) {
  const version = board.firmware ? `Firmware ${board.firmware}` : 'Firmware not read'
  const build = board.build ? BOARD_BUILD[board.build] : undefined
  return build ? `${version} · ${build}` : version
}

const BOARD_NOTE: Record<Exclude<VoBoardNote, 'after_firmware'>, string> = {
  unrecognized_bootloader:
    "Valve Oil doesn't recognize this board's bootloader yet, so it doesn't update this board or the printer's QIDI firmware. Open a support issue so a later release can support it.",
  unrecognized_bootloader_box:
    "Valve Oil doesn't recognize this Box's bootloader yet, so it leaves this Box as it is and keeps updating the rest of the printer. Open a support issue so a later release can support it.",
  unrecognized_firmware: "This board runs firmware Valve Oil doesn't recognize, so Valve Oil leaves it as it is. Open a support issue so a later release can support it.",
  unknown: "Valve Oil hasn't been able to read this board's firmware yet.",
  held_box: "This Box was put back on QIDI's firmware. Valve Oil leaves it there until you install the next Valve Oil release.",
  held_closed_loop: "Valve Oil left the closed-loop motor drivers on their firmware. Update them from the touchscreen's firmware page.",
  held: "Valve Oil leaves this board's firmware as it is.",
}

function boardExplanation(board: VoBoardView) {
  if (board.note === 'after_firmware')
    return `Valve Oil updates this board in the same update, right after it installs ${board.noteVersion ? `QIDI firmware ${board.noteVersion}` : 'the QIDI firmware update'}.`
  return board.note ? BOARD_NOTE[board.note] : ''
}

// Why Update now can't start, by the printer's code; any other code is shown as the printer says it.
const UPDATE_NOW_BLOCKED: Record<string, string> = {
  run_in_progress: 'Available once the running update finishes.',
  installer_busy: 'Valve Oil is checking for updates or installing. Available once it finishes.',
  printing: 'A print is running or paused. Available once it finishes.',
  heaters_on: 'A heater is on. Available once every heater is off.',
  printer_state_unknown: "Klipper hasn't said whether the printer is idle. Available once it's ready.",
  installer_missing: "Valve Oil's installer is missing from the printer. Install Valve Oil again to use Update now.",
}

// A recovery is shown as the printer says it, as its message names what to run first.
function blockedText(blockedBy: VoStatusBlocker[]) {
  if (blockedBy.length === 0) return 'Not available right now.'
  return blockedBy
    .map((b) => (VO_UPDATE_NOW_BLOCKERS.includes(b.code) ? UPDATE_NOW_BLOCKED[b.code] : b.code === 'recovery_required' ? voSentence(b.message) : `Not available now: ${b.message}.`))
    .join(' ')
}

function waitingText(item: VoStatusPending, boards: VoStatusBoard[]) {
  const current = item.current ? ` (this printer has ${item.current})` : ''
  switch (item.kind) {
    case 'release':
      if (item.reinstall) return 'Valve Oil needs to be installed again (its files on the printer are missing or changed)'
      return item.version ? `Valve Oil ${item.version}${current}` : 'A new Valve Oil release'
    case 'firmware':
      return item.version ? `QIDI firmware ${item.version}${current}` : 'A QIDI firmware update'
    default: {
      const name = boards.find((b) => b.id === item.board)?.name ?? item.board
      return name ? `New firmware for ${name}` : 'New firmware for a board'
    }
  }
}

function noticeText(view: VoStatusView) {
  switch (view.notice) {
    // Installing the installed release again says so, rather than "the update".
    case 'auto_off':
      return `${view.reinstall ? 'Valve Oil needs to be installed again.' : 'An update is waiting.'} Automatic updates are off, so install it with Update now or by running the Valve Oil installer.`
    case 'blocked':
      return `Automatic updates are on, but ${view.reinstall ? "Valve Oil hasn't been installed again yet" : "the update hasn't been installed yet"}: ${view.reasons.join('; ')}.`
    case 'window': {
      const hours = view.updateWindow?.hours
      const window = `${hours ? voUpdateWindowLabel(hours) : ''} ${view.updateWindow?.timezone ?? 'UTC'}`
      return view.reinstall
        ? `Automatic updates are on, so Valve Oil will be installed again during the update window, ${window}, while the printer is idle.`
        : `Automatic updates are on, so the update will be installed during the update window, ${window}, while the printer is idle.${view.firmwareWaiting ? " The printer can't be used while it installs." : ''}`
    }
    case 'next_check': {
      const when = view.nextCheckAt != null ? `, around ${fmtSoon(view.nextCheckAt)},` : ''
      return view.reinstall
        ? `Automatic updates are on, so Valve Oil will be installed again at the next update check${when} while the printer is idle.`
        : `Automatic updates are on, so the update will be installed at the next update check${when} while the printer is idle.${view.firmwareWaiting ? " The printer can't be used while it installs." : ''}`
    }
    default:
      return ''
  }
}

function confirmText(view: VoStatusView): { text: string; strong: boolean } {
  const release = view.waiting.find((item) => item.kind === 'release')
  if (view.updateNow.action === 'continue')
    return {
      strong: true,
      text: "Continue the QIDI firmware update that stopped? The printer can't be used until the update finishes, which can take a while, and this page loses contact with the printer for part of that time. Don't turn the printer off until the update finishes.",
    }
  if (view.updateNow.action === 'finish')
    return {
      strong: true,
      text: "Finish the update that stopped? The printer can't be used until it finishes, which can take a while, and this page loses contact with the printer for part of that time. Don't turn the printer off until the update finishes.",
    }
  if (view.firmwareWaiting)
    return {
      strong: true,
      text: "Install everything that's waiting now, including QIDI firmware and board firmware? The printer can't be used until the update finishes, which can take a while, and this page loses contact with the printer for part of that time. Don't turn the printer off until the update finishes.",
    }
  if (release?.reinstall && release.version)
    return {
      strong: false,
      text: `Install Valve Oil ${release.version} again now? Its files on the printer are missing or changed. Klipper restarts while it installs, so the printer can't be used for a few minutes.`,
    }
  return {
    strong: false,
    text: `Install ${release?.version && !release.reinstall ? `Valve Oil ${release.version}` : 'the latest Valve Oil release'} now? Klipper restarts while it installs, so the printer can't be used for a few minutes.`,
  }
}

export function UpdatesScreen() {
  useEffect(watchValveOil, [])
  const s = useValveOil()
  const view = useValveOilView()

  if (!view) {
    const text = reconnecting(s) ? reconnectText(s) : s.unavailable ? "Valve Oil's update status isn't available on this printer." : "Loading Valve Oil's status…"
    return (
      <div className="grid content-start gap-3.5">
        <PageHeader title="Updates" />
        <Card>
          <div className="flex items-start gap-2.5 text-ink-2">
            {reconnecting(s) ? <LoaderCircle size={16} className="spin mt-0.5 flex-none" /> : <Info size={16} className="mt-0.5 flex-none text-ink-3" />}
            <span>{text}</span>
          </div>
          {s.expectUpdate && reconnecting(s) && <div className="mt-2 font-medium text-warn">{DONT_POWER_OFF}</div>}
        </Card>
      </div>
    )
  }

  return (
    <div className="grid content-start gap-3.5">
      <PageHeader title="Updates" />
      <div className="grid content-start items-start gap-3.5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
        <InstallStepsCard progress={s.status?.progress ?? null} />
        <ValveOilCard s={s} view={view} />
        <div className="grid content-start gap-3.5">
          <BoardsCard view={view} />
          <InstallerLogsCard progress={s.status?.progress ?? null} />
        </div>
      </div>
    </div>
  )
}

// The hours offered when a window is first set, before it's saved.
const SUGGESTED_HOURS: VoUpdateWindowHours = { start: 2, end: 6 }
const HOURS = Array.from({ length: 24 }, (_, hour) => hour)
const sameHours = (a: VoUpdateWindowHours | null, b: VoUpdateWindowHours | null) => (a == null || b == null ? a === b : a.start === b.start && a.end === b.end)

/** The hours automatic updates start in, and the timezone they're read in; edits are kept here until saved. */
function UpdateWindow({ window, disabled, pending, error }: { window: VoStatusUpdateWindow; disabled: boolean; pending: boolean; error: string }) {
  // undefined while not edited; null for any time.
  const [draft, setDraft] = useState<VoUpdateWindowHours | null | undefined>(undefined)
  const [browser] = useState(voBrowserTimezone)
  const hours = draft === undefined ? window.hours : draft
  const changed = draft !== undefined && !sameHours(draft, window.hours)
  const valid = hours == null || voUpdateWindowValid(hours)
  // A window saved without a timezone is saved with this browser's.
  const savesBrowserTimezone = window.timezone == null && browser != null
  const wasPending = useRef(pending)
  useEffect(() => {
    // Saved: show the window as the printer now has it.
    if (wasPending.current && !pending && !error) setDraft(undefined)
    wasPending.current = pending
  }, [pending, error])

  const timezoneText = (() => {
    if (changed && savesBrowserTimezone) return `Times are in ${browser} (this browser's timezone).`
    if (window.timezone == null) return 'Times are in UTC.'
    switch (window.timezone_source) {
      case 'lookup':
        return `Times are in ${window.timezone} (found from the printer's internet address).`
      case 'operator':
        return `Times are in ${window.timezone} (set over SSH).`
      case 'browser':
        return window.timezone === browser ? `Times are in ${window.timezone} (this browser's timezone).` : `Times are in ${window.timezone} (set from a browser).`
      default:
        return `Times are in ${window.timezone}.`
    }
  })()
  // This browser's timezone, when the window is read in another; not while the hours are being edited.
  const offeredTimezone = browser != null && !changed && browser !== (window.timezone ?? 'UTC') ? browser : null
  const setHours = (change: Partial<VoUpdateWindowHours>) => setDraft({ ...(hours ?? SUGGESTED_HOURS), ...change })
  const save = () => {
    if (!changed || !valid) return
    void setUpdateWindow({ window: hours, ...(hours && savesBrowserTimezone && browser ? { timezone: browser } : {}) })
  }
  const hourSelect = (value: number, label: string, onChange: (hour: number) => void) => (
    <select className="field w-auto" aria-label={label} value={value} disabled={disabled} onChange={(e) => onChange(Number(e.target.value))}>
      {HOURS.map((hour) => (
        <option key={hour} value={hour}>
          {voHourLabel(hour)}
        </option>
      ))}
    </select>
  )

  return (
    <div className="grid gap-2 py-1">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium">Update window</div>
          <div className="text-[12.5px] text-ink-3">{hours ? 'Automatic updates start only between these hours.' : 'Automatic updates can start at any hour.'}</div>
          {hours && (
            <div className="text-[12.5px] text-ink-3">
              {timezoneText}
              {offeredTimezone && (
                <button className="ml-1 cursor-pointer border-0 bg-transparent p-0 font-medium text-accent hover:underline disabled:opacity-45" disabled={disabled} onClick={() => void setUpdateWindow({ timezone: offeredTimezone })}>
                  Use {offeredTimezone}
                </button>
              )}
            </div>
          )}
        </div>
        <select
          className="field w-auto"
          aria-label="Update window"
          value={hours ? 'hours' : 'any'}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value === 'hours' ? (hours ?? window.hours ?? { ...SUGGESTED_HOURS }) : null)}
        >
          <option value="any">Any time</option>
          <option value="hours">Set hours</option>
        </select>
      </div>
      {(hours || changed) && (
        <div className="flex flex-wrap items-center justify-end gap-2">
          {hours && (
            <>
              {hourSelect(hours.start, 'Start hour', (start) => setHours({ start }))}
              <span className="text-ink-2">to</span>
              {hourSelect(hours.end, 'End hour', (end) => setHours({ end }))}
            </>
          )}
          {changed && (
            <>
              <button className="btn btn-sm btn-primary" disabled={disabled || !valid} onClick={save}>
                {pending && <LoaderCircle size={13} className="spin" />}
                Save
              </button>
              <button className="btn btn-sm" disabled={pending} onClick={() => setDraft(undefined)}>
                Cancel
              </button>
            </>
          )}
        </div>
      )}
      {!valid && <div className="text-right text-[12px] text-bad">Pick a different start and end hour.</div>}
      {error && (
        <Notice tone="bad" onClose={clearWindowError}>
          The update window couldn't be changed: {error}
        </Notice>
      )}
    </div>
  )
}

function ValveOilCard({ s, view }: { s: ValveOilState; view: VoStatusView }) {
  const [confirm, setConfirm] = useState(false)
  const update = shownUpdate(view, s.dismissed)
  const disabled = updateNowDisabled(s)
  const confirmation = confirmText(view)
  const notice = view.notice !== 'none' && !view.updating ? noticeText(view) : ''
  const actionLabel = view.updateNow.action === 'continue' ? 'Continue the update' : 'Update now'
  const recoveryRequired = view.updateNow.blockedBy.some((b) => b.code === 'recovery_required')

  return (
    <Card>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="section-label">Valve Oil{view.printerName ? ` · ${view.printerName}` : ''}</div>
          <div className="mt-1 flex items-center gap-2.5 text-[22px] font-bold tracking-[-0.01em]">
            {view.updating ? (
              <LoaderCircle size={22} className="spin flex-none text-accent-ink" />
            ) : view.waiting.length > 0 ? (
              <Download size={22} className="flex-none text-accent-ink" />
            ) : (
              <CircleCheck size={22} className="flex-none text-good" />
            )}
            {view.updating ? 'Updating' : view.waiting.length > 0 ? 'Waiting to install' : 'Up to date'}
          </div>
        </div>
      </div>

      <div className="mt-3 rounded-xl bg-inset px-3.5 py-1 shadow-[inset_0_0_0_1px_var(--line)]">
        <div className="row-k">
          <span className="k">Valve Oil release</span>
          <span className="v">{view.release || 'Unknown'}</span>
          {view.devBuild != null && <span className="col-span-2 -mt-1 text-right text-[12px] text-ink-3">{view.devBuild ? `Dev build ${view.devBuild}` : 'Dev build'}</span>}
        </div>
        <div className="row-k">
          <span className="k">QIDI firmware</span>
          <span className="v">{view.firmware || 'Unknown'}</span>
          {view.targetFirmware && <span className="col-span-2 -mt-1 text-right text-[12px] text-ink-3">This Valve Oil release updates it to {view.targetFirmware}</span>}
        </div>
      </div>

      <div className="mt-2" />
      <label className="flex items-center gap-3 py-1">
        <div className="min-w-0 flex-1">
          <div className="font-medium">Automatic updates</div>
          <div className="text-[12.5px] text-ink-3">Install new Valve Oil releases, QIDI firmware, and board firmware by themselves while the printer is idle.</div>
        </div>
        {s.autoUpdateRequest != null && <LoaderCircle size={15} className="spin flex-none text-ink-3" />}
        <Toggle checked={s.autoUpdateRequest ?? s.status?.auto_update.enabled === true} disabled={autoUpdateDisabled(s)} label="Automatic updates" onChange={setAutoUpdate} />
      </label>
      {view.updateWindow && <UpdateWindow window={view.updateWindow} disabled={autoUpdateDisabled(s)} pending={s.windowPending} error={s.windowError} />}
      {/* QIDI's setting, through Moonraker; it hides itself while Moonraker is down or isn't QIDI's. */}
      <LanOnly className="mt-2 border-t border-line pt-2" />

      <div className="sep my-2" />
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2 py-1">
        <div className="min-w-[180px] flex-1">
          <div className="font-medium">Waiting to install</div>
          {view.waiting.length === 0 ? (
            <div className="text-[12.5px] text-ink-3">Nothing is waiting.</div>
          ) : (
            view.waiting.map((item) => (
              <div key={`${item.kind}-${item.board ?? ''}-${item.version ?? ''}`} className="text-[12.5px] text-ink-2">
                {waitingText(item, view.boards)}
              </div>
            ))
          )}
        </div>
        {view.updateNow.offered && (
          <div className="ml-auto grid max-w-[320px] justify-items-end gap-1">
            <button className="btn btn-primary" disabled={disabled} onClick={() => setConfirm(true)}>
              {s.updateNowPending && <LoaderCircle size={13} className="spin" />}
              {actionLabel}
            </button>
            {!view.updateNow.allowed && <div className="text-right text-[12px] text-ink-3">{blockedText(view.updateNow.blockedBy)}</div>}
          </div>
        )}
      </div>

      <div className="mt-1 grid gap-2 empty:hidden">
        {notice && <Notice tone={view.notice === 'next_check' || view.notice === 'window' ? 'info' : 'warn'}>{notice}</Notice>}
        {reconnecting(s) && (
          <Notice tone="info" icon={<LoaderCircle size={15} className="spin" />}>
            {reconnectText(s)}
          </Notice>
        )}
        {s.autoUpdateError && (
          <Notice tone="bad" onClose={clearAutoUpdateError}>
            Automatic updates couldn't be changed: {s.autoUpdateError}
          </Notice>
        )}
        {s.updateNowError && (
          <Notice tone="bad" onClose={clearUpdateNowError}>
            {s.updateNowBlockedBy.length > 0 ? `Update now couldn't start. ${blockedText(s.updateNowBlockedBy)}` : `The update couldn't be started: ${s.updateNowError}`}
          </Notice>
        )}
        {view.checkFailed != null && <Notice tone="warn">{view.checkFailed ? `The last check for updates failed: ${view.checkFailed}` : 'The last check for updates failed.'}</Notice>}
        {update && <UpdateProgress update={update} recoveryRequired={recoveryRequired} />}
      </div>

      {view.supportIssueUrl && (
        <>
          <div className="sep my-2" />
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 py-1">
            <div className="min-w-[180px] flex-1">
              <div className="font-medium">Report a problem</div>
              <div className="text-[12.5px] text-ink-3">
                Opens a bug report on GitHub and downloads a support bundle with Klipper's, Moonraker's, and Valve Oil's logs. Attach the bundle to the issue.
              </div>
            </div>
            <button className="btn btn-sm ml-auto" disabled={s.supportPending} onClick={reportProblem}>
              {s.supportPending ? <LoaderCircle size={13} className="spin" /> : <ExternalLink size={13} />}
              Open an issue
            </button>
          </div>
          {s.supportError && (
            <Notice tone="bad" onClose={clearSupportError}>
              The support bundle couldn't be downloaded: {s.supportError}
            </Notice>
          )}
        </>
      )}

      {view.checkedAt && <div className="mt-3 border-t border-line pt-2 text-[12px] text-ink-3">Last checked for updates {fmtTime(view.checkedAt)}</div>}

      <Dialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title={actionLabel}
        footer={
          <>
            <button className="btn" onClick={() => setConfirm(false)}>
              Cancel
            </button>
            <button
              className="btn btn-primary"
              disabled={disabled}
              onClick={() => {
                setConfirm(false)
                updateNow()
              }}
            >
              {actionLabel}
            </button>
          </>
        }
      >
        {confirmation.strong ? (
          <div className="tone-warn flex items-start gap-2.5 rounded-lg px-3 py-2.5">
            <TriangleAlert size={16} className="mt-0.5 flex-none" />
            <span className="text-ink">{confirmation.text}</span>
          </div>
        ) : (
          <div>{confirmation.text}</div>
        )}
      </Dialog>
    </Card>
  )
}

function UpdateProgress({ update, recoveryRequired }: { update: VoUpdateView; recoveryRequired: boolean }) {
  // null follows the update: the log opens by itself when it fails.
  const [showLog, setShowLog] = useState<boolean | null>(null)
  useEffect(() => setShowLog(null), [update.id])
  const logVisible = showLog ?? update.state === 'failed'

  return (
    <div className="grid gap-1.5">
      {update.state === 'running' ? (
        <div className="grid gap-1.5 rounded-lg bg-inset px-3 py-2.5 shadow-[inset_0_0_0_1px_var(--line)]">
          <div className="font-medium">{RUNNING[update.kind]}</div>
          <div className="bar live">
            <i style={{ width: '100%' }} />
          </div>
          <div className="task run -mx-1 [overflow-wrap:anywhere]">
            <span className="ic">
              <LoaderCircle size={15} className="spin" />
            </span>
            <span className="shimmer">{update.stepLabel || 'Starting the update…'}</span>
            <span />
          </div>
          {update.resumed && <div className="text-[12px] text-ink-3">Picking up where the last attempt stopped.</div>}
          <div className="font-medium text-warn">{DONT_POWER_OFF}</div>
        </div>
      ) : (
        <Notice tone={update.state === 'succeeded' ? 'good' : update.state === 'interrupted' ? 'warn' : 'bad'} onClose={() => dismissUpdate(update.id)}>
          <div>
            <b>{update.state === 'failed' && update.firmware ? "The QIDI firmware update didn't finish" : RESULT[update.state][update.kind]}</b>
            {update.finishedAt && <span className="text-[12px] text-ink-2"> · {fmtTime(update.finishedAt)}</span>}
          </div>
          {update.stepLabel && <div className="text-ink-2">It stopped at: {update.stepLabel}.</div>}
          {update.message && <div className="text-ink-2">{update.message}</div>}
          {update.state === 'interrupted' && <div className="text-ink-2">Finish it with Update now or by running the Valve Oil installer.</div>}
          {update.canContinue && !recoveryRequired && (
            <div className="text-ink-2">Continue it with Update now or by running the Valve Oil installer.</div>
          )}
        </Notice>
      )}
      {update.log.length > 0 && (
        <>
          <button className="btn btn-sm btn-ghost justify-self-start px-1.5!" onClick={() => setShowLog(!logVisible)}>
            <ChevronDown size={14} className={logVisible ? 'rotate-180' : ''} />
            {logVisible ? 'Hide log' : 'Show log'}
          </button>
          {logVisible && <Log lines={update.log} />}
        </>
      )}
    </div>
  )
}

// The report the installer leaves in Klipper's log folder for a board it can't support yet, as the printer warnings
// name it (installer/runtime/board_firmware.py).
const boardReportFile = (id: string) =>
  id === 'thr' ? 'tltg_thr_firmware.json' : id === 'mcu' ? 'tltg_mainboard_firmware.json' : `tltg_box_firmware_${id}.json`

/** What reporting a board Valve Oil can't support yet takes: the prefilled issue, what the form still asks, and the report. */
function BoardSupport({ board, url }: { board: VoBoardView; url: string }) {
  const report = boardReportFile(board.id)
  return (
    <div className="tone-warn mt-2 grid gap-1.5 rounded-xl px-3 py-2.5">
      <b className="text-ink">Open a support issue for {board.name}</b>
      <ol className="list-decimal grid gap-0.5 pl-5 text-ink-2">
        <li>Open the issue. The form comes with this printer, its QIDI firmware, and {board.name}'s report filled in.</li>
        <li>Say how {board.name} got its firmware: it came with the printer, you bought it on its own, or QIDI sent it as a replacement.</li>
        <li>If the form's report says it was too long for the link, download the report and attach it.</li>
      </ol>
      <div className="flex flex-wrap gap-2">
        <a className="btn btn-sm" href={url} target="_blank" rel="noopener noreferrer">
          Open a support issue <ExternalLink size={13} />
        </a>
        <a className="btn btn-sm btn-ghost" href={client.fileUrl('logs', report)} download={report}>
          <Download size={13} /> Download the report
        </a>
      </div>
    </div>
  )
}

function BoardsCard({ view }: { view: VoStatusView }) {
  if (view.boards.length === 0) return null
  return (
    <Card title="Boards" icon={<Cpu size={14} />}>
      <div className="grid">
        {view.boards.map((board, i) => {
          const explanation = boardExplanation(board)
          return (
            <div key={board.id} className={`py-2.5 ${i > 0 ? 'border-t border-line' : 'pt-0'}`}>
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div className="font-medium">{board.name}</div>
                  <div className="text-[12.5px] text-ink-3">{boardFirmwareText(board)}</div>
                </div>
                <span className={`pill ${BOARD_TONE[board.badge]} flex-none py-0.5!`}>{BOARD_BADGE[board.badge]}</span>
              </div>
              {(explanation || board.shownDetail) && (
                <div className="mt-1.5 text-ink-2">
                  {explanation}
                  {board.shownDetail && <div className={`[overflow-wrap:anywhere] ${explanation ? 'mt-0.5 text-[12px] text-ink-3' : ''}`}>{board.shownDetail}</div>}
                </div>
              )}
              {board.reportUrl && (board.state === 'unrecognized_bootloader' || board.state === 'unrecognized_firmware') ? (
                <BoardSupport board={board} url={board.reportUrl} />
              ) : board.reportUrl && (
                <a className="btn btn-sm mt-2" href={board.reportUrl} target="_blank" rel="noopener noreferrer">
                  Report this board <ExternalLink size={13} />
                </a>
              )}
            </div>
          )
        })}
      </div>
    </Card>
  )
}

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronDown, CircleAlert, CircleCheck, Cpu, ExternalLink, Info, LoaderCircle, RefreshCw, TriangleAlert, X } from 'lucide-react'
import { Card, Dialog, Toggle } from '../components/ui'
import {
  TLTG_UPDATE_NOW_BLOCKERS,
  tltgSentence,
  type TltgBoardBadge,
  type TltgBoardNote,
  type TltgBoardView,
  type TltgStatusBlocker,
  type TltgStatusBoard,
  type TltgStatusPending,
  type TltgStatusView,
  type TltgUpdateView,
} from '../qidi/tltg-status'
import {
  autoUpdateDisabled,
  clearAutoUpdateError,
  clearUpdateNowError,
  dismissUpdate,
  reconnecting,
  reconnectingLongMinutes,
  setAutoUpdate,
  shownUpdate,
  updateNow,
  updateNowDisabled,
  useValveOil,
  useValveOilView,
  watchValveOil,
  type ValveOilState,
} from '../state/valve-oil'

// The wording is the Fluidd fork's (app.tltg_valve_oil in src/locales/en.yaml there); keep the two in step.

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

const RUNNING: Record<TltgUpdateView['kind'], string> = {
  update_now: 'Updating…',
  automatic: 'Automatic update running…',
  install: 'The Valve Oil installer is running…',
  other: 'Updating…',
}
const RESULT: Record<Exclude<TltgUpdateView['state'], 'running'>, Record<TltgUpdateView['kind'], string>> = {
  succeeded: { update_now: 'The update finished', automatic: 'The automatic update finished', install: 'The Valve Oil installer finished', other: 'The update finished' },
  failed: { update_now: 'The update failed', automatic: 'The automatic update failed', install: 'The Valve Oil installer failed', other: 'The update failed' },
  interrupted: {
    update_now: 'The update stopped before it finished',
    automatic: 'The automatic update stopped before it finished',
    install: 'The Valve Oil installer stopped before it finished',
    other: 'The update stopped before it finished',
  },
}

const BOARD_BADGE: Record<TltgBoardBadge, string> = {
  current: 'Up to date',
  needs_image: 'Update waiting',
  after_firmware: 'Waiting for QIDI firmware',
  unrecognized_firmware: 'Firmware not recognized',
  unrecognized_bootloader: 'Not supported yet',
  held: "Kept on QIDI's firmware",
  touchscreen: 'Update from the touchscreen',
  unknown: 'Not checked',
}
const BOARD_TONE: Record<TltgBoardBadge, string> = {
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

function boardFirmwareText(board: TltgStatusBoard) {
  const version = board.firmware ? `Firmware ${board.firmware}` : 'Firmware not read'
  const build = board.build ? BOARD_BUILD[board.build] : undefined
  return build ? `${version} · ${build}` : version
}

const BOARD_NOTE: Record<Exclude<TltgBoardNote, 'after_firmware'>, string> = {
  unrecognized_bootloader:
    "Valve Oil doesn't recognize this board's bootloader yet, so it doesn't update this board or the printer's QIDI firmware. Report it so a later release can support it.",
  unrecognized_bootloader_box:
    "Valve Oil doesn't recognize this Box's bootloader yet, so it leaves this Box as it is and keeps updating the rest of the printer. Report it so a later release can support it.",
  unrecognized_firmware: "This board runs firmware Valve Oil doesn't recognize, so Valve Oil leaves it as it is. A later release that recognizes it will update it.",
  unknown: "Valve Oil hasn't been able to read this board's firmware yet.",
  held_box: "This Box was put back on QIDI's firmware. Valve Oil leaves it there until you install the next Valve Oil release.",
  held_closed_loop: "Valve Oil left the closed-loop motor drivers on their firmware. Update them from the touchscreen's firmware page.",
  held: "Valve Oil leaves this board's firmware as it is.",
}

function boardExplanation(board: TltgBoardView) {
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
function blockedText(blockedBy: TltgStatusBlocker[]) {
  if (blockedBy.length === 0) return 'Not available right now.'
  return blockedBy
    .map((b) => (TLTG_UPDATE_NOW_BLOCKERS.includes(b.code) ? UPDATE_NOW_BLOCKED[b.code] : b.code === 'recovery_required' ? tltgSentence(b.message) : `Not available now: ${b.message}.`))
    .join(' ')
}

function waitingText(item: TltgStatusPending, boards: TltgStatusBoard[]) {
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

function noticeText(view: TltgStatusView) {
  switch (view.notice) {
    // Installing the installed release again says so, rather than "the update".
    case 'auto_off':
      return `${view.reinstall ? 'Valve Oil needs to be installed again.' : 'An update is waiting.'} Automatic updates are off, so install it with Update now or by running the Valve Oil installer.`
    case 'blocked':
      return `Automatic updates are on, but ${view.reinstall ? "Valve Oil hasn't been installed again yet" : "the update hasn't been installed yet"}: ${view.reasons.join('; ')}.`
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

function confirmText(view: TltgStatusView): { text: string; strong: boolean } {
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
      <Card title="Valve Oil" icon={<RefreshCw size={14} />}>
        <div className="flex items-start gap-2.5 text-ink-2">
          {reconnecting(s) ? <LoaderCircle size={16} className="spin mt-0.5 flex-none" /> : <Info size={16} className="mt-0.5 flex-none text-ink-3" />}
          <span>{text}</span>
        </div>
        {s.expectUpdate && reconnecting(s) && <div className="mt-2 font-medium text-warn">{DONT_POWER_OFF}</div>}
      </Card>
    )
  }

  return (
    <div className="grid content-start gap-3.5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] xl:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
      <ValveOilCard s={s} view={view} />
      <BoardsCard view={view} />
    </div>
  )
}

function ValveOilCard({ s, view }: { s: ValveOilState; view: TltgStatusView }) {
  const [confirm, setConfirm] = useState(false)
  const update = shownUpdate(view, s.dismissed)
  const disabled = updateNowDisabled(s)
  const confirmation = confirmText(view)
  const notice = view.notice !== 'none' && !view.updating ? noticeText(view) : ''
  const actionLabel = view.updateNow.action === 'continue' ? 'Continue the update' : 'Update now'
  const recoveryRequired = view.updateNow.blockedBy.some((b) => b.code === 'recovery_required')

  return (
    <Card title="Valve Oil" icon={<RefreshCw size={14} />}>
      <div className="grid">
        <div className="row-k">
          <span className="k">Valve Oil release</span>
          <span className="v">{view.release || 'Unknown'}</span>
        </div>
        <div className="row-k">
          <span className="k">
            {view.printerName || 'Printer'}
            <span className="block text-[12px] text-ink-3">QIDI firmware</span>
          </span>
          <span className="v">{view.firmware || 'Unknown'}</span>
          {view.targetFirmware && <span className="col-span-2 -mt-1 text-right text-[12px] text-ink-3">This Valve Oil release updates it to {view.targetFirmware}</span>}
        </div>
      </div>

      <div className="sep my-2" />
      <label className="flex items-center gap-3 py-1">
        <div className="min-w-0 flex-1">
          <div className="font-medium">Automatic updates</div>
          <div className="text-[12.5px] text-ink-3">Install new Valve Oil releases, QIDI firmware, and board firmware by themselves while the printer is idle.</div>
        </div>
        {s.autoUpdateRequest != null && <LoaderCircle size={15} className="spin flex-none text-ink-3" />}
        <Toggle checked={s.autoUpdateRequest ?? s.status?.auto_update.enabled === true} disabled={autoUpdateDisabled(s)} label="Automatic updates" onChange={setAutoUpdate} />
      </label>

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
            <button className="btn btn-sm btn-primary" disabled={disabled} onClick={() => setConfirm(true)}>
              {s.updateNowPending && <LoaderCircle size={13} className="spin" />}
              {actionLabel}
            </button>
            {!view.updateNow.allowed && <div className="text-right text-[12px] text-ink-3">{blockedText(view.updateNow.blockedBy)}</div>}
          </div>
        )}
      </div>

      <div className="mt-1 grid gap-2 empty:hidden">
        {notice && <Notice tone={view.notice === 'next_check' ? 'info' : 'warn'}>{notice}</Notice>}
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

function Notice({ tone, icon, onClose, children }: { tone: 'info' | 'warn' | 'bad' | 'good'; icon?: ReactNode; onClose?: () => void; children: ReactNode }) {
  const cls = { info: 'tone-accent', warn: 'tone-warn', bad: 'tone-bad', good: 'tone-good' }[tone]
  const Icon = { info: Info, warn: TriangleAlert, bad: CircleAlert, good: CircleCheck }[tone]
  return (
    <div className={`${cls} flex items-start gap-2.5 rounded-lg px-3 py-2`}>
      <span className="mt-0.5 flex-none">{icon ?? <Icon size={15} />}</span>
      <div className="min-w-0 flex-1 text-ink [overflow-wrap:anywhere]">{children}</div>
      {onClose && (
        <button className="iconbtn -my-1.5 -mr-2 h-7 w-7 flex-none" onClick={onClose} aria-label="Dismiss">
          <X size={15} />
        </button>
      )}
    </div>
  )
}

function UpdateProgress({ update, recoveryRequired }: { update: TltgUpdateView; recoveryRequired: boolean }) {
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

function Log({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLPreElement>(null)
  const following = useRef(true)
  // Follow the newest line unless the reader has scrolled back.
  useLayoutEffect(() => {
    const el = ref.current
    if (el && following.current) el.scrollTop = el.scrollHeight
  }, [lines])
  return (
    <pre
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget
        following.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 8
      }}
      className="m-0 max-h-[240px] overflow-auto rounded-lg bg-inset p-2.5 font-mono text-[12px] whitespace-pre-wrap text-ink-2 shadow-[inset_0_0_0_1px_var(--line)] [overflow-wrap:anywhere]"
    >
      {lines.join('\n')}
    </pre>
  )
}

function BoardsCard({ view }: { view: TltgStatusView }) {
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
                <span className={`${BOARD_TONE[board.badge]} flex-none rounded-md px-2 py-0.5 text-[12px] font-medium`}>{BOARD_BADGE[board.badge]}</span>
              </div>
              {(explanation || board.shownDetail) && (
                <div className="mt-1.5 text-ink-2">
                  {explanation}
                  {board.shownDetail && <div className={`[overflow-wrap:anywhere] ${explanation ? 'mt-0.5 text-[12px] text-ink-3' : ''}`}>{board.shownDetail}</div>}
                </div>
              )}
              {board.reportUrl && (
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

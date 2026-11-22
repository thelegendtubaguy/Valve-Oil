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
import { locale, t, type MessageKey } from '../i18n'

// The wording is the Fluidd fork's (app.vo_valve_oil in src/locales/en.yaml there); keep the two in step.

const fmtTime = (ms: number) => new Date(ms).toLocaleString(locale(), { dateStyle: 'medium', timeStyle: 'short' })
/** A time later today as just the time, and one on another day with its date. */
const fmtSoon = (ms: number) => (new Date(ms).toDateString() === new Date().toDateString() ? new Date(ms).toLocaleTimeString(locale(), { timeStyle: 'short' }) : fmtTime(ms))

function DontPowerOff() {
  return <>{t('updates.dont_power_off')}</>
}
/** "Don't turn the printer off until it finishes.", looked up as it's shown. */
export const DONT_POWER_OFF = <DontPowerOff />

export function reconnectText(s: ValveOilState) {
  const minutes = reconnectingLongMinutes(s)
  return minutes != null ? t('updates.reconnecting_long', { count: minutes }) : t('updates.reconnecting')
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
// The builds a board's firmware can be, as the printer names them; another isn't shown.
const BOARD_BUILDS = ['patched', 'qidi', 'stock', 'superseded']

function boardFirmwareText(board: VoStatusBoard) {
  const version = board.firmware ? t('updates.boards.firmware', { version: board.firmware }) : t('updates.boards.firmware_unread')
  const build = board.build && BOARD_BUILDS.includes(board.build) ? t(`updates.boards.build.${board.build}` as MessageKey) : undefined
  return build ? `${version} · ${build}` : version
}

function boardExplanation(board: VoBoardView) {
  if (board.note === 'after_firmware')
    return board.noteVersion ? t('updates.boards.note.after_firmware_version', { version: board.noteVersion }) : t('updates.boards.note.after_firmware')
  return board.note ? t(`updates.boards.note.${board.note}`) : ''
}

// A recovery is shown as the printer says it, as its message names what to run first; a code this page doesn't word
// is shown as the printer says it too.
function blockedText(blockedBy: VoStatusBlocker[]) {
  if (blockedBy.length === 0) return t('updates.blocked.none')
  return blockedBy
    .map((b) =>
      VO_UPDATE_NOW_BLOCKERS.includes(b.code) ? t(`updates.blocked.code.${b.code}` as MessageKey) : b.code === 'recovery_required' ? voSentence(b.message) : t('updates.blocked.other', { message: b.message }),
    )
    .join(' ')
}

function waitingText(item: VoStatusPending, boards: VoStatusBoard[]) {
  switch (item.kind) {
    case 'release':
      if (item.reinstall) return t('updates.waiting.reinstall')
      if (!item.version) return t('updates.waiting.release_new')
      return item.current ? t('updates.waiting.release_current', { version: item.version, current: item.current }) : `Valve Oil ${item.version}`
    case 'firmware':
      if (!item.version) return t('updates.waiting.firmware_new')
      return item.current ? t('updates.waiting.firmware_current', { version: item.version, current: item.current }) : t('updates.waiting.firmware', { version: item.version })
    default: {
      const name = boards.find((b) => b.id === item.board)?.name ?? item.board
      return name ? t('updates.waiting.board', { name }) : t('updates.waiting.board_unnamed')
    }
  }
}

function noticeText(view: VoStatusView) {
  // Firmware takes the printer out of use for longer, which the notice adds.
  const busy = view.firmwareWaiting ? ` ${t('updates.notice.firmware_busy')}` : ''
  switch (view.notice) {
    // Installing the installed release again says so, rather than "the update".
    case 'auto_off':
      return view.reinstall ? t('updates.notice.auto_off_reinstall') : t('updates.notice.auto_off')
    case 'blocked':
      return t(view.reinstall ? 'updates.notice.blocked_reinstall' : 'updates.notice.blocked', { reasons: view.reasons.join('; ') })
    case 'window': {
      const hours = view.updateWindow?.hours
      const window = `${hours ? voUpdateWindowLabel(hours) : ''} ${view.updateWindow?.timezone ?? 'UTC'}`
      return view.reinstall ? t('updates.notice.window_reinstall', { window }) : t('updates.notice.window', { window }) + busy
    }
    case 'next_check': {
      if (view.nextCheckAt != null) {
        const time = fmtSoon(view.nextCheckAt)
        return view.reinstall ? t('updates.notice.next_check_reinstall_at', { time }) : t('updates.notice.next_check_at', { time }) + busy
      }
      return view.reinstall ? t('updates.notice.next_check_reinstall') : t('updates.notice.next_check') + busy
    }
    default:
      return ''
  }
}

function confirmText(view: VoStatusView): { text: string; strong: boolean } {
  const release = view.waiting.find((item) => item.kind === 'release')
  if (view.updateNow.action === 'continue') return { strong: true, text: t('updates.confirm.continue') }
  if (view.updateNow.action === 'finish') return { strong: true, text: t('updates.confirm.finish') }
  if (view.firmwareWaiting) return { strong: true, text: t('updates.confirm.firmware') }
  if (release?.reinstall && release.version) return { strong: false, text: t('updates.confirm.reinstall', { version: release.version }) }
  return {
    strong: false,
    text: release?.version && !release.reinstall ? t('updates.confirm.install_version', { version: release.version }) : t('updates.confirm.install_latest'),
  }
}

export function UpdatesScreen() {
  useEffect(watchValveOil, [])
  const s = useValveOil()
  const view = useValveOilView()

  if (!view) {
    const text = reconnecting(s) ? reconnectText(s) : s.unavailable ? t('updates.unavailable') : t('updates.loading')
    return (
      <div className="grid content-start gap-3.5">
        <PageHeader title={t('updates.title')} />
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
      <PageHeader title={t('updates.title')} />
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
    if (changed && savesBrowserTimezone) return t('updates.window.timezone.this_browser', { timezone: browser })
    if (window.timezone == null) return t('updates.window.timezone.utc')
    const timezone = window.timezone
    switch (window.timezone_source) {
      case 'lookup':
        return t('updates.window.timezone.lookup', { timezone })
      case 'operator':
        return t('updates.window.timezone.operator', { timezone })
      case 'browser':
        return timezone === browser ? t('updates.window.timezone.this_browser', { timezone }) : t('updates.window.timezone.browser', { timezone })
      default:
        return t('updates.window.timezone.other', { timezone })
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
          <div className="font-medium">{t('updates.window.title')}</div>
          <div className="text-[12.5px] text-ink-3">{hours ? t('updates.window.hours') : t('updates.window.any_hour')}</div>
          {hours && (
            <div className="text-[12.5px] text-ink-3">
              {timezoneText}
              {offeredTimezone && (
                <button className="ml-1 cursor-pointer border-0 bg-transparent p-0 font-medium text-accent hover:underline disabled:opacity-45" disabled={disabled} onClick={() => void setUpdateWindow({ timezone: offeredTimezone })}>
                  {t('updates.window.use_timezone', { timezone: offeredTimezone })}
                </button>
              )}
            </div>
          )}
        </div>
        <select
          className="field w-auto"
          aria-label={t('updates.window.title')}
          value={hours ? 'hours' : 'any'}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value === 'hours' ? (hours ?? window.hours ?? { ...SUGGESTED_HOURS }) : null)}
        >
          <option value="any">{t('updates.window.any_time')}</option>
          <option value="hours">{t('updates.window.set_hours')}</option>
        </select>
      </div>
      {(hours || changed) && (
        <div className="flex flex-wrap items-center justify-end gap-2">
          {hours && (
            <>
              {hourSelect(hours.start, t('updates.window.start_hour'), (start) => setHours({ start }))}
              <span className="text-ink-2">{t('updates.window.to')}</span>
              {hourSelect(hours.end, t('updates.window.end_hour'), (end) => setHours({ end }))}
            </>
          )}
          {changed && (
            <>
              <button className="btn btn-sm btn-primary" disabled={disabled || !valid} onClick={save}>
                {pending && <LoaderCircle size={13} className="spin" />}
                {t('common.save')}
              </button>
              <button className="btn btn-sm" disabled={pending} onClick={() => setDraft(undefined)}>
                {t('common.cancel')}
              </button>
            </>
          )}
        </div>
      )}
      {!valid && <div className="text-right text-[12px] text-bad">{t('updates.window.invalid')}</div>}
      {error && (
        <Notice tone="bad" onClose={clearWindowError}>
          {t('updates.window.error', { error })}
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
  const actionLabel = view.updateNow.action === 'continue' ? t('updates.card.continue') : t('updates.card.update_now')
  const recoveryRequired = view.updateNow.blockedBy.some((b) => b.code === 'recovery_required')

  return (
    <Card>
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="section-label">Valve Oil{view.printerName ? ` · ${view.printerName}` : ''}</div>
          <div className="mt-1 flex items-center gap-2.5 text-[22px] font-bold tracking-[-0.01em]">
            {view.updating || !s.fresh ? (
              <LoaderCircle size={22} className="spin flex-none text-accent-ink" />
            ) : view.waiting.length > 0 ? (
              <Download size={22} className="flex-none text-accent-ink" />
            ) : (
              <CircleCheck size={22} className="flex-none text-good" />
            )}
            {/* Until this screen's own answer arrives, what the page has may be from when it loaded, so it says nothing either way. */}
            {view.updating ? t('updates.card.updating') : !s.fresh ? t('updates.card.checking') : view.waiting.length > 0 ? t('updates.card.waiting') : t('updates.card.up_to_date')}
          </div>
        </div>
      </div>

      <div className="mt-3 rounded-xl bg-inset px-3.5 py-1 shadow-[inset_0_0_0_1px_var(--line)]">
        <div className="row-k">
          <span className="k">{t('updates.card.release')}</span>
          <span className="v">{view.release || t('updates.card.unknown')}</span>
          {view.devBuild != null && <span className="col-span-2 -mt-1 text-right text-[12px] text-ink-3">{view.devBuild ? t('updates.card.dev_build_commit', { commit: view.devBuild }) : t('updates.card.dev_build')}</span>}
        </div>
        <div className="row-k">
          <span className="k">{t('updates.card.qidi_firmware')}</span>
          <span className="v">{view.firmware || t('updates.card.unknown')}</span>
          {view.targetFirmware && <span className="col-span-2 -mt-1 text-right text-[12px] text-ink-3">{t('updates.card.target_firmware', { version: view.targetFirmware })}</span>}
        </div>
      </div>

      <div className="mt-2" />
      <label className="flex items-center gap-3 py-1">
        <div className="min-w-0 flex-1">
          <div className="font-medium">{t('updates.card.auto')}</div>
          <div className="text-[12.5px] text-ink-3">{t('updates.card.auto_sub')}</div>
        </div>
        {s.autoUpdateRequest != null && <LoaderCircle size={15} className="spin flex-none text-ink-3" />}
        <Toggle checked={s.autoUpdateRequest ?? s.status?.auto_update.enabled === true} disabled={autoUpdateDisabled(s)} label={t('updates.card.auto')} onChange={setAutoUpdate} />
      </label>
      {view.updateWindow && <UpdateWindow window={view.updateWindow} disabled={autoUpdateDisabled(s)} pending={s.windowPending} error={s.windowError} />}
      {/* QIDI's setting, through Moonraker; it hides itself while Moonraker is down or isn't QIDI's. */}
      <LanOnly className="mt-2 border-t border-line pt-2" />

      <div className="sep my-2" />
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2 py-1">
        <div className="min-w-[180px] flex-1">
          <div className="font-medium">{t('updates.card.waiting')}</div>
          {!s.fresh ? (
            <div className="text-[12.5px] text-ink-3">{t('updates.card.checking')}</div>
          ) : view.waiting.length === 0 ? (
            <div className="text-[12.5px] text-ink-3">{t('updates.card.nothing_waiting')}</div>
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
            {t('updates.card.auto_error', { error: s.autoUpdateError })}
          </Notice>
        )}
        {s.updateNowError && (
          <Notice tone="bad" onClose={clearUpdateNowError}>
            {s.updateNowBlockedBy.length > 0 ? t('updates.card.update_now_blocked', { reasons: blockedText(s.updateNowBlockedBy) }) : t('updates.card.update_now_error', { error: s.updateNowError })}
          </Notice>
        )}
        {view.checkFailed != null && <Notice tone="warn">{view.checkFailed ? t('updates.card.check_failed_message', { message: view.checkFailed }) : t('updates.card.check_failed')}</Notice>}
        {update && <UpdateProgress update={update} recoveryRequired={recoveryRequired} />}
      </div>

      {view.supportIssueUrl && (
        <>
          <div className="sep my-2" />
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2 py-1">
            <div className="min-w-[180px] flex-1">
              <div className="font-medium">{t('updates.card.report')}</div>
              <div className="text-[12.5px] text-ink-3">{t('updates.card.report_sub')}</div>
            </div>
            <button className="btn btn-sm ml-auto" disabled={s.supportPending} onClick={reportProblem}>
              {s.supportPending ? <LoaderCircle size={13} className="spin" /> : <ExternalLink size={13} />}
              {t('updates.card.open_issue')}
            </button>
          </div>
          {s.supportError && (
            <Notice tone="bad" onClose={clearSupportError}>
              {t('updates.card.support_error', { error: s.supportError })}
            </Notice>
          )}
        </>
      )}

      {view.checkedAt && <div className="mt-3 border-t border-line pt-2 text-[12px] text-ink-3">{t('updates.card.last_checked', { time: fmtTime(view.checkedAt) })}</div>}

      <Dialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title={actionLabel}
        footer={
          <>
            <button className="btn" onClick={() => setConfirm(false)}>
              {t('common.cancel')}
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
          <div className="font-medium">{t(`updates.progress.running.${update.kind}`)}</div>
          <div className="bar live">
            <i style={{ width: '100%' }} />
          </div>
          <div className="task run -mx-1 [overflow-wrap:anywhere]">
            <span className="ic">
              <LoaderCircle size={15} className="spin" />
            </span>
            <span className="shimmer">{update.stepLabel || t('updates.progress.starting')}</span>
            <span />
          </div>
          {update.resumed && <div className="text-[12px] text-ink-3">{t('updates.progress.resumed')}</div>}
          <div className="font-medium text-warn">{DONT_POWER_OFF}</div>
        </div>
      ) : (
        <Notice tone={update.state === 'succeeded' ? 'good' : update.state === 'interrupted' ? 'warn' : 'bad'} onClose={() => dismissUpdate(update.id)}>
          <div>
            <b>{update.state === 'failed' && update.firmware ? t('updates.progress.firmware_failed') : t(`updates.progress.result.${update.state}.${update.kind}`)}</b>
            {update.finishedAt && <span className="text-[12px] text-ink-2"> · {fmtTime(update.finishedAt)}</span>}
          </div>
          {update.stepLabel && <div className="text-ink-2">{t('updates.progress.stopped_at', { step: update.stepLabel })}</div>}
          {update.message && <div className="text-ink-2">{update.message}</div>}
          {update.state === 'interrupted' && <div className="text-ink-2">{t('updates.progress.finish_hint')}</div>}
          {update.canContinue && !recoveryRequired && (
            <div className="text-ink-2">{t('updates.progress.continue_hint')}</div>
          )}
        </Notice>
      )}
      {update.log.length > 0 && (
        <>
          <button className="btn btn-sm btn-ghost justify-self-start px-1.5!" onClick={() => setShowLog(!logVisible)}>
            <ChevronDown size={14} className={logVisible ? 'rotate-180' : ''} />
            {logVisible ? t('updates.progress.hide_log') : t('updates.progress.show_log')}
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
      <b className="text-ink">{t('updates.boards.support.title', { name: board.name })}</b>
      <ol className="list-decimal grid gap-0.5 pl-5 text-ink-2">
        <li>{t('updates.boards.support.step_open', { name: board.name })}</li>
        <li>{t('updates.boards.support.step_origin', { name: board.name })}</li>
        <li>{t('updates.boards.support.step_report')}</li>
      </ol>
      <div className="flex flex-wrap gap-2">
        <a className="btn btn-sm" href={url} target="_blank" rel="noopener noreferrer">
          {t('updates.boards.support.open')} <ExternalLink size={13} />
        </a>
        <a className="btn btn-sm btn-ghost" href={client.fileUrl('logs', report)} download={report}>
          <Download size={13} /> {t('updates.boards.support.download')}
        </a>
      </div>
    </div>
  )
}

function BoardsCard({ view }: { view: VoStatusView }) {
  if (view.boards.length === 0) return null
  return (
    <Card title={t('updates.boards.title')} icon={<Cpu size={14} />}>
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
                <span className={`pill ${BOARD_TONE[board.badge]} flex-none py-0.5!`}>{t(`updates.boards.badge.${board.badge}`)}</span>
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
                  {t('updates.boards.report')} <ExternalLink size={13} />
                </a>
              )}
            </div>
          )
        })}
      </div>
    </Card>
  )
}

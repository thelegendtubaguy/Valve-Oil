// Valve Oil's update status, from the status service beside Moonraker (src/qidi/vo-status.ts). The polling and
// request handling follow the Fluidd fork's VoValveOilSettings.vue, kept outside React here so the app shell can
// keep saying an update is running while Moonraker and Klipper are down for it.
//
// The service is asked once when the page opens and whenever Moonraker or Klipper goes away, then polled while the
// Updates screen is open or an update is running.
//
// While an update runs, a service that doesn't answer is taken to be restarting for it. Klipper's vo_printer_warnings
// reads the same run record as the service and says while a run is going, so once Klipper has been ready for a while
// without saying so, the page stops waiting: the update stopped (its own warning says so) or ended unseen.
import { createStore, useStore } from '../lib/store'
import { getRenamedItem } from '../lib/storage'
import { errorText, saveBlob, saveLocal } from '../lib/browser'
import { client, printer, type PrinterState } from './printer'
import {
  parseVoKlipperAnswer,
  parseVoStatus,
  parseVoStatusJob,
  voStatusApiBase,
  voStatusPollFailure,
  voStatusPollInterval,
  voStatusRequest,
  VoStatusRequestError,
  voStatusView,
  voShownUpdate,
  voSupportBundle,
  VO_STATUS_RECONNECT_PATIENCE_MS,
  type VoKlipperTree,
  type VoStatus,
  type VoStatusBlocker,
  type VoStatusView,
  type VoUpdateView,
  type VoUpdateWindowHours,
} from '../qidi/vo-status'
import { t } from '../i18n'

export type ValveOilState = {
  status: VoStatus | null
  /** The status was asked for since the Updates screen opened. Until then it may be hours old, from when the page loaded. */
  fresh: boolean
  /** The service answered that it isn't there, or with something this page doesn't understand. */
  unavailable: boolean
  failures: number
  /** An update is running or was just started, so failed requests mean the printer is restarting its services. */
  expectUpdate: boolean
  reconnectingSince: number | null
  now: number
  autoUpdateRequest: boolean | null
  autoUpdateError: string
  /** A change to the update window or its timezone is being saved. */
  windowPending: boolean
  windowError: string
  updateNowPending: boolean
  updateNowError: string
  /** Why the printer refused Update now, when it said. */
  updateNowBlockedBy: VoStatusBlocker[]
  dismissed: string[]
  supportPending: boolean
  supportError: string
  /** The Klipper a switch is being asked for. */
  klipperRequest: VoKlipperTree | null
  klipperError: string
  /** Why the printer refused the switch, when it said. */
  klipperBlockedBy: VoStatusBlocker[]
}

// The same keys as the Fluidd fork's section: both are served from the printer's one origin, so a result closed in
// one is closed in the other. Values saved under the keys' old tltg- names carry over.
const DISMISSED_UPDATES_KEY = 'vo-status.dismissed-updates'
const OLD_DISMISSED_UPDATES_KEY = 'tltg-status.dismissed-updates'
const DISMISSED_UPDATES_KEPT = 20
const UPDATING_KEY = 'vo-status.updating-since'
const OLD_UPDATING_KEY = 'tltg-status.updating-since'
const UPDATING_KEPT_MS = 6 * 60 * 60 * 1000
// How long the service goes unanswered, and Klipper ready without an update running, before the page stops waiting.
// Long enough for Update now's installer to start and record its run, and for Klipper to read it.
export const KLIPPER_QUIET_MS = 60 * 1000
const UPDATE_RUNNING_WARNING = 'valve_oil_update_running'

function loadDismissed(): string[] {
  try {
    const value: unknown = JSON.parse(getRenamedItem(localStorage, DISMISSED_UPDATES_KEY, OLD_DISMISSED_UPDATES_KEY) ?? '[]')
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : []
  } catch {
    return []
  }
}

function loadUpdating(): boolean {
  try {
    const since = Number(getRenamedItem(sessionStorage, UPDATING_KEY, OLD_UPDATING_KEY))
    return Number.isFinite(since) && since > 0 && Date.now() - since < UPDATING_KEPT_MS
  } catch {
    return false
  }
}

function saveUpdating(updating: boolean) {
  try {
    if (!updating) sessionStorage.removeItem(UPDATING_KEY)
    else if (!sessionStorage.getItem(UPDATING_KEY)) sessionStorage.setItem(UPDATING_KEY, String(Date.now()))
  } catch {
    // Private browsing can refuse storage; this page still knows.
  }
}

export const valveOil = createStore<ValveOilState>({
  status: null,
  fresh: false,
  unavailable: false,
  failures: 0,
  expectUpdate: loadUpdating(),
  reconnectingSince: null,
  now: Date.now(),
  autoUpdateRequest: null,
  autoUpdateError: '',
  windowPending: false,
  windowError: '',
  updateNowPending: false,
  updateNowError: '',
  updateNowBlockedBy: [],
  dismissed: loadDismissed(),
  supportPending: false,
  supportError: '',
  klipperRequest: null,
  klipperError: '',
  klipperBlockedBy: [],
})

const patch = (changes: Partial<ValveOilState>) => valveOil.set((s) => ({ ...s, ...changes }))

// Valve Oil UI talks only to the Moonraker of the web server that served it. In development the dev server proxies
// /vo-status to the printer or the fake (vite.config.ts), so the page's own origin is used there too.
const apiBase = () => voStatusApiBase(import.meta.env.DEV ? location.origin : client.httpBase, location)

async function request(path: string, method: 'GET' | 'POST' = 'GET', signal?: AbortSignal, body?: Record<string, unknown>) {
  const base = apiBase()
  if (!base) throw new VoStatusRequestError(0, t('updates.status.not_connected'))
  return voStatusRequest(base, path, { method, token: null, signal, body })
}

/** Calls the status service of the page's own printer, as the Updates screen does: for the System screen's Diagnostics check. */
export const statusRequest = request

// ---- polling ----
let watchers = 0
let timer: ReturnType<typeof setTimeout> | undefined
let abortController: AbortController | null = null

const updating = (s: ValveOilState) => s.expectUpdate || s.status?.busy === true

function wanted() {
  return !document.hidden && (watchers > 0 || updating(valveOil.get()))
}

function stop() {
  clearTimeout(timer)
  timer = undefined
  abortController?.abort()
  abortController = null
}

function schedule() {
  clearTimeout(timer)
  timer = undefined
  if (!wanted()) return
  const s = valveOil.get()
  timer = setTimeout(poll, voStatusPollInterval(s.status, s.reconnectingSince != null))
}

function applyStatus(status: VoStatus) {
  patch({ status, fresh: true, unavailable: false, failures: 0, reconnectingSince: null, expectUpdate: status.busy })
  saveUpdating(status.busy)
}

/** Since when Klipper has been ready and saying no update is running; null while it can't say or says one is. */
let klipperQuietSince: number | null = null

function klipperSeesUpdate(s: PrinterState): boolean | null {
  if (s.conn !== 'open' || s.klippy !== 'ready') return null
  const warnings = (s.status.vo_printer_warnings as { warnings?: unknown } | undefined)?.warnings
  // QIDI's Klipper, and a Klipper without Valve Oil's macros, can't say.
  return Array.isArray(warnings) ? valveOilWarningIds(warnings).includes(UPDATE_RUNNING_WARNING) : null
}

function onPollFailed() {
  const s = valveOil.get()
  const failures = s.failures + 1
  const now = Date.now()
  const outcome = voStatusPollFailure({ hasStatus: s.status != null, failures, updating: updating(s) })
  const quiet = (since: number | null) => since != null && now - since >= KLIPPER_QUIET_MS
  if (outcome === 'reconnecting' && quiet(s.reconnectingSince) && quiet(klipperQuietSince)) {
    patch({ failures, now, status: null, unavailable: true, reconnectingSince: null, expectUpdate: false })
    saveUpdating(false)
  } else if (outcome === 'reconnecting') patch({ failures, now, reconnectingSince: s.reconnectingSince ?? now })
  else if (outcome === 'unavailable') patch({ failures, now, status: null, unavailable: true, reconnectingSince: null })
  else patch({ failures, now })
}

/** Asks for the status now, then keeps polling while something wants it. */
export async function poll() {
  stop()
  if (document.hidden) return
  const controller = new AbortController()
  abortController = controller
  try {
    const status = parseVoStatus(await request('/status', 'GET', controller.signal))
    if (controller.signal.aborted) return
    // A body this page doesn't understand, such as a newer schema, means the status isn't available.
    if (status) applyStatus(status)
    else patch({ status: null, unavailable: true, reconnectingSince: null })
  } catch {
    if (controller.signal.aborted) return
    onPollFailed()
  }
  if (abortController === controller) abortController = null
  schedule()
}

let started = false
/** Asks once at startup and whenever Moonraker or Klipper goes away, which is when an update would show. */
export function startValveOilStatus() {
  if (started) return
  started = true
  document.addEventListener('visibilitychange', () => (document.hidden ? stop() : wanted() && poll()))
  let ready = false
  // The hourly update check writes what's waiting to a file that both this service and Klipper's printer warnings read.
  // The warnings reach the page at once, so when Valve Oil's warnings change, the status is asked for again, and the
  // Updates screen agrees with the banner that led there.
  let voWarnings: unknown = undefined
  if (klipperSeesUpdate(printer.get()) === false) klipperQuietSince = Date.now()
  printer.subscribe(() => {
    const s = printer.get()
    const now = s.conn === 'open' && s.klippy === 'ready'
    if (ready && !now && !timer && !abortController) poll()
    ready = now
    klipperQuietSince = klipperSeesUpdate(s) === false ? (klipperQuietSince ?? Date.now()) : null
    const w = (s.status.vo_printer_warnings as { warnings?: unknown } | undefined)?.warnings
    if (w !== voWarnings) {
      const changed = JSON.stringify(valveOilWarningIds(w)) !== JSON.stringify(valveOilWarningIds(voWarnings))
      voWarnings = w
      if (changed && !timer && !abortController) poll()
    }
  })
  poll()
}

function valveOilWarningIds(warnings: unknown): string[] {
  if (!Array.isArray(warnings)) return []
  return warnings.map((w) => (w && typeof w === 'object' ? String((w as { id?: unknown }).id ?? '') : '')).filter((id) => id.startsWith('valve_oil_'))
}

/** Keeps the status polled while the calling screen is shown. */
export function watchValveOil() {
  watchers++
  if (watchers === 1) {
    // What the page already has may be from when it loaded; the screen says it's checking until this answer arrives.
    patch({ fresh: false })
    poll()
  }
  return () => {
    watchers--
    if (!wanted()) stop()
  }
}

// ---- actions ----
/** Posts to api/auto-update, with `busy` set while it's saving and a failure kept in `error`. */
async function postAutoUpdate(body: Record<string, unknown>, busy: Partial<ValveOilState>, done: Partial<ValveOilState>, error: 'autoUpdateError' | 'windowError') {
  patch({ ...busy, [error]: '' })
  try {
    const status = parseVoStatus(await request('/auto-update', 'POST', undefined, body))
    if (status) applyStatus(status)
  } catch (e) {
    patch({ [error]: errorText(e) })
  } finally {
    patch(done)
  }
  poll()
}

export async function setAutoUpdate(enabled: boolean) {
  const s = valveOil.get()
  if (autoUpdateDisabled(s) || enabled === s.status?.auto_update.enabled) return
  await postAutoUpdate({ enabled }, { autoUpdateRequest: enabled }, { autoUpdateRequest: null }, 'autoUpdateError')
}

/** Saves the update window or its timezone: any of {"window": {...} | null, "timezone": "<IANA name>"}. */
export async function setUpdateWindow(body: { window?: VoUpdateWindowHours | null; timezone?: string }) {
  if (autoUpdateDisabled(valveOil.get())) return
  await postAutoUpdate(body, { windowPending: true }, { windowPending: false }, 'windowError')
}

export const clearWindowError = () => patch({ windowError: '' })

export async function updateNow() {
  if (updateNowDisabled(valveOil.get())) return
  patch({ updateNowPending: true, updateNowError: '', updateNowBlockedBy: [] })
  try {
    const job = parseVoStatusJob(await request('/update-now', 'POST'))
    patch({ expectUpdate: true })
    saveUpdating(true)
    const status = valveOil.get().status
    if (job && status) patch({ status: { ...status, busy: job.state === 'running' || status.busy, job } })
  } catch (e) {
    patch({
      updateNowError: errorText(e),
      updateNowBlockedBy: e instanceof VoStatusRequestError && e.status === 409 ? e.blockedBy : [],
    })
  } finally {
    patch({ updateNowPending: false })
  }
  poll()
}

export function dismissUpdate(id: string) {
  const s = valveOil.get()
  if (s.dismissed.includes(id)) return
  const dismissed = [...s.dismissed, id].slice(-DISMISSED_UPDATES_KEPT)
  patch({ dismissed })
  saveLocal(DISMISSED_UPDATES_KEY, JSON.stringify(dismissed))
}

/**
 * Opens the prefilled issue, then downloads the support bundle to attach to it. The issue opens first, while the click
 * still lets this page open a tab.
 */
export async function reportProblem() {
  const s = valveOil.get()
  const url = s.status ? voStatusView(s.status).supportIssueUrl : null
  const base = apiBase()
  if (!url || !base || s.supportPending) return
  window.open(url, '_blank', 'noopener,noreferrer')
  patch({ supportPending: true, supportError: '' })
  try {
    const { blob, name } = await voSupportBundle(base)
    saveBlob(blob, name)
  } catch (e) {
    patch({ supportError: errorText(e) })
  } finally {
    patch({ supportPending: false })
  }
}

/**
 * Chooses which Klipper runs. The printer saves the choice and restarts Klipper a moment after answering, and reports
 * the running Klipper as unknown until it has.
 */
export async function switchKlipper(use: VoKlipperTree) {
  if (klipperSwitchDisabled(valveOil.get())) return
  patch({ klipperRequest: use, klipperError: '', klipperBlockedBy: [] })
  try {
    const klipper = parseVoKlipperAnswer(await request('/klipper', 'POST', undefined, { use }))
    const status = valveOil.get().status
    if (klipper && status) patch({ status: { ...status, klipper } })
  } catch (e) {
    patch({
      klipperError: errorText(e),
      klipperBlockedBy: e instanceof VoStatusRequestError && e.status === 409 ? e.blockedBy : [],
    })
    poll()
  } finally {
    patch({ klipperRequest: null })
  }
}

export const clearKlipperError = () => patch({ klipperError: '', klipperBlockedBy: [] })

export const klipperSwitchDisabled = (s: ValveOilState) => s.status?.klipper == null || s.klipperRequest != null || reconnecting(s)

export const clearSupportError = () => patch({ supportError: '' })
export const clearAutoUpdateError = () => patch({ autoUpdateError: '' })
export const clearUpdateNowError = () => patch({ updateNowError: '', updateNowBlockedBy: [] })

// ---- derived ----
export const reconnecting = (s: ValveOilState) => s.reconnectingSince != null

export const autoUpdateDisabled = (s: ValveOilState) => s.status?.auto_update.enabled == null || s.autoUpdateRequest != null || s.windowPending || reconnecting(s)

export const updateNowDisabled = (s: ValveOilState) => {
  const view = s.status ? voStatusView(s.status) : null
  return view == null || !view.updateNow.offered || !view.updateNow.allowed || s.updateNowPending || reconnecting(s)
}

/** How long the printer hasn't answered during an update, in minutes, once that's unusually long; otherwise null. */
export const reconnectingLongMinutes = (s: ValveOilState): number | null => {
  const waited = s.reconnectingSince != null ? s.now - s.reconnectingSince : 0
  return waited >= VO_STATUS_RECONNECT_PATIENCE_MS ? Math.floor(waited / 60000) : null
}

export function useValveOil(): ValveOilState {
  return useStore(valveOil, (s) => s)
}

export function useValveOilView(): VoStatusView | null {
  const status = useStore(valveOil, (s) => s.status)
  return status ? voStatusView(status) : null
}

/** The update to show: a running one always, a finished one until it's closed, and a success for a day at most. */
export function shownUpdate(view: VoStatusView | null, dismissed: string[]): VoUpdateView | null {
  return voShownUpdate(view?.update ?? null, dismissed)
}

/** Whether an update is running, or the printer went quiet during one: what the app shell warns about. */
export const updateRunning = (s: ValveOilState) => s.status?.busy === true || (s.expectUpdate && s.reconnectingSince != null)

export function useUpdateRunning(): boolean {
  return useStore(valveOil, updateRunning)
}

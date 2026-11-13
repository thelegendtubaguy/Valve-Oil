// Valve Oil's update status, from the status service beside Moonraker (src/qidi/tltg-status.ts). The polling and
// request handling follow the Fluidd fork's TltgValveOilSettings.vue, kept outside React here so the app shell can
// keep saying an update is running while Moonraker and Klipper are down for it.
//
// The service is asked once when the page opens and whenever Moonraker or Klipper goes away, then polled while the
// Updates screen is open or an update is running.
import { createStore, useStore } from '../lib/store'
import { client, printer } from './printer'
import {
  parseTltgStatus,
  parseTltgStatusJob,
  tltgStatusApiBase,
  tltgStatusPollFailure,
  tltgStatusPollInterval,
  tltgStatusRequest,
  TltgStatusRequestError,
  tltgStatusView,
  TLTG_STATUS_RECONNECT_PATIENCE_MS,
  type TltgStatus,
  type TltgStatusBlocker,
  type TltgStatusView,
  type TltgUpdateView,
} from '../qidi/tltg-status'

export type ValveOilState = {
  status: TltgStatus | null
  /** The service answered that it isn't there, or with something this page doesn't understand. */
  unavailable: boolean
  failures: number
  /** An update is running or was just started, so failed requests mean the printer is restarting its services. */
  expectUpdate: boolean
  reconnectingSince: number | null
  now: number
  autoUpdateRequest: boolean | null
  autoUpdateError: string
  updateNowPending: boolean
  updateNowError: string
  /** Why the printer refused Update now, when it said. */
  updateNowBlockedBy: TltgStatusBlocker[]
  dismissed: string[]
}

// The same keys as the Fluidd fork's section: both are served from the printer's one origin, so a result closed in
// one is closed in the other.
const DISMISSED_UPDATES_KEY = 'tltg-status.dismissed-updates'
const DISMISSED_UPDATES_KEPT = 20
const UPDATING_KEY = 'tltg-status.updating-since'
const UPDATING_KEPT_MS = 6 * 60 * 60 * 1000

function loadDismissed(): string[] {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(DISMISSED_UPDATES_KEY) ?? '[]')
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : []
  } catch {
    return []
  }
}

function loadUpdating(): boolean {
  try {
    const since = Number(sessionStorage.getItem(UPDATING_KEY))
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
  unavailable: false,
  failures: 0,
  expectUpdate: loadUpdating(),
  reconnectingSince: null,
  now: Date.now(),
  autoUpdateRequest: null,
  autoUpdateError: '',
  updateNowPending: false,
  updateNowError: '',
  updateNowBlockedBy: [],
  dismissed: loadDismissed(),
})

const patch = (changes: Partial<ValveOilState>) => valveOil.set((s) => ({ ...s, ...changes }))

// Valve Oil UI talks only to the Moonraker of the web server that served it. In development the dev server proxies
// /tltg-status to the printer or the fake (vite.config.ts), so the page's own origin is used there too.
const apiBase = () => tltgStatusApiBase(import.meta.env.DEV ? location.origin : client.httpBase, location)

async function request(path: string, method: 'GET' | 'POST' = 'GET', signal?: AbortSignal, body?: Record<string, unknown>) {
  const base = apiBase()
  if (!base) throw new TltgStatusRequestError(0, 'Not connected to this printer')
  return tltgStatusRequest(base, path, { method, token: null, signal, body })
}

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
  timer = setTimeout(poll, tltgStatusPollInterval(s.status, s.reconnectingSince != null))
}

function applyStatus(status: TltgStatus) {
  patch({ status, unavailable: false, failures: 0, reconnectingSince: null, expectUpdate: status.busy })
  saveUpdating(status.busy)
}

function onPollFailed() {
  const s = valveOil.get()
  const failures = s.failures + 1
  const now = Date.now()
  const outcome = tltgStatusPollFailure({ hasStatus: s.status != null, failures, updating: updating(s) })
  if (outcome === 'reconnecting') patch({ failures, now, reconnectingSince: s.reconnectingSince ?? now })
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
    const status = parseTltgStatus(await request('/status', 'GET', controller.signal))
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
  printer.subscribe(() => {
    const s = printer.get()
    const now = s.conn === 'open' && s.klippy === 'ready'
    if (ready && !now && !timer && !abortController) poll()
    ready = now
  })
  poll()
}

/** Keeps the status polled while the calling screen is shown. */
export function watchValveOil() {
  watchers++
  if (watchers === 1) poll()
  return () => {
    watchers--
    if (!wanted()) stop()
  }
}

// ---- actions ----
export async function setAutoUpdate(enabled: boolean) {
  const s = valveOil.get()
  if (autoUpdateDisabled(s) || enabled === s.status?.auto_update.enabled) return
  patch({ autoUpdateRequest: enabled, autoUpdateError: '' })
  try {
    const status = parseTltgStatus(await request('/auto-update', 'POST', undefined, { enabled }))
    if (status) applyStatus(status)
  } catch (e) {
    patch({ autoUpdateError: e instanceof Error ? e.message : String(e) })
  } finally {
    patch({ autoUpdateRequest: null })
  }
  poll()
}

export async function updateNow() {
  if (updateNowDisabled(valveOil.get())) return
  patch({ updateNowPending: true, updateNowError: '', updateNowBlockedBy: [] })
  try {
    const job = parseTltgStatusJob(await request('/update-now', 'POST'))
    patch({ expectUpdate: true })
    saveUpdating(true)
    const status = valveOil.get().status
    if (job && status) patch({ status: { ...status, busy: job.state === 'running' || status.busy, job } })
  } catch (e) {
    patch({
      updateNowError: e instanceof Error ? e.message : String(e),
      updateNowBlockedBy: e instanceof TltgStatusRequestError && e.status === 409 ? e.blockedBy : [],
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
  try {
    localStorage.setItem(DISMISSED_UPDATES_KEY, JSON.stringify(dismissed))
  } catch {
    // Private browsing can refuse storage; the result still clears for this page.
  }
}

export const clearAutoUpdateError = () => patch({ autoUpdateError: '' })
export const clearUpdateNowError = () => patch({ updateNowError: '', updateNowBlockedBy: [] })

// ---- derived ----
export const reconnecting = (s: ValveOilState) => s.reconnectingSince != null

export const autoUpdateDisabled = (s: ValveOilState) => s.status?.auto_update.enabled == null || s.autoUpdateRequest != null || reconnecting(s)

export const updateNowDisabled = (s: ValveOilState) => {
  const view = s.status ? tltgStatusView(s.status) : null
  return view == null || !view.updateNow.offered || !view.updateNow.allowed || s.updateNowPending || reconnecting(s)
}

/** How long the printer hasn't answered during an update, in minutes, once that's unusually long; otherwise null. */
export const reconnectingLongMinutes = (s: ValveOilState): number | null => {
  const waited = s.reconnectingSince != null ? s.now - s.reconnectingSince : 0
  return waited >= TLTG_STATUS_RECONNECT_PATIENCE_MS ? Math.floor(waited / 60000) : null
}

export function useValveOil(): ValveOilState {
  return useStore(valveOil, (s) => s)
}

export function useValveOilView(): TltgStatusView | null {
  const status = useStore(valveOil, (s) => s.status)
  return status ? tltgStatusView(status) : null
}

/** The update to show: a running one always, a finished one until it's closed. */
export function shownUpdate(view: TltgStatusView | null, dismissed: string[]): TltgUpdateView | null {
  const update = view?.update
  return update && (update.state === 'running' || !dismissed.includes(update.id)) ? update : null
}

/** Whether an update is running, or the printer went quiet during one: what the app shell warns about. */
export function useUpdateRunning(): boolean {
  return useStore(valveOil, (s) => s.status?.busy === true || (s.expectUpdate && s.reconnectingSince != null))
}

// Valve Oil's add-ons (src/qidi/vo-addons.ts), from the add-ons service the printer's nginx serves at
// /vo-addons/api. The polling, dismissal, and request handling follow the Fluidd fork's VoAddonsSettings.vue; the
// service is polled only while the Add-ons settings are shown.
import { createStore, useStore } from '../lib/store'
import { getRenamedItem } from '../lib/storage'
import { client } from './printer'
import { t } from '../i18n'
import {
  parseVoAddonsJob,
  parseVoAddonsList,
  voAddonsApiBase,
  voAddonsPollInterval,
  voAddonsRequest,
  VoAddonsRequestError,
  type VoAddon,
  type VoAddonAction,
  type VoAddonJob,
  type VoAddonsList,
} from '../qidi/vo-addons'

export type AddonsState = {
  list: VoAddonsList | null
  unavailable: boolean
  failures: number
  pendingAction: { addon: string; action: VoAddonAction } | null
  actionErrors: Record<string, string>
  dismissedJobs: string[]
}

// Polls lose the list only after this many failures in a row, so a service restart mid-job keeps it on screen.
const FAILURES_BEFORE_UNAVAILABLE = 3
// A finished job stays on the printer until the next one starts. Its result here clears when dismissed, a success also
// by itself after a few seconds, and stays cleared across reloads. The key is the fork's: both UIs share the origin. Jobs
// closed under the key's old tltg- name stay closed.
const SUCCESS_VISIBLE_MS = 8000
const DISMISSED_JOBS_KEY = 'vo-addons.dismissed-jobs'
const OLD_DISMISSED_JOBS_KEY = 'tltg-addons.dismissed-jobs'
const DISMISSED_JOBS_KEPT = 20

function loadDismissedJobs(): string[] {
  try {
    const value: unknown = JSON.parse(getRenamedItem(localStorage, DISMISSED_JOBS_KEY, OLD_DISMISSED_JOBS_KEY) ?? '[]')
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : []
  } catch {
    return []
  }
}

export const addons = createStore<AddonsState>({ list: null, unavailable: false, failures: 0, pendingAction: null, actionErrors: {}, dismissedJobs: loadDismissedJobs() })
const patch = (changes: Partial<AddonsState>) => addons.set((s) => ({ ...s, ...changes }))

export function useAddons() {
  return useStore(addons, (s) => s)
}

// Valve Oil UI talks only to the web server that served it. In development the dev server proxies /vo-addons to the
// printer or the fake, so the page's own origin is used there too.
const apiBase = () => voAddonsApiBase(import.meta.env.DEV ? location.origin : client.httpBase, location)

/** Calls the add-ons API; panels use it for their own actions, such as a link code or joining a network. */
export function addonsRequest(path: string, method: 'GET' | 'POST' = 'GET', signal?: AbortSignal, body?: Record<string, unknown>): Promise<unknown> {
  const base = apiBase()
  if (!base) return Promise.reject(new VoAddonsRequestError(0, t('settings.addons.not_connected')))
  return voAddonsRequest(base, path, { method, token: null, signal, body })
}

// ---- polling ----
let watchers = 0
let timer: ReturnType<typeof setTimeout> | undefined
let successTimer: ReturnType<typeof setTimeout> | undefined
let abortController: AbortController | null = null
let lastJobState = ''

function stop() {
  clearTimeout(timer)
  timer = undefined
  abortController?.abort()
  abortController = null
}

function schedule() {
  clearTimeout(timer)
  if (watchers > 0 && !document.hidden) timer = setTimeout(poll, voAddonsPollInterval(addons.get().list))
}

function setList(list: VoAddonsList) {
  patch({ list, unavailable: false, failures: 0 })
  onJobState()
}

// A success clears itself after a while; a failure stays until it's dismissed.
function onJobState() {
  const job = addons.get().list?.job
  const key = job ? `${job.id}:${job.state}` : ''
  if (key === lastJobState) return
  lastJobState = key
  clearTimeout(successTimer)
  if (job?.state === 'succeeded' && !addons.get().dismissedJobs.includes(job.id)) successTimer = setTimeout(() => dismissJob(job.id), SUCCESS_VISIBLE_MS)
}

function onPollFailed() {
  const s = addons.get()
  const failures = s.failures + 1
  if (s.list == null || failures >= FAILURES_BEFORE_UNAVAILABLE) patch({ failures, list: null, unavailable: true })
  else patch({ failures })
}

export async function poll() {
  stop()
  if (watchers === 0 || document.hidden) return
  if (!apiBase()) {
    patch({ list: null, unavailable: true })
    return
  }
  const controller = new AbortController()
  abortController = controller
  try {
    const list = parseVoAddonsList(await addonsRequest('/addons', 'GET', controller.signal))
    if (controller.signal.aborted) return
    if (list) setList(list)
    else onPollFailed()
  } catch {
    if (controller.signal.aborted) return
    onPollFailed()
  }
  if (abortController === controller) abortController = null
  schedule()
}

const onVisibility = () => (document.hidden ? stop() : poll())

/** Keeps the list polled while the calling screen is shown. */
export function watchAddons() {
  watchers++
  if (watchers === 1) {
    document.addEventListener('visibilitychange', onVisibility)
    poll()
  }
  return () => {
    watchers--
    if (watchers === 0) {
      document.removeEventListener('visibilitychange', onVisibility)
      stop()
    }
  }
}

// ---- actions ----
export function actionsDisabled(s: AddonsState) {
  return s.list == null || s.list.busy || s.list.printer_busy || s.pendingAction != null
}

// Only one remote-access add-on can be installed; the service names the installed one in the way.
export const installBlocked = (addon: VoAddon) => addon.state === 'not_installed' && addon.blocked_by.length > 0

export const hasStatusPanel = (addon: VoAddon) => addon.state !== 'not_installed' && (addon.status != null || addon.service != null)

export function jobFor(s: AddonsState, addon: VoAddon): VoAddonJob | null {
  const job = s.list?.job
  return job && job.addon === addon.id && (job.state === 'running' || !s.dismissedJobs.includes(job.id)) ? job : null
}

export function dismissJob(id: string) {
  const s = addons.get()
  if (s.dismissedJobs.includes(id)) return
  const dismissedJobs = [...s.dismissedJobs, id].slice(-DISMISSED_JOBS_KEPT)
  patch({ dismissedJobs })
  try {
    localStorage.setItem(DISMISSED_JOBS_KEY, JSON.stringify(dismissedJobs))
  } catch {
    // Private browsing can refuse storage; the result still clears for this page.
  }
}

/** Starts an install, update, remove, or relink. The caller confirms a remove first. */
export async function runAddonAction(addon: VoAddon, action: VoAddonAction, body?: Record<string, unknown>) {
  const s = addons.get()
  if (actionsDisabled(s) || (action === 'install' && installBlocked(addon))) return
  const actionErrors = { ...s.actionErrors }
  delete actionErrors[addon.id]
  patch({ pendingAction: { addon: addon.id, action }, actionErrors })
  try {
    const job = parseVoAddonsJob(await addonsRequest(`/addons/${encodeURIComponent(addon.id)}/${action}`, 'POST', undefined, body))
    const list = addons.get().list
    if (job && list) setList({ ...list, busy: job.state === 'running' || list.busy, job })
  } catch (e) {
    patch({ actionErrors: { ...addons.get().actionErrors, [addon.id]: e instanceof Error ? e.message : String(e) } })
  } finally {
    patch({ pendingAction: null })
  }
  poll()
}

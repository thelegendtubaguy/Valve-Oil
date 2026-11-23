// Print history, job queue, and timelapse state for the History screen, kept current from Moonraker's notifications
// (notify_history_changed, notify_job_queue_changed, notify_timelapse_event) as Fluidd's history, jobQueue, and
// timelapse stores are.
import { useEffect, useState } from 'react'
import { client, printer, useConnected, usePrinter } from './printer'
import { createStore, useStore } from '../lib/store'
import { toast } from '../components/toast'
import { t } from '../i18n'
import type { Job, QueuedJob, QueueState, TimelapseFile, TimelapseSettings, Totals } from '../components/history/logic'

/** Fluidd loads this many recent jobs on connect (Globals.JOB_HISTORY_LOAD); Load all fetches the rest. */
export const HISTORY_LOAD = 100

/** Whether Moonraker loaded a component (history, job_queue, timelapse). */
export function useComponent(name: string): boolean | null {
  return usePrinter((s) => (s.server ? s.server.components.includes(name) : null))
}

// ---------------------------------------------------------------------------------------------------------------
// History

type HistoryState = { jobs: Job[]; loaded: boolean; allLoaded: boolean; loading: boolean; totals: Totals | null }
export const history = createStore<HistoryState>({ jobs: [], loaded: false, allLoaded: false, loading: false, totals: null })

export function useHistoryState<S>(selector: (s: HistoryState) => S): S {
  return useStore(history, selector)
}

const byStart = (a: Job, b: Job) => b.start_time - a.start_time

/** Loads the newest `limit` jobs; 0 loads every job, as Fluidd's Load all does. */
export async function loadHistory(limit = HISTORY_LOAD) {
  history.set((s) => ({ ...s, loading: true }))
  try {
    const r = await client.call<{ count: number; jobs: Job[] }>('server.history.list', { limit, order: 'desc' })
    const jobs = [...r.jobs].sort(byStart)
    history.set((s) => ({ ...s, jobs, loaded: true, loading: false, allLoaded: limit === 0 || jobs.length < limit }))
  } catch (e) {
    history.set((s) => ({ ...s, loaded: true, loading: false }))
    throw e
  }
}

export async function loadTotals() {
  try {
    const r = await client.call<{ job_totals: Totals }>('server.history.totals')
    history.set((s) => ({ ...s, totals: r.job_totals }))
  } catch {
    history.set((s) => ({ ...s, totals: null }))
  }
}

function upsertJob(job: Job) {
  history.set((s) => {
    const i = s.jobs.findIndex((j) => j.job_id === job.job_id)
    const jobs = i >= 0 ? s.jobs.map((j, k) => (k === i ? job : j)) : [job, ...s.jobs].sort(byStart)
    return { ...s, jobs }
  })
}

function dropJobs(ids: string[]) {
  const gone = new Set(ids)
  history.set((s) => ({ ...s, jobs: s.jobs.filter((j) => !gone.has(j.job_id)) }))
}

export async function deleteJob(uid: string) {
  const r = await client.call<{ deleted_jobs: string[] }>('server.history.delete_job', { uid })
  dropJobs(r.deleted_jobs)
}

export async function deleteAllJobs() {
  await client.call<{ deleted_jobs: string[] }>('server.history.delete_job', { all: true })
  history.set((s) => ({ ...s, jobs: [], allLoaded: true }))
}

/** Moonraker zeroes the totals; its reply carries only the previous ones. */
export async function resetTotals() {
  await client.call('server.history.reset_totals')
  history.set((s) => ({ ...s, totals: { total_jobs: 0, total_time: 0, total_print_time: 0, total_filament_used: 0, longest_job: 0, longest_print: 0 } }))
}

// ---------------------------------------------------------------------------------------------------------------
// Job queue

type QueueStoreState = { state: QueueState | null; jobs: QueuedJob[]; loaded: boolean }
export const queue = createStore<QueueStoreState>({ state: null, jobs: [], loaded: false })
export const useQueue = <S>(selector: (s: QueueStoreState) => S): S => useStore(queue, selector)

type QueueStatus = { queued_jobs: QueuedJob[]; queue_state: QueueState }
const applyQueue = (r: QueueStatus) => queue.set({ state: r.queue_state, jobs: r.queued_jobs ?? [], loaded: true })

export async function loadQueue() {
  applyQueue(await client.call<QueueStatus>('server.job_queue.status'))
}

export const queueActions = {
  /** Adds files to the end of the queue; with reset, replaces the queue with them (Moonraker API 1.1.0). */
  post: async (filenames: string[], reset = false) => applyQueue(await client.call<QueueStatus>('server.job_queue.post_job', reset ? { filenames, reset: true } : { filenames })),
  remove: async (jobIds: string[]) => applyQueue(await client.call<QueueStatus>('server.job_queue.delete_job', { job_ids: jobIds })),
  clear: async () => applyQueue(await client.call<QueueStatus>('server.job_queue.delete_job', { all: true })),
  pause: async () => applyQueue(await client.call<QueueStatus>('server.job_queue.pause')),
  start: async () => applyQueue(await client.call<QueueStatus>('server.job_queue.start')),
  jump: async (jobId: string) => applyQueue(await client.call<QueueStatus>('server.job_queue.jump', { job_id: jobId })),
}

// ---------------------------------------------------------------------------------------------------------------
// Timelapse

export type RenderStatus =
  | { action: 'render'; status: 'started'; msg: string; framecount: number }
  | { action: 'render'; status: 'running'; progress: number }
  | { action: 'render'; status: 'success'; msg: string; filename: string; printfile: string; previewimage: string }
type LastFrameInfo = { framecount: number; lastframefile: string }
type TimelapseState = { settings: TimelapseSettings | null; lastFrame: LastFrameInfo | null; render: RenderStatus | null }
export const timelapse = createStore<TimelapseState>({ settings: null, lastFrame: null, render: null })
export const useTimelapse = <S>(selector: (s: TimelapseState) => S): S => useStore(timelapse, selector)

export async function loadTimelapse() {
  const [settings, lastFrame] = await Promise.all([client.call<TimelapseSettings>('machine.timelapse.get_settings'), client.call<LastFrameInfo>('machine.timelapse.lastframeinfo').catch(() => null)])
  timelapse.set((s) => ({ ...s, settings, lastFrame }))
}

export async function postTimelapseSettings(settings: Partial<TimelapseSettings>) {
  const r = await client.call<TimelapseSettings>('machine.timelapse.post_settings', settings)
  timelapse.set((s) => ({ ...s, settings: r }))
}

export const renderTimelapse = () => client.call('machine.timelapse.render')
export const saveTimelapseFrames = () => client.call<{ action: 'saveframes'; status?: 'skipped' | 'running' | 'finished'; zipfile?: string }>('machine.timelapse.saveframes')

type TimelapseEvent =
  | { action: 'newframe'; status?: 'error'; frame: string; framefile: string }
  | RenderStatus
  | { action: 'render'; status: 'error'; msg: string; cmd?: string; cmdresponse?: string }
  | { action: string; status?: string }

function onTimelapseEvent(e: TimelapseEvent) {
  if (e.action === 'newframe') {
    const f = e as Extract<TimelapseEvent, { action: 'newframe' }>
    if (f.status === 'error') toast(t('history.timelapse.frame_error'), 'error')
    // A new frame ends whatever render was showing, as in Fluidd's setLastFrame.
    else timelapse.set((s) => ({ ...s, lastFrame: { framecount: parseInt(f.frame), lastframefile: f.framefile }, render: null }))
    return
  }
  if (e.action === 'render') {
    if (e.status === 'error') {
      const r = e as { msg: string; cmdresponse?: string }
      timelapse.set((s) => ({ ...s, render: null }))
      toast(r.msg || t('history.timelapse.render_failed'), 'error')
      return
    }
    if (e.status === 'started' || e.status === 'running' || e.status === 'success') timelapse.set((s) => ({ ...s, render: e as RenderStatus }))
    if (e.status === 'success') toast(t('history.timelapse.rendered'))
  }
}

/** The timelapse folder, re-read when Moonraker reports file changes. */
export function useTimelapseFiles(): TimelapseFile[] | null {
  const rev = usePrinter((s) => s.filesRev)
  const ready = useConnected()
  const [files, setFiles] = useState<TimelapseFile[] | null>(null)
  useEffect(() => {
    if (!ready) return
    let live = true
    client
      .call<TimelapseFile[]>('server.files.list', { root: 'timelapse' })
      .then((f) => live && setFiles(f.filter((x) => !x.path.startsWith('.') && !x.path.includes('/.'))))
      .catch(() => live && setFiles([]))
    return () => {
      live = false
    }
  }, [rev, ready])
  return files
}

// ---------------------------------------------------------------------------------------------------------------
// Wiring: subscribed once, on first use, and reloaded whenever Moonraker reconnects.

let started = false
function components() {
  return printer.get().server?.components ?? []
}
function reload() {
  const c = components()
  if (c.includes('history')) {
    const s = history.get()
    loadHistory(s.allLoaded && s.loaded ? 0 : Math.max(HISTORY_LOAD, s.jobs.length)).catch(() => {})
    loadTotals()
  }
  if (c.includes('job_queue')) loadQueue().catch(() => {})
  if (c.includes('timelapse')) loadTimelapse().catch(() => {})
}

export function startHistory() {
  if (started) return
  started = true
  client.on('notify_history_changed', (p) => {
    const e = p[0] as { action: 'added' | 'finished'; job?: Job } | undefined
    if (e?.job) upsertJob(e.job)
    else if (history.get().loaded) loadHistory(Math.max(HISTORY_LOAD, history.get().jobs.length)).catch(() => {})
    loadTotals()
  })
  client.on('notify_job_queue_changed', (p) => {
    const e = p[0] as { queue_state?: QueueState; updated_queue?: QueuedJob[] | null } | undefined
    if (!e) return
    queue.set((s) => ({ ...s, loaded: true, state: e.queue_state ?? s.state, jobs: e.updated_queue ?? s.jobs }))
  })
  client.on('notify_timelapse_event', (p) => onTimelapseEvent(p[0] as TimelapseEvent))
  // server.info arrives after the socket opens; load once per connection, when it names the components. (It's
  // queried again every couple of seconds while Klipper is down, which needs no reload.)
  let loadedThisConnection = false
  const check = () => {
    const s = printer.get()
    if (s.conn !== 'open') loadedThisConnection = false
    else if (s.server && !loadedThisConnection) {
      loadedThisConnection = true
      reload()
    }
  }
  printer.subscribe(check)
  check()
}

/** Starts the History screen's subscriptions. */
export function useHistoryFeed() {
  useEffect(startHistory, [])
}

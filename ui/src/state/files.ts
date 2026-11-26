import { useEffect, useMemo, useState } from 'react'
import { afterKlipperAnswers, client, useConnected, usePrinter } from './printer'
import { createStore, useStore } from '../lib/store'
import { toast } from '../components/toast'
import { baseName, extOf, isLocked, joinPath, parentOf, rootProps, type FsEntry } from '../components/files/fs'
import { errorText, saveBlob, saveUrl } from '../lib/browser'
import { t } from '../i18n'

export type Thumbnail = { width: number; height: number; size: number; relative_path: string }

export type FileMeta = {
  filename: string
  modified?: number
  size?: number
  slicer?: string
  estimated_time?: number
  layer_height?: number
  first_layer_height?: number
  object_height?: number
  filament_total?: number
  filament_weight_total?: number
  filament_type?: string
  filament_name?: string
  filament_colors?: string[]
  extruder_colors?: string[]
  filament_change_count?: number
  referenced_tools?: number[]
  thumbnails?: Thumbnail[]
  job_id?: string | null
  print_start_time?: number | null
  // Moonraker passes through extra slicer fields; QIDI-specific ones are read where used.
  [key: string]: unknown
}

export type FileEntry = { path: string; modified: number; size: number; permissions?: string }

export type HistoryJob = {
  job_id: string
  filename: string
  status: 'completed' | 'cancelled' | 'error' | 'klippy_shutdown' | 'klippy_disconnect' | 'in_progress' | 'interrupted' | 'server_exit' | string
  start_time: number
  end_time: number | null
  print_duration: number
  total_duration: number
  filament_used: number
  metadata?: FileMeta
  exists?: boolean
}

export type FileHistory = { last: HistoryJob; count: number }

/**
 * Looks up a G-code file's last job and completed prints in print history (newest first). Moonraker keeps history by
 * file name, so a job that started before the file's modified time printed an earlier file of that name, deleted and
 * uploaded again or replaced, and isn't this file's. Fluidd gets the same from the file metadata's `job_id`, which
 * Moonraker drops when the file changes.
 */
export function fileHistory(jobs: HistoryJob[] | null): (path: string, modified: number) => FileHistory | undefined {
  const byName = new Map<string, HistoryJob[]>()
  for (const j of jobs ?? []) {
    const list = byName.get(j.filename)
    if (list) list.push(j)
    else byName.set(j.filename, [j])
  }
  return (path, modified) => {
    const own = byName.get(path)?.filter((j) => j.start_time >= modified)
    if (!own?.length) return undefined
    return { last: own[0], count: own.filter((j) => j.status === 'completed').length }
  }
}

const metaCache = new Map<string, FileMeta | null>()
const inflight = new Map<string, Promise<FileMeta | null>>()

export function fetchMeta(filename: string, force = false): Promise<FileMeta | null> {
  const hit = metaCache.get(filename)
  if (hit !== undefined && !force) return Promise.resolve(hit)
  let p = inflight.get(filename)
  if (!p) {
    // After Klipper's state, which Moonraker would otherwise answer late behind a screenful of these.
    p = Promise.resolve(afterKlipperAnswers())
      .then(() => client.call<FileMeta>('server.files.metadata', { filename }))
      .catch(() => null)
      .then((meta) => {
        metaCache.set(filename, meta)
        inflight.delete(filename)
        return meta
      })
    inflight.set(filename, p)
  }
  return p
}

export function useFileMeta(filename: string | null | undefined) {
  const filesRev = usePrinter((s) => s.filesRev)
  const [meta, setMeta] = useState<FileMeta | null>(filename ? (metaCache.get(filename) ?? null) : null)
  useEffect(() => {
    if (!filename) return setMeta(null)
    let live = true
    fetchMeta(filename, filesRev > 0).then((m) => live && setMeta(m))
    return () => {
      live = false
    }
  }, [filename, filesRev])
  return meta
}

/** URL of the largest thumbnail, resolved relative to the G-code file's folder. */
export function thumbnailUrl(filename: string, meta: FileMeta | null | undefined, minWidth = 0): string | null {
  const thumbs = meta?.thumbnails
  if (!thumbs?.length) return null
  const sorted = [...thumbs].sort((a, b) => b.width - a.width)
  const pick = sorted.find((t) => t.width >= minWidth && t.width <= 400) ?? sorted[0]
  return client.fileUrl('gcodes', joinPath(parentOf(filename), pick.relative_path))
}

/**
 * A Moonraker list, fetched while connected and enabled and again when `rev` moves; empty when the call fails. With
 * `afterKlipper`, the first fetch on a connection waits for Klipper's state, so Moonraker answers that first.
 */
function useList<R, T>(method: string, params: Record<string, unknown>, rev: 'filesRev' | 'historyRev', enabled: boolean, pick: (r: R) => T[], afterKlipper = false) {
  const n = usePrinter((s) => s[rev])
  const ready = useConnected()
  const [list, setList] = useState<T[] | null>(null)
  const key = JSON.stringify(params)
  useEffect(() => {
    if (!ready || !enabled) return
    let live = true
    Promise.resolve(afterKlipper ? afterKlipperAnswers() : null)
      .then(() => client.call<R>(method, params))
      .then((r) => live && setList(pick(r)))
      .catch(() => live && setList([]))
    return () => {
      live = false
    }
  }, [method, key, n, ready, enabled])
  return list
}

// ---- deletes, hidden at once: Moonraker drops a file from its lists only once the delete has run and the list is
// read again, so a deleted file stayed on screen for a moment. It's hidden from the start, and comes back if the
// delete fails. Each is the file as it was (its modified time), so a new file of the same name shows straight away. ----

type Deleting = { root: string; path: string; modified?: number }
const deleting = createStore<Deleting[]>([])
// After this, a delete that went through has long since left Moonraker's lists.
const DELETE_HIDE_MS = 60_000

/** Hides these entries of a root from every file list until their deletes end. Returns a function that shows them again. */
export function hideDeleted(root: string, entries: { path: string; modified?: number }[]): () => void {
  const added = entries.map((e): Deleting => ({ root, path: e.path, modified: e.modified }))
  deleting.set((d) => [...d, ...added])
  const show = () => deleting.set((d) => d.filter((x) => !added.includes(x)))
  setTimeout(show, DELETE_HIDE_MS)
  return show
}

function isDeleted(list: Deleting[], root: string, e: { path: string; modified?: number }) {
  return list.some((d) => d.root === root && ((d.path === e.path && (d.modified == null || e.modified == null || d.modified === e.modified)) || e.path.startsWith(`${d.path}/`)))
}

/** `list` without the entries being deleted. */
export function useWithoutDeleted<T extends { path: string; modified?: number }>(root: string, list: T[] | null): T[] | null {
  const d = useStore(deleting, (x) => x)
  return useMemo(() => (list && d.length ? list.filter((e) => !isDeleted(d, root, e)) : list), [list, d, root])
}

export const useFiles = () => useWithoutDeleted('gcodes', useList<FileEntry[], FileEntry>('server.files.list', { root: 'gcodes' }, 'filesRev', true, (f) => f.filter((x) => /\.(gcode|g|gco|gcode\.3mf)$/i.test(x.path) && !x.path.startsWith('.')), true))

export const useHistory = (limit = 200, enabled = true) => useList<{ jobs: HistoryJob[] }, HistoryJob>('server.history.list', { limit, order: 'desc' }, 'historyRev', enabled, (r) => r.jobs, true)

// ---------------------------------------------------------------------------------------------------------------
// Any root: directories, file operations, uploads, and downloads, for the file browser and the editor. Paths are
// relative to the root; Moonraker's file methods take them with the root in front.

export type DiskUsage = { total: number; used: number; free: number }
export type Directory = { dirs: FsEntry[]; files: FsEntry[]; disk: DiskUsage | null; permissions: string }

type RawDir = { dirname: string; modified: number; size: number; permissions: string }
type RawFile = { filename: string; modified: number; size: number; permissions: string } & Record<string, unknown>

const rooted = (root: string, path: string) => joinPath(root, path)

/** One folder of a root, with G-code metadata inline; reloads when Moonraker reports a file change. */
export function useDirectory(root: string, path: string) {
  const rev = usePrinter((s) => s.filesRev)
  const ready = useConnected()
  const [state, setState] = useState<{ key: string; dir: Directory | null; error: string | null }>({ key: '', dir: null, error: null })
  const key = `${root}/${path}`
  useEffect(() => {
    if (!ready) return
    let live = true
    client
      .call<{ dirs: RawDir[]; files: RawFile[]; disk_usage?: DiskUsage; root_info?: { permissions: string } }>('server.files.get_directory', { path: rooted(root, path), extended: true })
      .then((r) => {
        if (!live) return
        const dirs = r.dirs.map((d): FsEntry => ({ kind: 'dir', name: d.dirname, path: joinPath(path, d.dirname), modified: d.modified, size: d.size, permissions: d.permissions ?? 'rw' }))
        const files = r.files.map((f): FsEntry => {
          const { filename, modified, size, permissions, ...rest } = f
          const p = joinPath(path, filename)
          return { kind: 'file', name: filename, path: p, modified, size, permissions: permissions ?? 'rw', meta: root === 'gcodes' && Object.keys(rest).length ? ({ ...rest, filename: p, modified, size } as FileMeta) : undefined }
        })
        setState({ key, dir: { dirs, files, disk: r.disk_usage ?? null, permissions: r.root_info?.permissions ?? 'rw' }, error: null })
      })
      .catch((e) => live && setState({ key, dir: null, error: errorText(e) }))
    return () => {
      live = false
    }
  }, [key, root, path, rev, ready])
  const dirs = useWithoutDeleted(root, state.dir?.dirs ?? null)
  const files = useWithoutDeleted(root, state.dir?.files ?? null)
  const dir = useMemo(() => (state.dir && dirs && files ? { ...state.dir, dirs, files } : state.dir), [state.dir, dirs, files])
  // A stale folder stays on screen only until the new one loads, so moving between folders doesn't flash empty.
  return { dir, error: state.key === key ? state.error : null, loading: state.key !== key }
}

/** Every file in a root (server.files.list), for searching across folders. */
export const useRootFiles = (root: string, enabled = true) => useWithoutDeleted(root, useList<FileEntry[], FileEntry>('server.files.list', { root }, 'filesRev', enabled, (f) => f))

/** Refuses writes over files Valve Oil UI must leave alone (config/fluidd.cfg). */
function guard(root: string, path: string) {
  if (isLocked(root, path)) throw new Error(t('files.ops.locked'))
}

export async function moveItem(root: string, from: string, to: string) {
  guard(root, from)
  guard(root, to)
  await client.call('server.files.move', { source: rooted(root, from), dest: rooted(root, to) })
}

export async function copyItem(root: string, from: string, to: string) {
  guard(root, to)
  await client.call('server.files.copy', { source: rooted(root, from), dest: rooted(root, to) })
}

/** Deletes a file or folder, hidden from the lists from the start and shown again if the delete fails. */
export async function deleteItem(root: string, e: Pick<FsEntry, 'kind' | 'path'> & { modified?: number }) {
  guard(root, e.path)
  const show = hideDeleted(root, [e])
  try {
    if (e.kind === 'dir') await client.call('server.files.delete_directory', { path: rooted(root, e.path), force: true })
    else await client.call('server.files.delete_file', { path: rooted(root, e.path) })
  } catch (err) {
    show()
    throw err
  }
}

export async function makeDir(root: string, path: string) {
  await client.call('server.files.post_directory', { path: rooted(root, path) })
}

/** Rescans a G-code file's metadata, as Fluidd's Refresh Metadata. */
export async function rescanMeta(filename: string) {
  const m = await client.call<FileMeta>('server.files.metascan', { filename })
  metaCache.set(filename, m)
  return m
}

export async function enqueueJobs(filenames: string[]) {
  await client.call('server.job_queue.post_job', { filenames })
}

/** A file's contents as text, never cached, for the editor. */
export async function readText(root: string, path: string): Promise<string> {
  const r = await fetch(client.fileUrl(root, path), { cache: 'no-store' })
  if (!r.ok) throw new Error(t('files.ops.open_failed', { path, status: r.status }))
  return r.text()
}

/** Saves text over a file by uploading it to the same path, as Fluidd's editor does. */
export async function writeText(root: string, path: string, text: string) {
  guard(root, path)
  await uploadOne(new File([text], baseName(path)), root, parentOf(path), false)
}

/** Downloads one file. Moonraker sends it as an attachment, so the browser saves it rather than opening it. */
export const downloadFile = (root: string, path: string) => saveUrl(client.fileUrl(root, path), baseName(path))

/** A zip's name: the one item's name, or else `fallback`, with the time it was made. */
export function zipName(items: Pick<FsEntry, 'name'>[], fallback: string) {
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '_').slice(0, 15)
  return items.length === 1 ? `${items[0].name}-${stamp}.zip` : `${fallback && `${fallback}-`}${stamp}.zip`
}

/**
 * Downloads several items as one zip: Moonraker zips them into the folder (server.files.zip), the browser fetches
 * the archive, and the archive is deleted again. Read-only roots can't hold the archive, so their files download
 * one by one.
 */
export async function downloadZip(root: string, dir: string, items: FsEntry[]) {
  if (rootProps(root).readonly) {
    for (const e of items.filter((x) => x.kind === 'file')) downloadFile(root, e.path)
    return
  }
  const name = zipName(items, baseName(dir) || root)
  const dest = joinPath(dir, name)
  await createZip(root, dest, items)
  try {
    const r = await fetch(client.fileUrl(root, dest), { cache: 'no-store' })
    if (!r.ok) throw new Error(t('files.ops.download_failed', { name, status: r.status }))
    saveBlob(await r.blob(), name)
  } finally {
    await client.call('server.files.delete_file', { path: rooted(root, dest) }).catch(() => {})
  }
}

export async function createZip(root: string, dest: string, items: Pick<FsEntry, 'path'>[]) {
  guard(root, dest)
  await client.call('server.files.zip', { dest: rooted(root, dest), items: items.map((e) => rooted(root, e.path)), store_only: false }, 10 * 60 * 1000)
}

// ---- uploads, one at a time with progress, as Fluidd does: parallel uploads upset Moonraker and Klipper ----

export type Upload = { id: number; name: string; root: string; size: number; loaded: number; state: 'waiting' | 'sending' | 'done' | 'failed' | 'cancelled'; error?: string; xhr?: XMLHttpRequest }
export type FileWithPath = { file: File; path: string }

const uploads = createStore<Upload[]>([])
let uploadId = 0
export const useUploads = () => useStore(uploads, (u) => u)

function patchUpload(id: number, p: Partial<Upload>) {
  uploads.set((list) => list.map((u) => (u.id === id ? { ...u, ...p } : u)))
}

export function cancelUpload(id: number) {
  const u = uploads.get().find((x) => x.id === id)
  u?.xhr?.abort()
  patchUpload(id, { state: 'cancelled', xhr: undefined })
}

/** An upload that hasn't finished: waiting its turn or sending. */
export const isPending = (u: Upload) => u.state === 'waiting' || u.state === 'sending'

export function clearUploads() {
  uploads.set((list) => list.filter(isPending))
}

function uploadOne(file: File, root: string, dir: string, print: boolean, id?: number): Promise<void> {
  guard(root, joinPath(dir, file.name))
  const form = new FormData()
  form.append('root', root)
  if (dir) form.append('path', dir)
  if (print) form.append('print', 'true')
  form.append('file', file, file.name)
  return new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `${client.httpBase}/server/files/upload`)
    if (id != null) {
      patchUpload(id, { state: 'sending', xhr })
      xhr.upload.onprogress = (e) => e.lengthComputable && patchUpload(id, { loaded: e.loaded })
    }
    xhr.onload = () => {
      if (xhr.status < 300) return resolve()
      let msg = t('files.ops.upload_failed_status', { status: xhr.status })
      try {
        msg = JSON.parse(xhr.responseText)?.error?.message ?? msg
      } catch {}
      reject(new Error(msg))
    }
    xhr.onerror = () => reject(new Error(t('files.ops.upload_failed')))
    xhr.onabort = () => reject(new DOMException('Cancelled', 'AbortError'))
    xhr.send(form)
  })
}

/**
 * Uploads files into a folder of a root, keeping each file's own sub-path (folder uploads). The gcodes root takes
 * only printable files. With `print`, a single file starts printing once Moonraker has it.
 */
export async function uploadFiles(root: string, dir: string, items: (File | FileWithPath)[], print = false): Promise<number> {
  let list = items.map((x) => (x instanceof File ? { file: x, path: '' } : x))
  const { accepts } = rootProps(root)
  if (root === 'gcodes') {
    const skipped = list.filter((x) => !accepts.includes(extOf(x.file.name)))
    if (skipped.length) toast(skipped.length === 1 ? t('files.ops.not_gcode_named', { name: skipped[0].file.name }) : t('files.ops.not_gcode_files', { count: skipped.length }), 'error')
    list = list.filter((x) => accepts.includes(extOf(x.file.name)))
  }
  const locked = list.filter((x) => isLocked(root, joinPath(dir, x.path, x.file.name)))
  if (locked.length) toast(t('files.ops.upload_locked'), 'error')
  list = list.filter((x) => !locked.includes(x))
  if (list.length !== 1) print = false
  const queued = list.map((x) => ({ ...x, id: ++uploadId }))
  uploads.set((u) => [...u.filter(isPending), ...queued.map((q): Upload => ({ id: q.id, name: joinPath(dir, q.path, q.file.name), root, size: q.file.size, loaded: 0, state: 'waiting' }))])
  let done = 0
  for (const q of queued) {
    if (uploads.get().find((u) => u.id === q.id)?.state !== 'waiting') continue
    try {
      await uploadOne(q.file, root, joinPath(dir, q.path), print, q.id)
      patchUpload(q.id, { state: 'done', loaded: q.file.size, xhr: undefined })
      done++
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') patchUpload(q.id, { state: 'cancelled', xhr: undefined })
      else patchUpload(q.id, { state: 'failed', error: errorText(e), xhr: undefined })
    }
  }
  // Finished uploads clear themselves a little later; failures stay until dismissed.
  setTimeout(() => uploads.set((u) => u.filter((x) => !(queued.some((q) => q.id === x.id) && (x.state === 'done' || x.state === 'cancelled')))), 4000)
  return done
}

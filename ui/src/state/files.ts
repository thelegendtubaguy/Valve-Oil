import { useEffect, useState } from 'react'
import { client, usePrinter } from './printer'
import { createStore, useStore } from '../lib/store'
import { toast } from '../components/toast'
import { baseName, extOf, isLocked, joinPath, parentOf, rootProps, type FsEntry } from '../components/files/fs'

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

const metaCache = new Map<string, { meta: FileMeta | null; at: number }>()
const inflight = new Map<string, Promise<FileMeta | null>>()

export function fetchMeta(filename: string, force = false): Promise<FileMeta | null> {
  const hit = metaCache.get(filename)
  if (hit && !force) return Promise.resolve(hit.meta)
  let p = inflight.get(filename)
  if (!p) {
    p = client
      .call<FileMeta>('server.files.metadata', { filename })
      .catch(() => null)
      .then((meta) => {
        metaCache.set(filename, { meta, at: Date.now() })
        inflight.delete(filename)
        return meta
      })
    inflight.set(filename, p)
  }
  return p
}

export function useFileMeta(filename: string | null | undefined) {
  const filesRev = usePrinter((s) => s.filesRev)
  const [meta, setMeta] = useState<FileMeta | null>(filename ? (metaCache.get(filename)?.meta ?? null) : null)
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
  const dir = filename.includes('/') ? filename.slice(0, filename.lastIndexOf('/') + 1) : ''
  return client.fileUrl('gcodes', dir + pick.relative_path)
}

export function useFiles() {
  const rev = usePrinter((s) => s.filesRev)
  const ready = usePrinter((s) => s.conn === 'open')
  const [files, setFiles] = useState<FileEntry[] | null>(null)
  useEffect(() => {
    if (!ready) return
    let live = true
    client
      .call<FileEntry[]>('server.files.list', { root: 'gcodes' })
      .then((f) => live && setFiles(f.filter((x) => /\.(gcode|g|gco|gcode\.3mf)$/i.test(x.path) && !x.path.startsWith('.'))))
      .catch(() => live && setFiles([]))
    return () => {
      live = false
    }
  }, [rev, ready])
  return files
}

export function useHistory(limit = 200, enabled = true) {
  const rev = usePrinter((s) => s.historyRev)
  const ready = usePrinter((s) => s.conn === 'open')
  const [jobs, setJobs] = useState<HistoryJob[] | null>(null)
  useEffect(() => {
    if (!ready || !enabled) return
    let live = true
    client
      .call<{ jobs: HistoryJob[] }>('server.history.list', { limit, order: 'desc' })
      .then((r) => live && setJobs(r.jobs))
      .catch(() => live && setJobs([]))
    return () => {
      live = false
    }
  }, [rev, ready, limit, enabled])
  return jobs
}

export type HistoryTotals = { total_jobs: number; total_time: number; total_print_time: number; total_filament_used: number; longest_job: number; longest_print: number }

export function useHistoryTotals() {
  const rev = usePrinter((s) => s.historyRev)
  const ready = usePrinter((s) => s.conn === 'open')
  const [totals, setTotals] = useState<HistoryTotals | null>(null)
  useEffect(() => {
    if (!ready) return
    client
      .call<{ job_totals: HistoryTotals }>('server.history.totals')
      .then((r) => setTotals(r.job_totals))
      .catch(() => setTotals(null))
  }, [rev, ready])
  return totals
}

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
  const ready = usePrinter((s) => s.conn === 'open')
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
      .catch((e) => live && setState({ key, dir: null, error: e instanceof Error ? e.message : String(e) }))
    return () => {
      live = false
    }
  }, [key, root, path, rev, ready])
  // A stale folder stays on screen only until the new one loads, so moving between folders doesn't flash empty.
  return { dir: state.dir, error: state.key === key ? state.error : null, loading: state.key !== key }
}

/** Every file in a root (server.files.list), for searching across folders. */
export function useRootFiles(root: string, enabled = true) {
  const rev = usePrinter((s) => s.filesRev)
  const ready = usePrinter((s) => s.conn === 'open')
  const [files, setFiles] = useState<FileEntry[] | null>(null)
  useEffect(() => {
    if (!ready || !enabled) return
    let live = true
    client
      .call<FileEntry[]>('server.files.list', { root })
      .then((f) => live && setFiles(f))
      .catch(() => live && setFiles([]))
    return () => {
      live = false
    }
  }, [root, rev, ready, enabled])
  return files
}

/** Refuses writes over files Valve Oil UI must leave alone (config/fluidd.cfg). */
function guard(root: string, path: string) {
  if (isLocked(root, path)) throw new Error('fluidd.cfg is read-only. Valve Oil leaves it as QIDI ships it.')
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

export async function deleteItem(root: string, e: Pick<FsEntry, 'kind' | 'path'>) {
  guard(root, e.path)
  if (e.kind === 'dir') await client.call('server.files.delete_directory', { path: rooted(root, e.path), force: true })
  else await client.call('server.files.delete_file', { path: rooted(root, e.path) })
}

export async function makeDir(root: string, path: string) {
  await client.call('server.files.post_directory', { path: rooted(root, path) })
}

/** Rescans a G-code file's metadata, as Fluidd's Refresh Metadata. */
export async function rescanMeta(filename: string) {
  const m = await client.call<FileMeta>('server.files.metascan', { filename })
  metaCache.set(filename, { meta: m, at: Date.now() })
  return m
}

export async function enqueueJobs(filenames: string[]) {
  await client.call('server.job_queue.post_job', { filenames })
}

/** A file's contents as text, never cached, for the editor. */
export async function readText(root: string, path: string): Promise<string> {
  const r = await fetch(client.fileUrl(root, path), { cache: 'no-store' })
  if (!r.ok) throw new Error(`Couldn’t open ${path} (${r.status})`)
  return r.text()
}

/** Saves text over a file by uploading it to the same path, as Fluidd's editor does. */
export async function writeText(root: string, path: string, text: string) {
  guard(root, path)
  await uploadOne(new File([text], baseName(path)), root, parentOf(path), false)
}

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.append(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 30000)
}

/** Downloads one file. Moonraker sends it as an attachment, so the browser saves it rather than opening it. */
export function downloadFile(root: string, path: string) {
  const a = document.createElement('a')
  a.href = client.fileUrl(root, path)
  a.download = baseName(path)
  a.rel = 'noopener'
  document.body.append(a)
  a.click()
  a.remove()
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
  const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '_').slice(0, 15)
  const name = items.length === 1 ? `${items[0].name}-${stamp}.zip` : `${baseName(dir) || root}-${stamp}.zip`
  const dest = joinPath(dir, name)
  await createZip(root, dest, items)
  try {
    const r = await fetch(client.fileUrl(root, dest), { cache: 'no-store' })
    if (!r.ok) throw new Error(`Couldn’t download ${name} (${r.status})`)
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

export function clearUploads() {
  uploads.set((list) => list.filter((u) => u.state === 'waiting' || u.state === 'sending'))
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
      let msg = `Upload failed (${xhr.status})`
      try {
        msg = JSON.parse(xhr.responseText)?.error?.message ?? msg
      } catch {}
      reject(new Error(msg))
    }
    xhr.onerror = () => reject(new Error('Upload failed'))
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
    if (skipped.length) toast(skipped.length === 1 ? `${skipped[0].file.name} isn't G-code or a sliced .gcode.3mf.` : `Skipped ${skipped.length} files that aren't G-code or sliced .gcode.3mf.`, 'error')
    list = list.filter((x) => accepts.includes(extOf(x.file.name)))
  }
  const locked = list.filter((x) => isLocked(root, joinPath(dir, x.path, x.file.name)))
  if (locked.length) toast('fluidd.cfg is read-only, so it wasn’t uploaded.', 'error')
  list = list.filter((x) => !locked.includes(x))
  if (list.length !== 1) print = false
  const queued = list.map((x) => ({ ...x, id: ++uploadId }))
  uploads.set((u) => [...u.filter((x) => x.state === 'waiting' || x.state === 'sending'), ...queued.map((q): Upload => ({ id: q.id, name: joinPath(dir, q.path, q.file.name), root, size: q.file.size, loaded: 0, state: 'waiting' }))])
  let done = 0
  for (const q of queued) {
    if (uploads.get().find((u) => u.id === q.id)?.state !== 'waiting') continue
    try {
      await uploadOne(q.file, root, joinPath(dir, q.path), print, q.id)
      patchUpload(q.id, { state: 'done', loaded: q.file.size, xhr: undefined })
      done++
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') patchUpload(q.id, { state: 'cancelled', xhr: undefined })
      else patchUpload(q.id, { state: 'failed', error: e instanceof Error ? e.message : String(e), xhr: undefined })
    }
  }
  // Finished uploads clear themselves a little later; failures stay until dismissed.
  setTimeout(() => uploads.set((u) => u.filter((x) => !(queued.some((q) => q.id === x.id) && (x.state === 'done' || x.state === 'cancelled')))), 4000)
  return done
}

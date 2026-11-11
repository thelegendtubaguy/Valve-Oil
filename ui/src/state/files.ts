import { useEffect, useState } from 'react'
import { client, usePrinter } from './printer'

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

export function useHistory(limit = 200) {
  const rev = usePrinter((s) => s.historyRev)
  const ready = usePrinter((s) => s.conn === 'open')
  const [jobs, setJobs] = useState<HistoryJob[] | null>(null)
  useEffect(() => {
    if (!ready) return
    let live = true
    client
      .call<{ jobs: HistoryJob[] }>('server.history.list', { limit, order: 'desc' })
      .then((r) => live && setJobs(r.jobs))
      .catch(() => live && setJobs([]))
    return () => {
      live = false
    }
  }, [rev, ready, limit])
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

import { useEffect } from 'react'
import { createStore, useStore } from '../../lib/store'
import { client } from '../../state/printer'
import type { ParsedGcode } from './types'
import type { ParseGcodeWorkerRequestMessage, ParseGcodeWorkerResponseMessage } from '../../workers/parseGcode.worker'
import type { PreviewErrorCode } from '../../workers/preview-error'
import { t } from '../../i18n'

// Parsed files, shared by the preview screen and Home's live preview so moving between them doesn't download and
// parse a file again. Fluidd holds one file; two are kept here so the printing file and another being looked at can
// both stay loaded. Each parse runs in its own worker and can be cancelled.

export type PreviewFile = {
  path: string
  /** The file's modified time when it was loaded, so a re-uploaded file loads again. */
  modified: number
  size: number
  phase: 'download' | 'parse' | 'ready' | 'error' | 'cancelled'
  /** Bytes downloaded of a 3MF, which arrives whole before its G-code is read. */
  received: number
  /** Position reached in the file, in bytes of `size`. */
  parsed: number
  data: ParsedGcode | null
  error: string | null
  /** What went wrong, when the worker knows: preview.errors.<code>, with its values. */
  errorCode?: PreviewErrorCode
  errorVars?: Record<string, string | number>
}

const KEEP = 2
const files = createStore<PreviewFile[]>([])
const workers = new Map<string, Worker>()

function update(path: string, patch: Partial<PreviewFile>) {
  files.set((all) => all.map((f) => (f.path === path ? { ...f, ...patch } : f)))
}

function stop(path: string) {
  workers.get(path)?.terminate()
  workers.delete(path)
}

/** Downloads and parses a file in a worker, replacing an earlier load of the same path. */
export function loadGcode(path: string, size: number, modified: number) {
  stop(path)
  const entry: PreviewFile = { path, modified, size, phase: /\.3mf$/i.test(path) ? 'download' : 'parse', received: 0, parsed: 0, data: null, error: null }
  files.set((all) => {
    const rest = all.filter((f) => f.path !== path)
    // Drop the oldest beyond KEEP, along with any parse still running for it.
    for (const old of rest.slice(KEEP - 1)) stop(old.path)
    return [entry, ...rest.slice(0, KEEP - 1)]
  })

  const worker = new Worker(new URL('../../workers/parseGcode.worker.ts', import.meta.url), { type: 'module' })
  workers.set(path, worker)
  worker.onmessage = (event: MessageEvent<ParseGcodeWorkerResponseMessage>) => {
    if (workers.get(path) !== worker) return
    const m = event.data
    // The worker reports the served size first; progress is measured against it.
    if (m.action === 'download') update(path, { received: m.received, size: m.total || size })
    else if (m.action === 'progress') update(path, { phase: 'parse', parsed: m.filePosition })
    else if (m.action === 'result') {
      stop(path)
      const { action: _, ...data } = m
      files.set((all) => all.map((f) => (f.path === path ? { ...f, phase: 'ready', data, parsed: f.size } : f)))
    } else if (m.action === 'error') {
      stop(path)
      update(path, { phase: 'error', error: m.error || t('preview.load.failed_detail'), errorCode: m.code, errorVars: m.vars })
    }
  }
  worker.onerror = (e) => {
    stop(path)
    update(path, { phase: 'error', error: e.message || t('preview.load.failed_detail') })
  }
  const message: ParseGcodeWorkerRequestMessage = { action: 'parse', url: client.fileUrl('gcodes', path), fileSize: size }
  worker.postMessage(message)
}

/** Stops a running parse. The file stays cancelled, rather than loading again by itself, until loaded by hand. */
export function cancelGcode(path: string) {
  stop(path)
  update(path, { phase: 'cancelled', data: null })
}

/** The loaded (or loading) state of one file. */
export function usePreviewFile(path: string | null): PreviewFile | null {
  return useStore(files, (all) => (path ? (all.find((f) => f.path === path) ?? null) : null))
}

/**
 * Loads `path` when it isn't loaded yet or has changed since (`modified` from Moonraker's metadata), while `enabled`.
 * A failed or cancelled load stays so until loaded again by hand.
 */
export function useAutoLoad(path: string | null, size: number | undefined, modified: number | undefined, enabled = true) {
  const file = usePreviewFile(path)
  useEffect(() => {
    if (!enabled || !path || size == null || modified == null) return
    if (file && file.modified === modified) return
    loadGcode(path, size, modified)
  }, [enabled, path, size, modified, file])
}

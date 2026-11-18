import { useState } from 'react'
import { ChevronRight, Folder, FolderPlus, House } from 'lucide-react'
import { Dialog, Spinner, prompt } from '../ui'
import { attempt, toast } from '../toast'
import { client } from '../../state/printer'
import { makeDir, thumbnailUrl, useDirectory, useFileMeta, type HistoryJob } from '../../state/files'
import { alwaysHidden, asList, joinPath, validName, type FsEntry } from './fs'
import { fmtDuration } from '../../lib/format'
import { fmtDate, fmtSize, outcomeText } from './cells'

/** Picks a folder of the same root to move items into. */
export function MoveDialog({ root, rootLabel, items, from, onMove, onClose }: { root: string; rootLabel: string; items: FsEntry[]; from: string; onMove: (dest: string) => void; onClose: () => void }) {
  const [at, setAt] = useState(from)
  const { dir, loading } = useDirectory(root, at)
  const moving = new Set(items.filter((e) => e.kind === 'dir').map((e) => e.path))
  const dirs = (dir?.dirs ?? []).filter((d) => !alwaysHidden(d) && !moving.has(d.path))
  const parts = at ? at.split('/') : []
  const newFolder = async () => {
    const name = await prompt({ title: 'Add directory', input: { label: 'Directory name', value: '' } })
    if (!name) return
    const err = validName(name)
    if (err) return toast(err, 'error')
    if (await attempt(async () => (await makeDir(root, joinPath(at, name)), true))) setAt(joinPath(at, name))
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={items.length === 1 ? `Move ${items[0].name}` : `Move ${items.length} items`}
      footer={
        <>
          <button className="btn btn-ghost mr-auto" onClick={newFolder}>
            <FolderPlus size={15} /> New folder
          </button>
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={at === from} onClick={() => onMove(at)}>
            Move here
          </button>
        </>
      }
    >
      <nav className="mb-2 flex flex-wrap items-center gap-0.5 text-[13px]">
        <button className="btn btn-ghost btn-sm" onClick={() => setAt('')}>
          <House size={13} /> {rootLabel}
        </button>
        {parts.map((p, i) => (
          <span key={i} className="flex items-center gap-0.5">
            <ChevronRight size={13} className="text-ink-3" />
            <button className="btn btn-ghost btn-sm" onClick={() => setAt(parts.slice(0, i + 1).join('/'))}>
              {p}
            </button>
          </span>
        ))}
      </nav>
      <div className="grid max-h-[50dvh] min-h-[160px] content-start gap-0.5 overflow-auto rounded-xl bg-inset p-1.5 shadow-[inset_0_0_0_1px_var(--line)]">
        {loading && !dir ? (
          <div className="grid place-items-center py-6">
            <Spinner />
          </div>
        ) : dirs.length === 0 ? (
          <div className="px-2 py-4 text-center text-ink-3">No folders here</div>
        ) : (
          dirs.map((d) => (
            <button key={d.path} className="flex items-center gap-2.5 rounded-lg border-0 bg-transparent px-2.5 py-2 text-left text-ink hover:bg-hover" onClick={() => setAt(d.path)}>
              <Folder size={16} className="flex-none text-accent-ink" />
              <span className="min-w-0 flex-1 truncate">{d.name}</span>
              <ChevronRight size={14} className="text-ink-3" />
            </button>
          ))
        )}
      </div>
    </Dialog>
  )
}

const META_LABELS: Record<string, string> = {
  slicer: 'Slicer',
  slicer_version: 'Slicer version',
  estimated_time: 'Estimated time',
  layer_height: 'Layer height',
  first_layer_height: 'First layer height',
  object_height: 'Height',
  nozzle_diameter: 'Nozzle diameter',
  filament_total: 'Filament',
  filament_weight_total: 'Filament weight',
  filament_type: 'Filament type',
  filament_name: 'Filament name',
  first_layer_extr_temp: 'First layer extruder temp',
  first_layer_bed_temp: 'First layer bed temp',
  chamber_temp: 'Chamber temp',
  filament_change_count: 'Filament changes',
}

function metaText(key: string, v: unknown): string {
  if (v == null || v === '') return '—'
  if (key === 'estimated_time') return fmtDuration(Number(v))
  if (key === 'filament_total') return `${(Number(v) / 1000).toFixed(2)} m`
  if (key === 'filament_weight_total') return `${Number(v).toFixed(1)} g`
  if (/height|diameter/.test(key)) return `${v} mm`
  if (/temp$/.test(key)) return `${v} °C`
  if (key === 'print_start_time' || key === 'modified') return fmtDate(Number(v))
  if (key === 'size') return fmtSize(Number(v))
  if (key === 'filament_type' || key === 'filament_name') return [...new Set(asList(v))].join(', ')
  if (Array.isArray(v)) return v.map((x) => (typeof x === 'object' ? JSON.stringify(x) : String(x))).join(', ')
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

/** Everything known about a file: its thumbnail, the slicer's metadata, and its last print. */
export function InfoDialog({ root, entry, job, onClose }: { root: string; entry: FsEntry; job?: HistoryJob; onClose: () => void }) {
  const fetched = useFileMeta(root === 'gcodes' && entry.kind === 'file' ? entry.path : null)
  const meta = fetched ?? entry.meta ?? null
  const thumb = root === 'gcodes' ? thumbnailUrl(entry.path, meta, 300) : null
  const colors = asList(meta?.filament_colors ?? meta?.extruder_colors)
  const known = Object.keys(META_LABELS).filter((k) => meta?.[k] != null && meta[k] !== '')
  const rest = meta ? Object.keys(meta).filter((k) => !(k in META_LABELS) && !['filename', 'size', 'modified', 'thumbnails'].includes(k)).sort() : []
  return (
    <Dialog open onClose={onClose} title={entry.name} size="wide">
      <div className="grid gap-4 sm:grid-cols-[200px_1fr]">
        {root === 'gcodes' && (
          <div className="grid aspect-square place-items-center self-start rounded-xl bg-inset shadow-[inset_0_0_0_1px_var(--line)]">
            {thumb ? <img src={thumb} alt="" className="max-h-[88%] max-w-[88%] object-contain" /> : <Folder size={28} className="text-ink-3" />}
          </div>
        )}
        <div className={`min-w-0 ${root === 'gcodes' ? '' : 'sm:col-span-2'}`}>
          <div className="row-k">
            <span className="k">Path</span>
            <span className="v code break-all">{joinPath(root, entry.path)}</span>
          </div>
          <div className="row-k">
            <span className="k">Size</span>
            <span className="v">{entry.kind === 'dir' ? '—' : fmtSize(entry.size)}</span>
          </div>
          <div className="row-k">
            <span className="k">Modified</span>
            <span className="v">{fmtDate(entry.modified)}</span>
          </div>
          <div className="row-k">
            <span className="k">Permissions</span>
            <span className="v">{entry.permissions === 'r' ? 'Read only' : 'Read and write'}</span>
          </div>
          {job && (
            <div className="row-k">
              <span className="k">Last print</span>
              <span className="v">
                {outcomeText(job)} · {fmtDate(job.start_time)}
              </span>
            </div>
          )}
          {colors.length > 0 && (
            <div className="row-k">
              <span className="k">Colors</span>
              <span className="v flex justify-end gap-1">
                {colors.map((c, i) => (
                  <i key={i} className="swatch" style={{ background: c }} title={c} />
                ))}
              </span>
            </div>
          )}
          {known.map((k) => (
            <div key={k} className="row-k">
              <span className="k">{META_LABELS[k]}</span>
              <span className="v break-words">{metaText(k, meta?.[k])}</span>
            </div>
          ))}
        </div>
      </div>
      {rest.length > 0 && (
        <details className="mt-4">
          <summary className="section-label cursor-pointer">All metadata</summary>
          <div className="mt-2 rounded-xl bg-inset px-3 py-1 shadow-[inset_0_0_0_1px_var(--line)]">
            {rest.map((k) => (
              <div key={k} className="row-k">
                <span className="k code">{k}</span>
                <span className="v code max-w-[60vw] break-all text-[12px] font-normal">{metaText(k, meta?.[k])}</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </Dialog>
  )
}

/** Shows an image or video from any root. */
export function ViewDialog({ root, entry, onClose }: { root: string; entry: FsEntry; onClose: () => void }) {
  const url = client.fileUrl(root, entry.path)
  const video = /\.(mp4|webm|mkv|mov)$/i.test(entry.name)
  return (
    <Dialog open onClose={onClose} title={entry.name} size="wide">
      <div className="grid place-items-center rounded-xl bg-inset p-2">{video ? <video src={url} controls className="max-h-[70dvh] max-w-full" /> : <img src={url} alt={entry.name} className="max-h-[70dvh] max-w-full object-contain" />}</div>
    </Dialog>
  )
}

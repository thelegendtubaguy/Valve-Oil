import { useState } from 'react'
import { ChevronRight, Folder, FolderPlus, House } from 'lucide-react'
import { Dialog, Spinner, prompt } from '../ui'
import { attempt, toast } from '../toast'
import { client } from '../../state/printer'
import { makeDir, thumbnailUrl, useDirectory, useFileMeta, type HistoryJob } from '../../state/files'
import { alwaysHidden, asList, columnLabel, joinPath, validName, type FsEntry } from './fs'
import { fmtDuration } from '../../lib/format'
import { fmtDate, fmtSize, outcomeText } from './cells'
import { t } from '../../i18n'

/** Picks a folder of the same root to move items into. */
export function MoveDialog({ root, rootLabel, items, from, onMove, onClose }: { root: string; rootLabel: string; items: FsEntry[]; from: string; onMove: (dest: string) => void; onClose: () => void }) {
  const [at, setAt] = useState(from)
  const { dir, loading } = useDirectory(root, at)
  const moving = new Set(items.filter((e) => e.kind === 'dir').map((e) => e.path))
  const dirs = (dir?.dirs ?? []).filter((d) => !alwaysHidden(d) && !moving.has(d.path))
  const parts = at ? at.split('/') : []
  const newFolder = async () => {
    const name = await prompt({ title: t('files.browser.add_dir'), input: { label: t('files.browser.dir_name'), value: '' } })
    if (!name) return
    const err = validName(name)
    if (err) return toast(err, 'error')
    if (await attempt(async () => (await makeDir(root, joinPath(at, name)), true))) setAt(joinPath(at, name))
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={items.length === 1 ? t('files.move.title_named', { name: items[0].name }) : t('files.move.title_items', { count: items.length })}
      footer={
        <>
          <button className="btn btn-ghost mr-auto" onClick={newFolder}>
            <FolderPlus size={15} /> {t('files.move.new_folder')}
          </button>
          <button className="btn btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button className="btn btn-primary" disabled={at === from} onClick={() => onMove(at)}>
            {t('files.move.move_here')}
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
          <div className="px-2 py-4 text-center text-ink-3">{t('files.move.no_folders')}</div>
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

// The metadata shown by name, in this order, labelled as the file table's columns.
const META_KEYS = [
  'slicer',
  'slicer_version',
  'estimated_time',
  'layer_height',
  'first_layer_height',
  'object_height',
  'nozzle_diameter',
  'filament_total',
  'filament_weight_total',
  'filament_type',
  'filament_name',
  'first_layer_extr_temp',
  'first_layer_bed_temp',
  'chamber_temp',
  'filament_change_count',
]

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
  const known = META_KEYS.filter((k) => meta?.[k] != null && meta[k] !== '')
  const rest = meta ? Object.keys(meta).filter((k) => !META_KEYS.includes(k) && !['filename', 'size', 'modified', 'thumbnails'].includes(k)).sort() : []
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
            <span className="k">{t('files.info.path')}</span>
            <span className="v code break-all">{joinPath(root, entry.path)}</span>
          </div>
          <div className="row-k">
            <span className="k">{columnLabel('size')}</span>
            <span className="v">{entry.kind === 'dir' ? '—' : fmtSize(entry.size)}</span>
          </div>
          <div className="row-k">
            <span className="k">{columnLabel('modified')}</span>
            <span className="v">{fmtDate(entry.modified)}</span>
          </div>
          <div className="row-k">
            <span className="k">{t('files.info.permissions')}</span>
            <span className="v">{entry.permissions === 'r' ? t('files.read_only') : t('files.info.read_write')}</span>
          </div>
          {job && (
            <div className="row-k">
              <span className="k">{t('files.info.last_print')}</span>
              <span className="v">
                {outcomeText(job)} · {fmtDate(job.start_time)}
              </span>
            </div>
          )}
          {colors.length > 0 && (
            <div className="row-k">
              <span className="k">{t('files.info.colors')}</span>
              <span className="v flex justify-end gap-1">
                {colors.map((c, i) => (
                  <i key={i} className="swatch" style={{ background: c }} title={c} />
                ))}
              </span>
            </div>
          )}
          {known.map((k) => (
            <div key={k} className="row-k">
              <span className="k">{columnLabel(k)}</span>
              <span className="v break-words">{metaText(k, meta?.[k])}</span>
            </div>
          ))}
        </div>
      </div>
      {rest.length > 0 && (
        <details className="mt-4">
          <summary className="section-label cursor-pointer">{t('files.info.all_metadata')}</summary>
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

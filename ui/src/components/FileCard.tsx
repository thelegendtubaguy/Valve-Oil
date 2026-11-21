import { useEffect, useRef, useState } from 'react'
import { Clock, Ellipsis, Layers } from 'lucide-react'
import { useFileMeta, thumbnailUrl, type FileMeta, type HistoryJob } from '../state/files'
import { displayName, fmtDuration } from '../lib/format'
import { t } from '../i18n'

/** Last outcome of a file, from print history. */
export function outcomeLabel(job: HistoryJob | undefined, count: number): { text: string; tone: '' | 'bad' | 'good' } {
  if (!job) return { text: t('app.file.new'), tone: '' }
  if (job.status === 'completed') return { text: count > 1 ? t('app.file.printed_times', { n: count }) : t('app.file.printed'), tone: 'good' }
  if (job.status === 'cancelled') return { text: t('app.file.cancelled'), tone: '' }
  if (job.status === 'in_progress') return { text: t('app.file.printing'), tone: 'good' }
  return { text: t('app.file.failed'), tone: 'bad' }
}

/**
 * A file with its thumbnail; metadata loads once the card scrolls into view unless the folder listing brought it.
 * In the file browser it also takes a selection box and a menu.
 */
export function FileCard({
  path,
  last,
  count,
  onOpen,
  meta: given,
  selected,
  onSelect,
  onMenu,
}: {
  path: string
  last?: HistoryJob
  count: number
  onOpen: () => void
  meta?: FileMeta | null
  selected?: boolean
  onSelect?: (on: boolean, shift: boolean) => void
  onMenu?: (x: number, y: number) => void
}) {
  const ref = useRef<HTMLButtonElement>(null)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el || given) return
    const io = new IntersectionObserver((e) => e.some((x) => x.isIntersecting) && (setVisible(true), io.disconnect()), { rootMargin: '200px' })
    io.observe(el)
    return () => io.disconnect()
  }, [given])
  const fetched = useFileMeta(visible && !given ? path : null)
  const meta = given ?? fetched
  const thumb = thumbnailUrl(path, meta, 150)
  const outcome = outcomeLabel(last, count)
  const types = [...new Set((Array.isArray(meta?.filament_type) ? meta.filament_type : String(meta?.filament_type ?? '').split(';')).map((t) => String(t).trim()).filter(Boolean))]
  return (
    <div className={`group relative rounded-xl ${selected ? 'shadow-[0_0_0_2px_var(--accent)]' : ''}`} onContextMenu={onMenu && ((e) => (e.preventDefault(), onMenu(e.clientX, e.clientY)))}>
      <button ref={ref} onClick={onOpen} className="flex h-full w-full flex-col items-stretch overflow-hidden rounded-xl border-0 bg-surface p-0 text-left text-inherit shadow-[var(--shadow-card)] transition-transform hover:-translate-y-0.5">
        <div className="relative grid aspect-[4/3] place-items-center border-b border-line bg-inset">
          {thumb ? <img src={thumb} alt="" loading="lazy" className="max-h-[85%] max-w-[85%] object-contain" /> : <Layers size={26} className="text-ink-3" />}
          <span className={`chip absolute ${onSelect ? 'bottom-2 left-2' : 'top-2 left-2'} ${outcome.tone === 'bad' ? 'text-bad!' : ''}`}>{outcome.text}</span>
        </div>
        <div className="grid gap-1.5 px-3 py-2.5">
          <b className="truncate text-[12.5px]" title={path}>
            {displayName(path)}
          </b>
          <div className="flex flex-wrap gap-1.5">
            {meta?.estimated_time != null && (
              <span className="chip">
                <Clock size={11} /> {fmtDuration(meta.estimated_time)}
              </span>
            )}
            {types.length > 0 && <span className="chip">{types.length > 1 ? `${types[0]} +${types.length - 1}` : types[0]}</span>}
          </div>
        </div>
      </button>
      {onSelect && (
        <label className={`absolute top-1.5 left-1.5 grid h-7 w-7 cursor-pointer place-items-center rounded-lg bg-surface/90 shadow-[0_0_0_1px_var(--line)] ${selected ? '' : 'opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100'}`}>
          <input type="checkbox" className="h-4 w-4 accent-[var(--accent)]" checked={!!selected} onChange={(e) => onSelect(e.target.checked, (e.nativeEvent as MouseEvent).shiftKey)} aria-label={t('app.file.select', { name: displayName(path) })} />
        </label>
      )}
      {onMenu && (
        <button className="iconbtn absolute top-1.5 right-1.5 h-7 w-7 bg-surface/90 shadow-[0_0_0_1px_var(--line)]" aria-label={t('app.file.more_for', { name: displayName(path) })} onClick={(e) => onMenu(e.clientX, e.clientY)}>
          <Ellipsis size={15} />
        </button>
      )}
    </div>
  )
}

/** A compact file row: small thumbnail, name, print time, and last outcome. */
export function FileRow({ path, last, count, onOpen }: { path: string; last?: HistoryJob; count: number; onOpen: () => void }) {
  const meta = useFileMeta(path)
  const thumb = thumbnailUrl(path, meta, 48)
  const outcome = outcomeLabel(last, count)
  return (
    <button onClick={onOpen} className="flex w-full items-center gap-2.5 rounded-lg border-0 bg-transparent px-1.5 py-1.5 text-left text-inherit hover:bg-hover">
      <span className="grid h-10 w-10 flex-none place-items-center overflow-hidden rounded-md bg-inset shadow-[inset_0_0_0_1px_var(--line)]">
        {thumb ? <img src={thumb} alt="" loading="lazy" className="max-h-[90%] max-w-[90%] object-contain" /> : <Layers size={16} className="text-ink-3" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-medium" title={path}>
          {displayName(path)}
        </span>
        <span className="flex items-center gap-1.5 text-[12px] text-ink-3">
          {meta?.estimated_time != null && <>{fmtDuration(meta.estimated_time)} · </>}
          <span className={outcome.tone === 'bad' ? 'text-bad' : ''}>{outcome.text}</span>
        </span>
      </span>
    </button>
  )
}

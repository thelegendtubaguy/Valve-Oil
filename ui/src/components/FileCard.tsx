import { useEffect, useRef, useState } from 'react'
import { Clock, Layers } from 'lucide-react'
import { useFileMeta, thumbnailUrl, type HistoryJob } from '../state/files'
import { displayName, fmtDuration } from '../lib/format'

/** Last outcome of a file, from print history. */
export function outcomeLabel(job: HistoryJob | undefined, count: number): { text: string; tone: '' | 'bad' | 'good' } {
  if (!job) return { text: 'New', tone: '' }
  if (job.status === 'completed') return { text: count > 1 ? `Printed ${count}×` : 'Printed', tone: 'good' }
  if (job.status === 'cancelled') return { text: 'Cancelled', tone: '' }
  if (job.status === 'in_progress') return { text: 'Printing', tone: 'good' }
  return { text: 'Failed', tone: 'bad' }
}

/** A file with its thumbnail; metadata loads once the card scrolls into view. */
export function FileCard({ path, last, count, onOpen }: { path: string; last?: HistoryJob; count: number; onOpen: () => void }) {
  const ref = useRef<HTMLButtonElement>(null)
  const [visible, setVisible] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver((e) => e.some((x) => x.isIntersecting) && (setVisible(true), io.disconnect()), { rootMargin: '200px' })
    io.observe(el)
    return () => io.disconnect()
  }, [])
  const meta = useFileMeta(visible ? path : null)
  const thumb = thumbnailUrl(path, meta, 150)
  const outcome = outcomeLabel(last, count)
  const types = [...new Set((meta?.filament_type ?? '').split(';').map((t) => t.trim()).filter(Boolean))]
  return (
    <button ref={ref} onClick={onOpen} className="flex flex-col items-stretch overflow-hidden rounded-xl border-0 bg-surface p-0 text-left text-inherit shadow-[var(--shadow-card)] transition-transform hover:-translate-y-0.5">
      <div className="relative grid aspect-[4/3] place-items-center border-b border-line bg-inset">
        {thumb ? <img src={thumb} alt="" loading="lazy" className="max-h-[85%] max-w-[85%] object-contain" /> : <Layers size={26} className="text-ink-3" />}
        <span className={`chip absolute top-2 left-2 ${outcome.tone === 'bad' ? 'text-bad!' : ''}`}>{outcome.text}</span>
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

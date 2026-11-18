import { useState } from 'react'
import { FileX, FileText } from 'lucide-react'
import { thumbnailUrl, type FileMeta } from '../../state/files'
import { confirm } from '../ui'
import { attempt } from '../toast'
import { deleteJob } from '../../state/history'
import { displayName } from '../../lib/format'
import { cellText, fmtDateTime, jobColors, statusLabel, statusTone, type Column, type Job } from './logic'

/** A G-code file's slicer thumbnail, or a file icon (crossed out when the file is gone). */
export function Thumb({ filename, meta, size = 40, exists = true, className = '' }: { filename: string; meta: FileMeta | Record<string, never> | null | undefined; size?: number; exists?: boolean; className?: string }) {
  const [failed, setFailed] = useState(false)
  const url = exists && !failed ? thumbnailUrl(filename, meta as FileMeta | null, size * 2) : null
  return (
    <span className={`grid flex-none place-items-center overflow-hidden rounded-lg bg-inset text-ink-3 ${className}`} style={{ width: size, height: size, boxShadow: 'inset 0 0 0 1px var(--line)' }}>
      {url ? <img src={url} alt="" loading="lazy" className="h-full w-full object-contain" onError={() => setFailed(true)} /> : exists ? <FileText size={size * 0.45} /> : <FileX size={size * 0.45} />}
    </span>
  )
}

export function StatusPill({ status, className = '' }: { status: string; className?: string }) {
  return <span className={`pill tone-${statusTone(status)} px-2.5 py-0.5 text-[12px] ${className}`}>{statusLabel(status)}</span>
}

export function Swatches({ colors }: { colors: string[] }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      {colors.map((c, i) => (
        <i key={i} className="swatch" style={{ background: c }} title={c} />
      ))}
    </span>
  )
}

export async function confirmDelete(j: Job) {
  if (await confirm({ title: 'Delete this job?', body: `${displayName(j.filename)}, started ${fmtDateTime(j.start_time)}, leaves the history. The file stays.`, confirm: 'Delete', danger: true })) await attempt(() => deleteJob(j.job_id))
}

/** A history table cell: status as a pill, colors as swatches, the rest as text. */
export function Cell({ job, col }: { job: Job; col: Column }) {
  if (col.kind === 'status') return <StatusPill status={job.status} />
  if (col.kind === 'colors') {
    const v = col.key === 'metadata.filament_colors' ? jobColors(job.metadata as Record<string, unknown>) : (job.metadata as Record<string, unknown> | undefined)?.[col.key.slice(9)]
    return Array.isArray(v) && v.length ? <Swatches colors={v.filter((c): c is string => typeof c === 'string' && c !== '')} /> : <span className="text-ink-3">—</span>
  }
  const t = cellText(job, col)
  if (t == null) return <span className="text-ink-3">—</span>
  return <span className="num">{Array.isArray(t) ? t.join(', ') : t}</span>
}


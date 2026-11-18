import { useState } from 'react'
import { ListPlus, Play, Trash2 } from 'lucide-react'
import { Dialog, SectionLabel } from '../ui'
import { attempt, toast } from '../toast'
import { PrintDialog } from '../PrintDialog'
import { queueActions, useComponent } from '../../state/history'
import { useJob, isActive } from '../../state/job'
import { displayName } from '../../lib/format'
import { cellText, fmtDateTime, fmtLength, fmtSpan, historyColumns, jobColors, type Column, type Job } from './logic'
import { Cell, StatusPill, Thumb, confirmDelete } from './bits'

// Shown in the summary at the top, so left out of the file details below.
const SUMMARY = new Set(['status', 'start_time', 'end_time', 'print_duration', 'total_duration', 'filament_used'])

/** One job: what happened, the file's slicer data, auxiliary data, and Print again, Add to queue, and Delete. */
export function JobDialog({ job, onClose }: { job: Job; onClose: () => void }) {
  const [print, setPrint] = useState(false)
  const hasQueue = useComponent('job_queue')
  const printing = isActive(useJob().state)
  const meta = job.metadata as Record<string, unknown> | undefined
  const hasValue = (c: Column) => (c.kind !== 'colors' ? cellText(job, c) != null : c.key === 'metadata.filament_colors' ? jobColors(meta).length > 0 : Array.isArray(meta?.[c.key.slice(9)]) && (meta[c.key.slice(9)] as unknown[]).length > 0)
  const details = historyColumns([job]).filter((c) => !SUMMARY.has(c.key) && c.key !== 'user' && !c.key.startsWith('auxiliary_data.') && hasValue(c))
  const aux = job.auxiliary_data ?? []

  const enqueue = async () => {
    await attempt(async () => {
      await queueActions.post([job.filename])
      toast(`Added ${displayName(job.filename)} to the job queue`)
    })
  }

  if (print) return <PrintDialog filename={job.filename} onClose={() => (setPrint(false), onClose())} />

  return (
    <Dialog
      open
      onClose={onClose}
      size="wide"
      title={
        <span className="flex min-w-0 items-center gap-2.5">
          <span className="truncate">{displayName(job.filename)}</span>
          <StatusPill status={job.status} />
        </span>
      }
      footer={
        <>
          <button className="btn btn-danger mr-auto" onClick={() => confirmDelete(job).then(onClose)}>
            <Trash2 size={15} /> Delete
          </button>
          {hasQueue && job.exists && (
            <button className="btn" onClick={enqueue}>
              <ListPlus size={15} /> Add to queue
            </button>
          )}
          {job.exists && (
            <button className="btn btn-primary" disabled={printing} onClick={() => setPrint(true)} title={printing ? 'The printer is busy' : undefined}>
              <Play size={15} /> Print again
            </button>
          )}
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-[180px_minmax(0,1fr)]">
        <Thumb filename={job.filename} meta={job.metadata} exists={job.exists} size={180} className="mx-auto max-w-full sm:mx-0" />
        <div className="min-w-0">
          <div className="mb-2 text-[12.5px] break-all text-ink-3">
            {job.filename}
            {!job.exists && <span className="chip ml-2">File deleted</span>}
          </div>
          <div className="grid grid-cols-2 gap-2 lg:grid-cols-3">
            <Fact label="Started" value={fmtDateTime(job.start_time)} />
            <Fact label="Ended" value={job.end_time ? fmtDateTime(job.end_time) : '—'} />
            <Fact label="Used filament" value={job.filament_used > 0 ? fmtLength(job.filament_used) : '—'} />
            <Fact label="Print duration" value={fmtSpan(job.print_duration)} />
            <Fact label="Total duration" value={fmtSpan(job.total_duration)} />
            {job.user && job.user !== '_TRUSTED_USER_' && <Fact label="User" value={job.user} />}
          </div>
        </div>
      </div>

      {details.length > 0 && (
        <>
          <SectionLabel className="mt-5 mb-1">File</SectionLabel>
          <div className="rows-2 max-sm:!grid-cols-1">
            {details.map((c) => (
              <div key={c.key} className="row-k">
                <span className="k">{c.label}</span>
                <span className="v max-w-[55vw] truncate sm:max-w-[260px]" title={c.kind === 'colors' ? undefined : [cellText(job, c)].flat().join(', ')}>
                  <Cell job={job} col={c} />
                </span>
              </div>
            ))}
          </div>
        </>
      )}

      {aux.length > 0 && (
        <>
          <SectionLabel className="mt-5 mb-1">Auxiliary data</SectionLabel>
          <div className="rows-2 max-sm:!grid-cols-1">
            {historyColumns([job])
              .filter((c) => c.key.startsWith('auxiliary_data.'))
              .map((c) => {
                const t = cellText(job, c)
                const a = aux.find((x) => x.name === c.key.slice(15))
                return (
                  <div key={c.key} className="row-k" title={a?.provider}>
                    <span className="k">{c.label}</span>
                    <span className="v">{t == null ? '—' : Array.isArray(t) ? t.join(', ') : t}</span>
                  </div>
                )
              })}
          </div>
        </>
      )}
    </Dialog>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="tile !gap-1 !px-3 !py-2.5">
      <span className="section-label !text-[10px]">{label}</span>
      <span className="num text-[14px] font-semibold">{value}</span>
    </div>
  )
}

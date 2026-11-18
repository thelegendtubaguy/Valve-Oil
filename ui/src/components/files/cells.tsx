import type { HistoryJob } from '../../state/files'
import { fmtBytes, fmtDuration } from '../../lib/format'
import { asList, columnValue, type FsEntry } from './fs'

/** Sizes up to disk sizes: B, KB, MB, GB. */
export function fmtSize(n: number): string {
  return n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)} GB` : fmtBytes(n)
}

export function fmtDate(epochSeconds: number | null | undefined): string {
  if (!epochSeconds) return '—'
  const d = new Date(epochSeconds * 1000)
  const sameYear = d.getFullYear() === new Date().getFullYear()
  return d.toLocaleString([], { year: sameYear ? undefined : 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function outcomeText(job: HistoryJob): string {
  switch (job.status) {
    case 'completed':
      return 'Completed'
    case 'cancelled':
      return 'Cancelled'
    case 'in_progress':
      return 'Printing'
    case 'klippy_shutdown':
    case 'klippy_disconnect':
    case 'interrupted':
    case 'server_exit':
      return 'Interrupted'
    default:
      return 'Failed'
  }
}

export function outcomeTone(job: HistoryJob): string {
  if (job.status === 'completed' || job.status === 'in_progress') return 'tone-good'
  if (job.status === 'cancelled') return 'tone-muted'
  return 'tone-bad'
}

const num = (v: unknown) => (v == null || v === '' ? null : Number(v))

/** One table cell: the column's value for the entry, formatted the way Fluidd formats it. */
export function Cell({ entry, column, job }: { entry: FsEntry; column: string; job?: HistoryJob }) {
  const v = columnValue(entry, column, job)
  if (column === 'history.status') return job ? <span className={`pill ${outcomeTone(job)} px-2! py-0.5! text-[11.5px]!`}>{outcomeText(job)}</span> : <span className="text-ink-3">—</span>
  if (v == null || v === '' || (Array.isArray(v) && !v.length)) return <span className="text-ink-3">—</span>
  const n = num(v)
  switch (column) {
    case 'modified':
    case 'print_start_time':
      return <>{fmtDate(n)}</>
    case 'size':
      return <>{fmtSize(n ?? 0)}</>
    case 'estimated_time':
    case 'history.print_duration':
    case 'history.total_duration':
      return <>{fmtDuration(n)}</>
    case 'filament_total':
    case 'history.filament_used':
      return <>{((n ?? 0) / 1000).toFixed(2)} m</>
    case 'filament_weight_total':
      return <>{(n ?? 0).toFixed(1)} g</>
    case 'filament_weights':
      return <>{asList(v).map((w) => `${Number(w).toFixed(1)} g`).join(', ')}</>
    case 'layer_height':
    case 'first_layer_height':
    case 'object_height':
    case 'nozzle_diameter':
      return <>{Number.isFinite(n) ? `${n} mm` : String(v)}</>
    case 'first_layer_bed_temp':
    case 'first_layer_extr_temp':
    case 'chamber_temp':
      return <>{n} °C</>
    case 'filament_temps':
      return <>{asList(v).map((t) => `${t} °C`).join(', ')}</>
    case 'filament_colors':
    case 'extruder_colors':
      return (
        <span className="flex gap-1">
          {asList(v).map((c, i) => (
            <i key={i} className="swatch h-3! w-3!" style={{ background: c.startsWith('#') || !/^[0-9a-f]{6}$/i.test(c) ? c : `#${c}` }} title={c} />
          ))}
        </span>
      )
    case 'filament_type':
    case 'filament_name':
      return <>{[...new Set(asList(v))].join(', ')}</>
    case 'mmu_print':
      return <>{n ? 'Yes' : 'No'}</>
  }
  if (Array.isArray(v)) return <>{v.join(', ')}</>
  if (typeof v === 'object') return <>{JSON.stringify(v)}</>
  return <>{String(v)}</>
}

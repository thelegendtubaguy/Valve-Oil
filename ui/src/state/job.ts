import { usePrinter } from './printer'
import type { FileMeta } from './files'

export type JobState = 'printing' | 'paused' | 'complete' | 'cancelled' | 'error' | 'standby'

export function useJob() {
  return usePrinter((s) => {
    const ps = s.status.print_stats ?? {}
    const vsd = s.status.virtual_sdcard ?? {}
    const ds = s.status.display_status ?? {}
    return {
      state: (ps.state ?? 'standby') as JobState,
      filename: (ps.filename ?? '') as string,
      printDuration: (ps.print_duration ?? 0) as number,
      totalDuration: (ps.total_duration ?? 0) as number,
      filamentUsed: (ps.filament_used ?? 0) as number,
      message: (ps.message ?? '') as string,
      currentLayer: (ps.info?.current_layer ?? null) as number | null,
      totalLayers: (ps.info?.total_layer ?? null) as number | null,
      fileProgress: (vsd.progress ?? 0) as number,
      displayProgress: (ds.progress ?? 0) as number,
      displayMessage: (ds.message ?? null) as string | null,
      speedFactor: (s.status.gcode_move?.speed_factor ?? 1) as number,
      extrudeFactor: (s.status.gcode_move?.extrude_factor ?? 1) as number,
      zOffset: (s.status.gcode_move?.homing_origin?.[2] ?? 0) as number,
    }
  })
}

export type Job = ReturnType<typeof useJob>

/** Progress 0..1; QIDI's M73 display progress when the slicer sends it, else file position. */
export function jobProgress(job: Job): number {
  return job.displayProgress > 0 ? job.displayProgress : job.fileProgress
}

/**
 * Remaining seconds. Early on the slicer's estimate is the better guide; later the file-based
 * extrapolation catches up with how this printer actually runs. Blend the two by progress.
 */
export function remainingSeconds(job: Job, meta: FileMeta | null): number | null {
  const p = jobProgress(job)
  const t = job.printDuration
  const slicer = meta?.estimated_time ? Math.max(0, meta.estimated_time - t) : null
  const file = p > 0.02 && t > 60 ? t / p - t : null
  if (slicer == null) return file
  if (file == null) return slicer
  const w = Math.min(1, Math.max(0, (p - 0.05) / 0.45))
  return slicer * (1 - w) + file * w
}

export function isActive(state: JobState) {
  return state === 'printing' || state === 'paused'
}

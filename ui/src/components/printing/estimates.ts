import type { Job } from '../../state/job'
import type { FileMeta } from '../../state/files'

// Fluidd's print estimates and layer counts (getTimeEstimates, getPrintLayers, getPrintLayer), for the print card.
/** Time left extrapolated from how far through the file the print is, as Fluidd's "File" estimate. */
export function fileLeft(job: Job): number | null {
  return job.printDuration > 0 && job.fileProgress >= 0.01 ? job.printDuration / job.fileProgress - job.printDuration : null
}

/** Time left by the slicer's estimate, as Fluidd's "Slicer" estimate. */
export function slicerLeft(job: Job, meta: FileMeta | null): number | null {
  return meta?.estimated_time && meta.estimated_time > 0 ? Math.max(0, meta.estimated_time - job.printDuration) : null
}

/** Layer count: Klipper's print_stats info when the slicer sets it, else worked out from the file's heights. */
export function printLayers(job: Job, meta: FileMeta | null): number | null {
  if (job.totalLayers != null) return job.totalLayers
  const count = meta?.layer_count
  if (typeof count === 'number') return count
  const { first_layer_height: first, layer_height: lh, object_height: oh } = meta ?? {}
  if (first != null && lh != null && oh != null && lh > 0) {
    const n = Math.ceil((oh - first) / lh + 1)
    if (n > 0) return n
  }
  return null
}

/** Current layer: print_stats info when set, else from the nozzle height, as Fluidd works it out. */
export function printLayer(job: Job, meta: FileMeta | null, z: number | null): number | null {
  if (job.currentLayer != null) return job.currentLayer
  const { first_layer_height: first, layer_height: lh } = meta ?? {}
  if (job.printDuration > 0 && first != null && lh != null && lh > 0 && z != null) {
    const layer = Math.ceil((z - first) / lh + 1)
    if (layer > 0) return layer
  }
  return null
}

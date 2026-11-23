// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/util/vo-probe-report.ts at
// github.com/thelegendtubaguy/fluidd qidi@6405679, with the fork's Klipper status types for vo_probe_report
// (src/typings/klipper.d.ts). Keep behavior in step with the fork.

export interface VoProbeReportPoint {
  x: number;
  y: number;
  tries: number;
  spread: number;
}

// Retried probe points of the most recent bed mesh, from Valve Oil's vo_probe_report
export interface VoProbeReportState {
  measured: number | null;
  loaded: boolean;
  profile: string | null;
  mesh_min: [number, number] | null;
  mesh_max: [number, number] | null;
  probe_count: [number, number] | null;
  points: VoProbeReportPoint[];
}

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' &&
  value != null &&
  !Array.isArray(value)
)

const isFiniteNumber = (value: unknown): value is number => (
  typeof value === 'number' &&
  Number.isFinite(value)
)

const isProbeReportPoint = (value: unknown): value is VoProbeReportPoint => (
  isRecord(value) &&
  isFiniteNumber(value.x) &&
  isFiniteNumber(value.y) &&
  isFiniteNumber(value.tries) &&
  isFiniteNumber(value.spread)
)

/**
 * Probe points that needed a retry in the loaded bed mesh, from Valve Oil's
 * vo_probe_report. Empty when the object is absent, or when the loaded mesh
 * isn't the one the report describes.
 */
const voProbeReportPoints = (state: unknown): VoProbeReportPoint[] => {
  if (!isRecord(state) || state.loaded !== true || !Array.isArray(state.points)) {
    return []
  }

  return state.points
    .filter(isProbeReportPoint)
    .map(({ x, y, tries, spread }) => ({ x, y, tries, spread }))
}

export default voProbeReportPoints

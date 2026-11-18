// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/util/tltg-probe-report.ts at
// github.com/thelegendtubaguy/fluidd qidi@6405679, with the fork's Klipper status types for tltg_probe_report
// (src/typings/klipper.d.ts). Keep behavior in step with the fork.

export interface TltgProbeReportPoint {
  x: number;
  y: number;
  tries: number;
  spread: number;
}

// Retried probe points of the most recent bed mesh, from QIDI Max 4 Optimized's tltg_probe_report
export interface TltgProbeReportState {
  measured: number | null;
  loaded: boolean;
  profile: string | null;
  mesh_min: [number, number] | null;
  mesh_max: [number, number] | null;
  probe_count: [number, number] | null;
  points: TltgProbeReportPoint[];
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

const isProbeReportPoint = (value: unknown): value is TltgProbeReportPoint => (
  isRecord(value) &&
  isFiniteNumber(value.x) &&
  isFiniteNumber(value.y) &&
  isFiniteNumber(value.tries) &&
  isFiniteNumber(value.spread)
)

/**
 * Probe points that needed a retry in the loaded bed mesh, from QIDI Max 4
 * Optimized's tltg_probe_report. Empty when the object is absent, or when the
 * loaded mesh isn't the one the report describes.
 */
const tltgProbeReportPoints = (state: unknown): TltgProbeReportPoint[] => {
  if (!isRecord(state) || state.loaded !== true || !Array.isArray(state.points)) {
    return []
  }

  return state.points
    .filter(isProbeReportPoint)
    .map(({ x, y, tries, spread }) => ({ x, y, tries, spread }))
}

export default tltgProbeReportPoints

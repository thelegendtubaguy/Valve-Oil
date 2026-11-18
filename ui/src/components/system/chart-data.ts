import { klipperHealthChartValues } from '../../qidi/klipper-health-chart-values'
import type { Status } from '../../state/printer'

// Pure chart-data helpers for the System and Diagnostics charts.

/** Times (ms) and named columns of equal length; a missing value is NaN, drawn as a gap. */
export type SeriesBuffer = { times: number[]; cols: Record<string, number[]> }

export function createBuffer(): SeriesBuffer {
  return { times: [], cols: {} }
}

/** Appends one sample, keeping at most `retention` points. Columns first seen now are back-filled with gaps. */
export function pushSample(buf: SeriesBuffer, time: number, values: Record<string, number>, retention: number) {
  const n = buf.times.length
  buf.times.push(time)
  for (const k of Object.keys(values)) if (!buf.cols[k]) buf.cols[k] = new Array(n).fill(NaN)
  for (const [k, col] of Object.entries(buf.cols)) col.push(k in values ? values[k] : NaN)
  const over = buf.times.length - retention
  if (over > 0) {
    buf.times.splice(0, over)
    for (const col of Object.values(buf.cols)) col.splice(0, over)
  }
}

export const round2 = (v: number) => Math.round(v * 100) / 100

/** An MCU's load and awake time in percent, as Fluidd charts them from last_stats. */
export function mcuChartValues(lastStats: Record<string, number> | undefined): Record<string, number> {
  if (!lastStats) return {}
  const out: Record<string, number> = {}
  // Klipper reports stats over 5 s; 2.5 ms is the task time Fluidd treats as full load.
  if (typeof lastStats.mcu_task_avg === 'number' && typeof lastStats.mcu_task_stddev === 'number') out.load = round2((100 * (lastStats.mcu_task_avg + 3 * lastStats.mcu_task_stddev)) / 0.0025)
  if (typeof lastStats.mcu_awake === 'number') out.awake = round2((100 * lastStats.mcu_awake) / 5)
  return out
}

/**
 * Klipper's values for one sample: system load, Klipper's process CPU as a percent of a core since the last sample
 * (cputime is seconds of CPU), and the main thread and timer count when Valve Oil reports them.
 */
export function klipperChartValues(stats: { sysload?: number; cputime?: number } | undefined, lastCputime: number | null, seconds: number, health?: Status[string]): Record<string, number> {
  const out: Record<string, number> = { ...klipperHealthChartValues(health) }
  if (typeof stats?.sysload === 'number') out.load = round2(stats.sysload)
  if (typeof stats?.cputime === 'number' && lastCputime != null && seconds > 0) out.cputime_change = round2(Math.max(0, ((stats.cputime - lastCputime) / seconds) * 100))
  return out
}

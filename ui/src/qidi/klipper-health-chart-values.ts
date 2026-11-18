// Ported from the Fluidd fork's src/util/klipper-health-chart-values.ts (with decimal-round.ts). Keep them in step.

export type TltgKlipperHealthState = {
  timers: number
  removed_timers: number
  main_thread_load: number | null
  timer_delay: number
}

const decimalRound = (value: number, places: number): number => {
  const factor = 10 ** places
  return Math.round(value * factor) / factor
}

/**
 * Chart values from Valve Oil's tltg_klipper_health: Klipper's timer count and its main thread's share of a core, in
 * percent. Status updates carry only changed fields, so the update is laid over the stored state.
 */
export function klipperHealthChartValues(stored?: Partial<TltgKlipperHealthState>, update?: Partial<TltgKlipperHealthState>): Record<string, number> {
  const health = { ...stored, ...update }
  const values: Record<string, number> = {}
  if (typeof health.timers === 'number') values.timers = health.timers
  if (typeof health.main_thread_load === 'number') values.main_thread = decimalRound(health.main_thread_load * 100, 2)
  return values
}

import { describe, expect, it } from 'vitest'
import { klipperHealthChartValues } from '../klipper-health-chart-values'

describe('klipperHealthChartValues', () => {
  it('charts nothing without vo_klipper_health', () => {
    expect(klipperHealthChartValues(undefined, undefined)).toEqual({})
  })

  it('lays the changed fields over the stored state', () => {
    const stored = { timers: 63, removed_timers: 0, main_thread_load: 0.1, timer_delay: 0 }
    expect(klipperHealthChartValues(stored, { main_thread_load: 0.4234 })).toEqual({ timers: 63, main_thread: 42.34 })
  })

  it('leaves out a main-thread load not measured yet', () => {
    expect(klipperHealthChartValues({ timers: 63, main_thread_load: null }, undefined)).toEqual({ timers: 63 })
  })
})

import { describe, expect, it } from 'vitest'
import { createBuffer, klipperChartValues, mcuChartValues, pushSample } from './chart-data'

describe('mcuChartValues', () => {
  it('charts load and awake time as Fluidd does', () => {
    expect(mcuChartValues({ mcu_awake: 0.039, mcu_task_avg: 0.000026, mcu_task_stddev: 0.000018 })).toEqual({ load: 3.2, awake: 0.78 })
  })
  it('charts only what an MCU reports', () => {
    expect(mcuChartValues({ mcu_awake: 0.012 })).toEqual({ awake: 0.24 })
    expect(mcuChartValues(undefined)).toEqual({})
  })
})

describe('klipperChartValues', () => {
  it('turns cputime into a percent of a core over the sample time', () => {
    expect(klipperChartValues({ sysload: 0.65, cputime: 101.5 }, 100, 2, { timers: 80, main_thread_load: 0.251 })).toEqual({ load: 0.65, cputime_change: 75, timers: 80, main_thread: 25.1 })
  })
  it('waits for a second sample before charting Klipper load', () => {
    expect(klipperChartValues({ sysload: 0.5, cputime: 10 }, null, 0)).toEqual({ load: 0.5 })
  })
})

describe('pushSample', () => {
  it('keeps columns aligned and drops the oldest past retention', () => {
    const b = createBuffer()
    pushSample(b, 1, { a: 1 }, 3)
    pushSample(b, 2, { a: 2, b: 5 }, 3)
    pushSample(b, 3, { b: 6 }, 3)
    pushSample(b, 4, { a: 4, b: 7 }, 3)
    expect(b.times).toEqual([2, 3, 4])
    expect(b.cols.a).toEqual([2, NaN, 4])
    expect(b.cols.b).toEqual([5, 6, 7])
  })
})

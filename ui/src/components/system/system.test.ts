// The System screen's calculations, one block per source file.
import { describe, expect, it } from 'vitest'
import { createBuffer, klipperChartValues, mcuChartValues, pushSample } from './chart-data'
import { collectorPath, referencedObjects } from './collectors'
import { MAX_LINES, tailText } from './log-tail'

describe('chart-data.ts', () => {
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
})

describe('collectors.ts', () => {
  describe('referencedObjects', () => {
    it('finds dotted and bracketed objects', () => {
      expect(referencedObjects("printer.motion_report.live_extruder_velocity * Math.PI * (printer.configfile.settings.extruder.filament_diameter / 2) ** 2 + printer['tmc2209 extruder'].run_current")).toEqual([
        'motion_report',
        'configfile',
        'tmc2209 extruder',
      ])
    })
    it('finds nothing in a constant', () => {
      expect(referencedObjects('12')).toEqual([])
    })
  })

  describe('collectorPath', () => {
    it('builds paths a collector can evaluate', () => {
      expect(collectorPath(['toolhead', 'position', 0])).toBe('printer.toolhead.position[0]')
      expect(collectorPath(['mcu THR', 'last_stats', 'mcu_awake'])).toBe("printer['mcu THR'].last_stats.mcu_awake")
      expect(collectorPath(['gcode_macro _X', "it's"])).toBe("printer['gcode_macro _X']['it\\'s']")
    })
  })
})

describe('log-tail.ts', () => {
  describe('tailText', () => {
    it('keeps a whole log whole', () => {
      expect(tailText('a\nb\n', 0, 4)).toEqual({ lines: ['a', 'b'], shown: 4, total: 4, truncated: false })
    })
    it('drops the partial first line of a tail', () => {
      expect(tailText('ial line\nnext\nlast\n', 100, 119)).toEqual({ lines: ['next', 'last'], shown: 19, total: 119, truncated: true })
    })
    it('caps very long logs to the newest lines', () => {
      const t = tailText(Array.from({ length: MAX_LINES + 5 }, (_, i) => `l${i}`).join('\n'), 0, 1)
      expect(t.lines.length).toBe(MAX_LINES)
      expect(t.lines[0]).toBe('l5')
      expect(t.truncated).toBe(true)
    })
  })
})

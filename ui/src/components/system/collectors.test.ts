import { describe, expect, it } from 'vitest'
import { collectorPath, referencedObjects } from './collectors'

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

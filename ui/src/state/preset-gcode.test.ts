import { describe, expect, it } from 'vitest'
import { presetGcode, type TemperaturePreset } from './preset-gcode'

// Presets are keyed by the heater's short name, as Fluidd and the preset editor save them.
const preset = (values: TemperaturePreset['values'], gcode?: string): TemperaturePreset => ({ id: 'pla', name: 'PLA', values, gcode })

describe('presetGcode', () => {
  it('sets each ticked heater’s target, in the preset’s order', () => {
    const p = preset({
      extruder: { value: 220, type: 'heater', active: true },
      heater_bed: { value: 60, type: 'heater', active: true },
      heater_box1: { value: 45, type: 'heater', active: true },
    })
    expect(presetGcode(p)).toBe('SET_HEATER_TEMPERATURE HEATER=extruder TARGET=220\nSET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=60\nSET_HEATER_TEMPERATURE HEATER=heater_box1 TARGET=45')
  })

  it('leaves unticked targets alone, whatever value they hold', () => {
    const p = preset({
      extruder: { value: 220, type: 'heater', active: false },
      heater_bed: { value: 60, type: 'heater', active: true },
      chamber: { value: 0, type: 'heater', active: false },
    })
    expect(presetGcode(p)).toBe('SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=60')
  })

  it('sets a temperature fan with its own command', () => {
    const p = preset({ chamber_fan: { value: 42.5, type: 'fan', active: true } })
    expect(presetGcode(p)).toBe('SET_TEMPERATURE_FAN_TARGET TEMPERATURE_FAN=chamber_fan TARGET=42.5')
  })

  it('names a heater saved under its full Klipper section by its short name', () => {
    const p = preset({ 'heater_generic chamber': { value: 55, type: 'heater', active: true } })
    expect(presetGcode(p)).toBe('SET_HEATER_TEMPERATURE HEATER=chamber TARGET=55')
  })

  it('sends the preset’s own G-code after the targets, without surrounding blank space', () => {
    const p = preset({ heater_bed: { value: 60, type: 'heater', active: true } }, '\n  M106 S128\nSET_FAN_SPEED FAN=chamber SPEED=0.5\n')
    expect(presetGcode(p)).toBe('SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=60\nM106 S128\nSET_FAN_SPEED FAN=chamber SPEED=0.5')
  })

  it('sends only the G-code of a preset with no ticked targets, and nothing for a blank one', () => {
    expect(presetGcode(preset({ extruder: { value: 220, type: 'heater', active: false } }, 'G28'))).toBe('G28')
    expect(presetGcode(preset({ extruder: { value: 220, type: 'heater', active: false } }, '  \n'))).toBe('')
  })
})

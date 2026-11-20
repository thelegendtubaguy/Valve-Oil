import { FLUIDD, useDbItem } from './database'
import { runGcode } from './printer'
import { presetGcode, type TemperaturePreset } from './preset-gcode'

// The preset shape and the G-code a preset sends are in preset-gcode.ts, which has no import-time side effects.
export { presetGcode } from './preset-gcode'
export type { PresetValue, TemperaturePreset } from './preset-gcode'

export const PRESETS_KEY = 'uiSettings.dashboard.tempPresets'

export function usePresets() {
  return useDbItem<TemperaturePreset[]>(FLUIDD, PRESETS_KEY, [])
}

export function applyPreset(p: TemperaturePreset) {
  return runGcode(presetGcode(p))
}

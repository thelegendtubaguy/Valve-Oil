// The G-code a temperature preset sends. Nothing here touches Moonraker or the browser, so tests import it directly;
// presets.ts adds the database hook and re-exports everything here.

import { gcode } from '../components/control/gcode'

// Temperature presets, shared with Fluidd: the same database key and shape (Fluidd's TemperaturePreset), so a
// preset made in either shows in both.
export type PresetValue = { value: number; type: 'fan' | 'heater'; active: boolean }
export type TemperaturePreset = { id: number | string; name: string; values: Record<string, PresetValue>; gcode?: string }

/** The G-code a preset sends: each active heater's target and temperature fan's target, then its own G-code. */
export function presetGcode(p: TemperaturePreset): string {
  const lines: string[] = []
  for (const [name, v] of Object.entries(p.values)) {
    if (!v.active) continue
    const short = name.split(' ').pop()
    if (v.type === 'heater') lines.push(gcode`SET_HEATER_TEMPERATURE HEATER=${short} TARGET=${v.value}`)
    else lines.push(gcode`SET_TEMPERATURE_FAN_TARGET TEMPERATURE_FAN=${short} TARGET=${v.value}`)
  }
  if (p.gcode?.trim()) lines.push(p.gcode.trim())
  return lines.join('\n')
}

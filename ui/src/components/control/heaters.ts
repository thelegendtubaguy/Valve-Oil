// Heater target limits, as the Fluidd fork sets them ("limit heater targets like QIDI's Fluidd"): QIDI's Klipper
// reports target_min_temp and target_max_temp for each heater, and targets stay within those and the heater's
// min_temp and max_temp. A QIDI Box heater is further limited to the lowest Box temperature limit among the filaments
// loaded in that Box; when that is 0 the target is locked, though a Box already heating can still be turned off.

import { encodeGcodeParamValue, gcode } from './gcode'
import { t } from '../../i18n'

export type HeaterConfig = { min_temp?: number; max_temp?: number; target_min_temp?: number; target_max_temp?: number; control?: string }

export type TargetRange = { min: number; max: number; locked: boolean }

/** The targets a heater accepts besides 0 (off). `boxLimit` is the Box filament limit for a Box heater. */
export function targetRange(config: HeaterConfig | undefined, boxLimit: number | null = null, target = 0): TargetRange {
  const minTemp = config?.min_temp ?? 0
  const maxTemp = config?.max_temp ?? 500
  const configTargetMax = config?.target_max_temp
  const targetMax = boxLimit != null ? Math.min(boxLimit, configTargetMax ?? boxLimit) : configTargetMax
  const min = Math.max(minTemp, config?.target_min_temp ?? minTemp)
  const max = Math.min(maxTemp, targetMax ?? maxTemp)
  return { min, max, locked: targetMax === 0 && !target }
}

/** Why a target can't be set, or null when it can. 0 always turns the heater off. */
export function targetError(value: number, range: TargetRange): string | null {
  if (!Number.isFinite(value)) return t('control.heaters.enter_number')
  if (value === 0) return null
  if (range.locked || range.max <= 0) return t('control.heaters.locked')
  if (value < range.min) return t('control.heaters.at_least', { min: range.min })
  if (value > range.max) return t('control.heaters.at_most', { max: range.max })
  return null
}

/** A heater's name as SET_HEATER_TEMPERATURE and the calibrations take it, quoted when it needs to be. */
export const heaterParam = (obj: string) => encodeGcodeParamValue(obj.replace(/^heater_generic /, ''))

/** Sets a heater's target; 0 turns it off. */
export const heaterGcode = (obj: string, target: number) => gcode`SET_HEATER_TEMPERATURE HEATER=${heaterParam(obj)} TARGET=${target}`

/** The Box number of a QIDI Box heater object, or null. */
export function boxHeaterNumber(obj: string): number | null {
  const m = /^heater_generic heater_box(\d+)$/.exec(obj)
  return m ? Number(m[1]) : null
}

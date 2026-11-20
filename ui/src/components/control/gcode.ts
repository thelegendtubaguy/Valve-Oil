// G-code building and macro parameter parsing, ported from Fluidd (src/util/gcode-helpers.ts and
// src/util/gcode-macro-params.ts) so commands sent from here read the same as Fluidd's.

const basicGcodeCommandRegexp = /^[gm]\d+$/i
const quotableCharsRegexp = /[ '"#;=\\]/

export type GcodeParamValueType = number | string | false | null | undefined
export type GcodeParamValue = GcodeParamValueType | { value: GcodeParamValueType }

const rawGcodeCommands = { M117: 'Message', M118: 'Message', M23: 'Filename' } as const

/** The single free-text parameter of M117, M118, and M23, which take their text raw. */
export function getParamNameForRawGcodeCommand(commandName: string): string | false {
  return commandName in rawGcodeCommands && rawGcodeCommands[commandName as keyof typeof rawGcodeCommands]
}

export const isBasicGcodeCommand = (commandName: string) => basicGcodeCommandRegexp.test(commandName)

function getParamValueAsString(param: GcodeParamValue) {
  if (param && typeof param === 'object') param = param.value
  if (!param && param !== 0) return ''
  return typeof param === 'number' ? gcodeNumber(param) : param.toString()
}

/**
 * A number as Klipper reads it. JavaScript writes a number below 0.000001 as `1e-7`, which Klipper reads as 1 followed
 * by an E (extruder) word, so numbers are written in plain decimals, to six places. A number with no plain form (not
 * finite, or 1e21 and above) comes out as JavaScript writes it, and runGcode refuses to send it.
 */
export function gcodeNumber(value: number) {
  return String(Number(value.toFixed(6)))
}

/** A G-code template: each number in it is written by gcodeNumber, everything else as it is. */
export function gcode(strings: TemplateStringsArray, ...values: unknown[]) {
  return strings.reduce((text, part, i) => {
    const value = values[i - 1]
    return text + (typeof value === 'number' ? gcodeNumber(value) : String(value)) + part
  })
}

// A number Klipper misreads: `1e-7` is 1 and an E word, and `nan` and `inf` pass its range checks.
const misreadNumberRegexp = /^[+-]?((\d+\.?\d*|\.\d+)e[+-]?\d+|nan|inf(inity)?)$/i

/**
 * The first word of a script with a number Klipper would misread, or null. Valve Oil UI writes its numbers with
 * gcodeNumber, so this finds one that wasn't, or one with no plain form. The text M117, M118, and M23 take raw isn't
 * read as numbers.
 */
export function misreadGcodeNumber(script: string): string | null {
  for (const line of script.split('\n')) {
    const [command, ...words] = line.replace(/;.*/, '').trim().split(/\s+/)
    if (!command || getParamNameForRawGcodeCommand(command.toUpperCase())) continue
    for (const word of words) {
      const equals = word.indexOf('=')
      if (misreadNumberRegexp.test(equals >= 0 ? word.slice(equals + 1) : word.slice(1))) return word
    }
  }
  return null
}

/** Quotes a parameter value when Klipper would otherwise split or misread it. */
export function encodeGcodeParamValue(value: string) {
  if (quotableCharsRegexp.test(value)) return `"${value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`
  return value
}

/** `NAME KEY=value ...`, or `G1 X10` for basic G-code. Empty values are left out. */
export function gcodeCommandBuilder(commandName: string, params: Record<string, GcodeParamValue>) {
  commandName = commandName.toUpperCase()
  const basic = isBasicGcodeCommand(commandName)
  const raw = getParamNameForRawGcodeCommand(commandName)
  const paramsString = raw
    ? getParamValueAsString(params[raw])
    : Object.entries(params)
        .map(([key, param]) => {
          key = key.toUpperCase()
          const value = getParamValueAsString(param)
          if (!value) return null
          return basic ? `${key}${value}` : `${key}=${encodeGcodeParamValue(value)}`
        })
        .filter(Boolean)
        .join(' ')
  return paramsString ? `${commandName} ${paramsString}` : commandName
}

const paramRegExp = /params\.(\w+\b)(?!\s*\()(.*)/gi
const defaultValueRegExp = /\|\s*default\s*\(\s*((["'])(?:\\\2|(?!\2).)*\2|-?\d[^,)]*)/i

/** The literal default of `params.X|default(...)`, or '' when it has none or isn't a literal. */
export function gcodeMacroParamDefault(param: string) {
  const parsed = defaultValueRegExp.exec(param)
  if (!parsed) return ''
  const [, value, quoteChar] = parsed
  if (quoteChar) {
    return value
      .substring(1, value.length - 1)
      .replace(new RegExp(`\\\\${quoteChar}`, 'g'), quoteChar)
      .replace(/\\\\/g, '\\')
  }
  return (value || '').trim()
}

/** Every `params.NAME` a macro's G-code reads, with its default. */
export function gcodeMacroParams(gcode: string) {
  return [...gcode.matchAll(paramRegExp)].map(([, name, rest]) => ({ name, value: gcodeMacroParamDefault(rest) }))
}

/**
 * A toolhead move as Fluidd sends it: through the `_CLIENT_LINEAR_MOVE` macro when the printer has one, else inside a
 * saved G-code state so the printer isn't left in relative mode. Rates are mm/s.
 */
export function moveGcode(movement: Partial<Record<'X' | 'Y' | 'Z', number>>, rate: number, absolute: boolean, clientMacro: string | null) {
  const sep = clientMacro ? '=' : ''
  const words = Object.entries(movement)
    .map(([k, v]) => `${k}${sep}${gcodeNumber(v)}`)
    .join(' ')
  if (clientMacro) return gcode`${clientMacro.toUpperCase()} ${words} F=${rate * 60}${absolute ? ' ABSOLUTE=1' : ''}`
  return gcode`SAVE_GCODE_STATE NAME=_ui_movement\nG9${absolute ? 0 : 1}\nG1 ${words} F${rate * 60}\nRESTORE_GCODE_STATE NAME=_ui_movement`
}

/** An extrude (positive) or retract (negative) move, as Fluidd sends it. Rate is mm/s. */
export function extrudeGcode(amount: number, rate: number, clientMacro: string | null) {
  if (clientMacro) return gcode`${clientMacro.toUpperCase()} E=${amount} F=${rate * 60}`
  return gcode`SAVE_GCODE_STATE NAME=_ui_retract\nM83\nG1 E${amount} F${rate * 60}\nRESTORE_GCODE_STATE NAME=_ui_retract`
}

/** The command that drops one object from the running print. Klipper's object names are upper case without spaces. */
export const excludeObjectGcode = (name: string) =>
  `EXCLUDE_OBJECT NAME=${encodeGcodeParamValue(name.toUpperCase().replace(/\s/g, '_'))}`

/**
 * A jog distance shortened so the move stays inside the axis limits Klipper reports (toolhead.axis_minimum and
 * axis_maximum), so a jog near the end of travel moves to the end instead of failing with "Move out of range".
 */
export function clampJog(position: number | undefined, distance: number, min: number | undefined, max: number | undefined) {
  if (position == null) return distance
  let target = position + distance
  if (max != null && target > max) target = max
  if (min != null && target < min) target = min
  return Math.round((target - position) * 1000) / 1000
}

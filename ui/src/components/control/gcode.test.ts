import { afterAll, describe, it, expect, vi } from 'vitest'
import {
  clampJog,
  excludeObjectGcode,
  extrudeGcode,
  gcode,
  gcodeCommandBuilder,
  gcodeMacroParamDefault,
  gcodeMacroParams,
  misreadGcodeNumber,
  moveGcode,
} from './gcode'

// Ported from Fluidd's gcode-command-builder.spec.ts and gcode-macro-params.spec.ts.
describe('gcodeCommandBuilder', () => {
  it.each([
    [{ command: 'M117', params: { Message: '' } }, 'M117'],
    [{ command: 'M117', params: { Message: 'ab' } }, 'M117 ab'],
    [{ command: 'M117', params: { Message: { value: 'ab' } } }, 'M117 ab'],
    [{ command: 'G1', params: {} }, 'G1'],
    [{ command: 'G1', params: { X: '' } }, 'G1'],
    [{ command: 'G1', params: { X: 10 } }, 'G1 X10'],
    [{ command: 'G1', params: { X: { value: 10 } } }, 'G1 X10'],
    [{ command: 'TEST', params: {} }, 'TEST'],
    [{ command: 'TEST', params: { X: '' } }, 'TEST'],
    [{ command: 'TEST', params: { X: 'abc' } }, 'TEST X=abc'],
    [{ command: 'TEST', params: { X: 'a b c' } }, 'TEST X="a b c"'],
    [{ command: 'TEST', params: { X: ' a b c ' } }, 'TEST X=" a b c "'],
    [{ command: 'TEST', params: { X: "a'b'c" } }, 'TEST X="a\'b\'c"'],
    [{ command: 'TEST', params: { X: 'a"b"c' } }, 'TEST X="a\\"b\\"c"'],
    [{ command: 'TEST', params: { X: 'a#b#c' } }, 'TEST X="a#b#c"'],
    [{ command: 'TEST', params: { X: 'a;b;c' } }, 'TEST X="a;b;c"'],
    [{ command: 'TEST', params: { X: 'a=b=c' } }, 'TEST X="a=b=c"'],
    [{ command: 'TEST', params: { X: 'a\\b\\c' } }, 'TEST X="a\\\\b\\\\c"'],
  ])('builds %o as %s', ({ command, params }, expected) => {
    expect(gcodeCommandBuilder(command, params)).toStrictEqual(expected)
  })
})

describe('gcodeMacroParams', () => {
  it.each([
    ['{% set ENABLE = params.ENABLE %}', [{ name: 'ENABLE', value: '' }]],
    ['{% set ENABLE = params.ENABLE | default(1) %}', [{ name: 'ENABLE', value: '1' }]],
    ['{% set ENABLE = params._ENABLE %}', [{ name: '_ENABLE', value: '' }]],
    ['{% set ENABLE = params._ENABLE | default(1) %}', [{ name: '_ENABLE', value: '1' }]],
    ['{% for p, v in params.items()', []],
    ['{% for p, v in params.items () %}', []],
  ])('reads the parameters of %s', (param, expected) => {
    expect(gcodeMacroParams(param)).toStrictEqual(expected)
  })
})

describe('gcodeMacroParamDefault', () => {
  it.each([
    [' | default(0, true) | float', '0'],
    [' | int | default(0.005, true) | float', '0.005'],
    [' | default("PLA", true)', 'PLA'],
    [' | default("PLA")', 'PLA'],
    ['| int | default(0.2)', '0.2'],
    ['| int | default(-0.2)', '-0.2'],
    ['| int | default( -0.2 )', '-0.2'],
    ['| int | default( - 0.2 )', ''],
    ['|default(printer)|int', ''],
    ['|default(-somevar)|int', ''],
    ['|default(printer.configfile.settings.printer.max_velocity)|int', ''],
    ['|default("this, \\"works,\\"" , true)|trim', 'this, "works,"'],
    ['|default(\'this, "works,"\' , true)|trim', 'this, "works,"'],
    ['|default(",\\"fine,\\"",true)|trim', ',"fine,"'],
    ['|default(",\\"\\\\fine\\\\,\\"",true)|trim', ',"\\fine\\,"'],
  ])('reads the default of %s as %s', (param, expected) => {
    expect(gcodeMacroParamDefault(param)).toBe(expected)
  })
})

describe('clampJog', () => {
  it('shortens a jog that would leave the axis limits', () => {
    expect(clampJog(385, 10, -2, 392)).toBe(7)
    expect(clampJog(3, -10, -2, 392)).toBe(-5)
    expect(clampJog(100, 10, -2, 392)).toBe(10)
  })
  it('jogs the full distance when the limits are unknown', () => {
    expect(clampJog(385, 10, undefined, undefined)).toBe(10)
  })
})

// Rates are mm/s on the screen and mm/min in G-code: 130 mm/s for X and Y is F7800, 10 mm/s for Z is F600.
describe('moveGcode', () => {
  it('jogs in relative mode inside a saved G-code state, so the printer’s own mode is restored', () => {
    expect(moveGcode({ X: 10 }, 130, false, null)).toBe('SAVE_GCODE_STATE NAME=_ui_movement\nG91\nG1 X10 F7800\nRESTORE_GCODE_STATE NAME=_ui_movement')
  })

  it('jogs backwards and by fractions of a millimeter', () => {
    expect(moveGcode({ Y: -50 }, 130, false, null)).toBe('SAVE_GCODE_STATE NAME=_ui_movement\nG91\nG1 Y-50 F7800\nRESTORE_GCODE_STATE NAME=_ui_movement')
    expect(moveGcode({ Z: -0.05 }, 10, false, null)).toBe('SAVE_GCODE_STATE NAME=_ui_movement\nG91\nG1 Z-0.05 F600\nRESTORE_GCODE_STATE NAME=_ui_movement')
  })

  it('moves to an entered position in absolute mode', () => {
    expect(moveGcode({ Z: 25.5 }, 10, true, null)).toBe('SAVE_GCODE_STATE NAME=_ui_movement\nG90\nG1 Z25.5 F600\nRESTORE_GCODE_STATE NAME=_ui_movement')
    expect(moveGcode({ X: -2 }, 130, true, null)).toBe('SAVE_GCODE_STATE NAME=_ui_movement\nG90\nG1 X-2 F7800\nRESTORE_GCODE_STATE NAME=_ui_movement')
  })

  it('sends a jog that clampJog shortened at the end of travel', () => {
    expect(moveGcode({ X: clampJog(385, 10, -2, 392) }, 130, false, null)).toBe('SAVE_GCODE_STATE NAME=_ui_movement\nG91\nG1 X7 F7800\nRESTORE_GCODE_STATE NAME=_ui_movement')
  })

  it('moves through the printer’s _CLIENT_LINEAR_MOVE macro when it has one, relative unless told otherwise', () => {
    expect(moveGcode({ X: -10 }, 130, false, '_CLIENT_LINEAR_MOVE')).toBe('_CLIENT_LINEAR_MOVE X=-10 F=7800')
    expect(moveGcode({ Z: 25.5 }, 10, true, '_CLIENT_LINEAR_MOVE')).toBe('_CLIENT_LINEAR_MOVE Z=25.5 F=600 ABSOLUTE=1')
  })

  // JavaScript writes 0.0000001 as 1e-7, and Klipper reads `X1e-7` as X1 and an extruder move of -7.
  it('moves to a position typed with more decimals than Klipper needs without writing it as 1e-7', () => {
    expect(moveGcode({ X: 0.0000001 }, 130, true, null)).toBe('SAVE_GCODE_STATE NAME=_ui_movement\nG90\nG1 X0 F7800\nRESTORE_GCODE_STATE NAME=_ui_movement')
    expect(moveGcode({ X: -0.0000005 }, 130, true, '_CLIENT_LINEAR_MOVE')).toBe('_CLIENT_LINEAR_MOVE X=0 F=7800 ABSOLUTE=1')
    expect(moveGcode({ Z: 0.000001 }, 10, true, null)).toContain('G1 Z0.000001 F600')
  })
})

describe('extrudeGcode', () => {
  it('extrudes in relative extrusion mode inside a saved G-code state', () => {
    expect(extrudeGcode(25, 5, null)).toBe('SAVE_GCODE_STATE NAME=_ui_retract\nM83\nG1 E25 F300\nRESTORE_GCODE_STATE NAME=_ui_retract')
  })

  it('retracts with a negative length', () => {
    expect(extrudeGcode(-25, 5, null)).toBe('SAVE_GCODE_STATE NAME=_ui_retract\nM83\nG1 E-25 F300\nRESTORE_GCODE_STATE NAME=_ui_retract')
    expect(extrudeGcode(-0.5, 0.5, null)).toBe('SAVE_GCODE_STATE NAME=_ui_retract\nM83\nG1 E-0.5 F30\nRESTORE_GCODE_STATE NAME=_ui_retract')
  })

  it('extrudes and retracts through the printer’s _CLIENT_LINEAR_MOVE macro when it has one', () => {
    expect(extrudeGcode(25, 5, '_CLIENT_LINEAR_MOVE')).toBe('_CLIENT_LINEAR_MOVE E=25 F=300')
    expect(extrudeGcode(-25, 5, '_CLIENT_LINEAR_MOVE')).toBe('_CLIENT_LINEAR_MOVE E=-25 F=300')
  })

  it('never writes an amount as 1e-7, which Klipper reads as a retract of 7 mm', () => {
    expect(extrudeGcode(0.0000001, 5, null)).toContain('G1 E0 F300')
  })
})

describe('numbers sent to Klipper', () => {
  it('are written in plain decimals, to six places', () => {
    const target = 0.1 + 0.2
    expect(gcode`SET_HEATER_TEMPERATURE HEATER=extruder TARGET=${target}`).toBe('SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0.3')
    expect(gcode`SET_PRESSURE_ADVANCE ADVANCE=${0.0000004} SMOOTH_TIME=${0.0325}`).toBe('SET_PRESSURE_ADVANCE ADVANCE=0 SMOOTH_TIME=0.0325')
    expect(gcode`M106 S${255} ; ${'fan'}`).toBe('M106 S255 ; fan')
    expect(gcodeCommandBuilder('PARK', { x: 0.0000001, speed: 120 })).toBe('PARK X=0 SPEED=120')
  })

  it('that Klipper would misread are found, in G-code words and in parameters', () => {
    for (const script of ['G1 X1e-7 F7800', '_CLIENT_LINEAR_MOVE X=1E-7 F=7800', 'SET_VELOCITY_LIMIT VELOCITY=1e+21',
      `SET_HEATER_TEMPERATURE HEATER=extruder TARGET=${NaN}`, `M104 S${Infinity}`, 'G90\nG1 Z-5e-7']) {
      expect(misreadGcodeNumber(script), script).not.toBeNull()
    }
    for (const script of ['G1 X0.000001 F7800', 'M117 Done in 1e5 seconds', 'RESPOND MSG="1e-7"', 'BED_MESH_PROFILE LOAD=default',
      'SET_GCODE_OFFSET Z_ADJUST=+0.01 MOVE=1', 'EXCLUDE_OBJECT NAME="PART;1E5"']) {
      expect(misreadGcodeNumber(script), script).toBeNull()
    }
  })
})

describe('runGcode', () => {
  afterAll(() => vi.unstubAllGlobals())

  it('refuses to send a command with a number Klipper would misread, but sends what was typed in the console', async () => {
    vi.stubGlobal('location', new URL('http://printer.test/valve-oil/'))
    vi.stubGlobal('localStorage', undefined)
    const { client, runGcode } = await import('../../state/printer')
    const sent: string[] = []
    vi.spyOn(client, 'gcode').mockImplementation(async (script: string) => (sent.push(script), 'ok'))

    await expect(runGcode('G1 X1e-7 F7800')).rejects.toThrow('X1e-7')
    await expect(runGcode(gcode`SET_HEATER_TEMPERATURE HEATER=extruder TARGET=${NaN}`)).rejects.toThrow('TARGET=NaN')
    await expect(runGcode(moveGcode({ X: 0.0000001 }, 130, true, null))).resolves.toBe('ok')
    await expect(runGcode('G1 X1e-7 F7800', { typed: true })).resolves.toBe('ok')
    expect(sent).toEqual([moveGcode({ X: 0 }, 130, true, null), 'G1 X1e-7 F7800'])
  })
})

describe('excludeObjectGcode', () => {
  it('names the object as Klipper knows it: upper case, with spaces as underscores', () => {
    expect(excludeObjectGcode('planter_body_id_0_copy_1')).toBe('EXCLUDE_OBJECT NAME=PLANTER_BODY_ID_0_COPY_1')
    expect(excludeObjectGcode('Benchy v2')).toBe('EXCLUDE_OBJECT NAME=BENCHY_V2')
  })

  it('quotes a name that would otherwise end the parameter or start another command', () => {
    expect(excludeObjectGcode('part;M112')).toBe(`EXCLUDE_OBJECT NAME=${JSON.stringify('PART;M112')}`)
    expect(excludeObjectGcode('a"b')).toBe('EXCLUDE_OBJECT NAME="A\\"B"')
  })
})

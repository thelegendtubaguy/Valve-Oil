import { describe, it, expect } from 'vitest'
import { clampJog, gcodeCommandBuilder, gcodeMacroParamDefault, gcodeMacroParams } from './gcode'

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

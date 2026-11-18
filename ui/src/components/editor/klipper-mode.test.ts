import { describe, expect, it } from 'vitest'
import { StringStream, type StreamParser } from '@codemirror/language'
import { gcodeFile, klipperConfig } from './klipper-mode'

/** Runs a parser over text the way StreamLanguage does: each line's tokens as [text, style] pairs. */
function tokens<S>(parser: StreamParser<S>, text: string): [string, string | null][][] {
  const state = parser.startState!(2)
  return text.split('\n').map((line) => {
    const out: [string, string | null][] = []
    if (!line) {
      parser.blankLine?.(state, 2)
      return out
    }
    const stream = new StringStream(line, 2, 2)
    while (!stream.eol()) {
      const style = parser.token(stream, state)
      const text = line.slice(stream.start, stream.pos)
      if (text.trim()) out.push([text, style])
      stream.start = stream.pos
    }
    return out
  })
}

const styleOf = (lines: [string, string | null][][], text: string) => lines.flat().find(([t]) => t === text)?.[1]

describe('Klipper config highlighting', () => {
  const cfg = tokens(
    klipperConfig,
    [
      '[gcode_macro PRINT_START]',
      'description: Start the print # inline',
      'variable_temp: 215',
      'gcode:',
      '  {% set bed = params.BED|default(60)|float %}',
      '  M140 S{bed}',
      '  SET_HEATER_TEMPERATURE HEATER=chamber TARGET=45',
      '# a comment line inside the macro',
      '  G28',
      '',
      '[printer]',
      'kinematics: corexy',
      'max_velocity: 600',
      '#*# <---------------------- SAVE_CONFIG ---------------------->',
    ].join('\n'),
  )

  it('reads section headers', () => {
    expect(styleOf(cfg, 'gcode_macro')).toBe('heading')
    expect(styleOf(cfg, 'PRINT_START')).toBe('labelName')
    expect(styleOf(cfg, 'printer')).toBe('heading')
  })

  it('reads keys, values, and comments', () => {
    expect(styleOf(cfg, 'description')).toBe('propertyName')
    expect(styleOf(cfg, '# inline')).toBe('comment')
    expect(styleOf(cfg, '215')).toBe('number')
    expect(styleOf(cfg, 'kinematics')).toBe('propertyName')
    expect(styleOf(cfg, 'corexy')).toBe('string')
  })

  it('reads the G-code and Jinja in a gcode: value, past comment lines, until the next section', () => {
    expect(styleOf(cfg, 'M140')).toBe('keyword')
    expect(styleOf(cfg, 'SET_HEATER_TEMPERATURE')).toBe('keyword')
    expect(styleOf(cfg, 'HEATER')).toBe('propertyName')
    expect(styleOf(cfg, 'set')).toBe('controlKeyword')
    expect(styleOf(cfg, 'params')).toBe('variableName.special')
    expect(styleOf(cfg, 'G28')).toBe('keyword')
    expect(cfg[7][0][1]).toBe('comment')
    // max_velocity is a key again, not G-code.
    expect(styleOf(cfg, 'max_velocity')).toBe('propertyName')
  })

  it('marks the SAVE_CONFIG block', () => {
    expect(cfg[13][0][1]).toBe('meta')
  })
})

describe('G-code highlighting', () => {
  const g = tokens(gcodeFile, ['G1 X10.5 Y-2 F3000 ; move', 'EXCLUDE_OBJECT_DEFINE NAME=part_1'].join('\n'))
  it('reads commands, parameters, and comments', () => {
    expect(g[0][0]).toEqual(['G1', 'keyword'])
    expect(styleOf(g, 'X')).toBe('propertyName')
    expect(styleOf(g, '10.5')).toBe('number')
    expect(styleOf(g, '; move')).toBe('comment')
    expect(styleOf(g, 'NAME')).toBe('propertyName')
  })
})

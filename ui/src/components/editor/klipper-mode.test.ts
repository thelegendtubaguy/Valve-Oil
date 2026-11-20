import { describe, expect, it } from 'vitest'
import { StringStream, type StreamParser } from '@codemirror/language'
import { gcodeFile, klipperConfig } from './klipper-mode'

/** How a parser colors each piece of text, run line by line the way StreamLanguage does. */
function styles<S>(parser: StreamParser<S>, lines: string[]): (text: string) => string | null | undefined {
  const state = parser.startState!(2)
  const found = new Map<string, string | null>()
  for (const line of lines) {
    if (!line) {
      parser.blankLine?.(state, 2)
      continue
    }
    const stream = new StringStream(line, 2, 2)
    while (!stream.eol()) {
      const style = parser.token(stream, state)
      const text = line.slice(stream.start, stream.pos).trim()
      if (text && !found.has(text)) found.set(text, style)
      stream.start = stream.pos
    }
  }
  return (text) => found.get(text)
}

// These compare how pieces of a file are colored with each other, not which color each gets.
describe('Klipper config highlighting', () => {
  it('colors a gcode: value as G-code past comment lines, and ends it at the next section', () => {
    const style = styles(klipperConfig, [
      '[gcode_macro PRINT_START]',
      'variable_temp: 215',
      'gcode:',
      '  M140 S60',
      '# a comment line inside the macro',
      '  G28',
      '',
      '[printer]',
      'max_velocity: 600',
    ])
    // G28 is still a command after the comment line, as M140 is before it.
    expect(style('M140')).toBeTruthy()
    expect(style('G28')).toBe(style('M140'))
    // max_velocity is a config key again, like variable_temp, and not a command.
    expect(style('max_velocity')).toBe(style('variable_temp'))
    expect(style('max_velocity')).not.toBe(style('M140'))
    // And [printer] is a section header, like [gcode_macro ...].
    expect(style('printer')).toBe(style('gcode_macro'))
  })
})

describe('G-code file highlighting', () => {
  it('colors the first word of every line as its command, apart from its parameters and comments', () => {
    const style = styles(gcodeFile, ['G1 X10.5 F3000 ; move', 'EXCLUDE_OBJECT NAME=part_1'])
    expect(style('G1')).toBeTruthy()
    expect(style('EXCLUDE_OBJECT')).toBe(style('G1'))
    expect(style('NAME')).not.toBe(style('G1'))
    expect(style('; move')).not.toBe(style('G1'))
  })
})

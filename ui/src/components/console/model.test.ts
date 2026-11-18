import { describe, expect, it } from 'vitest'
import { addToHistory, applyCompletion, compileFilter, completions, currentCommand, normalizeFilter, segments, visibleEntries, type ConsoleEntry, type ConsoleFilter } from './model'

const entry = (message: string, extra: Partial<ConsoleEntry> = {}): ConsoleEntry => ({ id: 1, time: 1000, message, type: 'response', ...extra })
const filter = (type: ConsoleFilter['type'], value: string, enabled = true): ConsoleFilter => ({ id: 'f', name: 'f', type, value, enabled })
const view = { hideTempWaits: false, filters: [] as ConsoleFilter[], lastCleared: 0 }

describe('console filters stored by Fluidd', () => {
  it('reads the numeric types older Fluidd releases stored', () => {
    expect(normalizeFilter({ ...filter('contains', 'x'), type: 1 }).type).toBe('starts-with')
    expect(normalizeFilter({ ...filter('contains', 'x'), type: 2 }).type).toBe('expression')
    expect(normalizeFilter({ ...filter('contains', 'x'), type: 0 }).type).toBe('contains')
  })

  it('matches as Fluidd does: contains and starts-with ignore case and literal characters, expressions are as written', () => {
    expect(compileFilter(filter('contains', 'PRESSURE_advance')).test('// pressure_advance: 0.02')).toBe(true)
    expect(compileFilter(filter('starts-with', '// pressure')).test('x // pressure')).toBe(false)
    expect(compileFilter(filter('contains', 'a.b')).test('axb')).toBe(false)
    expect(compileFilter(filter('expression', '^B:\\d')).test('B:55.0 /55.0')).toBe(true)
    expect(compileFilter(filter('expression', '^b:\\d')).test('B:55.0 /55.0')).toBe(false)
  })

  it('a broken expression hides nothing', () => {
    expect(visibleEntries([entry('anything')], { ...view, filters: [filter('expression', '(')] })).toHaveLength(1)
  })

  it('hides what enabled filters match, temperature waits when asked, bare ok, and lines before the last clear', () => {
    const entries = [
      entry('B:55.0 /55.0 T0:146.8 /215.0'),
      entry('ok B:55.0 /55.0 T0:146.8 /215.0'),
      entry('ok'),
      entry('// pressure_advance: 0.020000'),
      entry('!! Move out of range'),
      entry('// old', { time: 10 }),
    ]
    const shown = visibleEntries(entries, { hideTempWaits: true, filters: [filter('contains', 'pressure_advance'), filter('contains', 'Move', false)], lastCleared: 20_000 })
    expect(shown.map((e) => e.message)).toEqual(['!! Move out of range'])
    expect(visibleEntries(entries, view)).toHaveLength(5)
  })

  it('search keeps only the lines containing it, ignoring case', () => {
    expect(visibleEntries([entry('// Homing'), entry('!! Move out of range')], { ...view, search: 'move' }).map((e) => e.message)).toEqual(['!! Move out of range'])
  })
})

describe('known commands in replies', () => {
  const known = new Set(['TLTG_BOX_SLOT_JOG', 'SAVE_CONFIG'])
  it('makes the commands Klipper knows clickable and leaves other words', () => {
    expect(segments('// Run TLTG_BOX_SLOT_JOG, then SAVE_CONFIG. NOT_A_COMMAND', known)).toEqual([
      { text: '// Run ' },
      { text: 'TLTG_BOX_SLOT_JOG', command: 'TLTG_BOX_SLOT_JOG' },
      { text: ', then ' },
      { text: 'SAVE_CONFIG', command: 'SAVE_CONFIG' },
      { text: '. NOT_A_COMMAND' },
    ])
  })
  it('marks search matches without losing a command', () => {
    expect(segments('use SAVE_CONFIG', known, 'config')).toEqual([{ text: 'use ' }, { text: 'SAVE_', command: 'SAVE_CONFIG' }, { text: 'CONFIG', command: 'SAVE_CONFIG', mark: true }])
  })
})

describe('the command line', () => {
  const cmds = ['G28', 'SET_FAN_SPEED', 'SET_HEATER_TEMPERATURE', 'SAVE_CONFIG']
  it('completes the first word of the last line', () => {
    expect(completions('set_', 4, cmds).matches).toEqual(['SET_FAN_SPEED', 'SET_HEATER_TEMPERATURE'])
    expect(completions('G28\nsa', 6, cmds).matches).toEqual(['SAVE_CONFIG'])
    expect(completions('SET_FAN_SPEED FAN=', 18, cmds).matches).toEqual([])
    expect(completions('set_', 2, cmds).matches).toEqual([])
    expect(applyCompletion('G28\nsa', 'SAVE_CONFIG')).toBe('G28\nSAVE_CONFIG ')
  })
  it('names the command on the caret’s line, for its help', () => {
    expect(currentCommand('G28\nset_fan_speed FAN=x', 10)).toBe('SET_FAN_SPEED')
    expect(currentCommand('G28\nM84', 2)).toBe('G28')
  })
  it('keeps Fluidd’s 20 newest commands, newest first, repeats included', () => {
    let h: string[] = []
    for (let i = 0; i < 25; i++) h = addToHistory(h, `C${i}`)
    h = addToHistory(h, 'C24')
    expect(h).toHaveLength(20)
    expect(h.slice(0, 3)).toEqual(['C24', 'C24', 'C23'])
  })
})

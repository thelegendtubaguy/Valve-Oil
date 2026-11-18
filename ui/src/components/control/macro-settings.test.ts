import { describe, it, expect } from 'vitest'
import { listMacros, macroGroups, macroParams, removeCategory, saveMacros, type MacrosDb } from './macro-settings'

const objects = ['gcode_macro VO_TEST_PROBE', 'gcode_macro CLEAR_NOZZLE', 'gcode_macro _km_globals', 'gcode_macro M117', 'extruder']
const settings = {
  'gcode_macro vo_test_probe': { description: 'Probe the bed', gcode: '{% set s = params.SAMPLES|default(10)|int %}\n{% set x = params._X %}' },
  'gcode_macro clear_nozzle': { description: 'G-Code macro', gcode: 'G28' },
}
// As stored on a Max 4 by Fluidd.
const db: MacrosDb = {
  expanded: [0],
  categories: [{ id: 'vo', name: 'VO' }],
  stored: [{ name: 'vo_test_probe', alias: '', visible: true, disabledWhilePrinting: true, color: '', categoryId: 'vo' }],
}

describe('listMacros', () => {
  it('lists macros without a leading underscore, with stored settings matched regardless of case', () => {
    const macros = listMacros(objects, settings, db)
    expect(macros.map((m) => m.name)).toEqual(['CLEAR_NOZZLE', 'M117', 'VO_TEST_PROBE'])
    expect(macros.find((m) => m.name === 'VO_TEST_PROBE')).toMatchObject({ categoryId: 'vo', disabledWhilePrinting: true, description: 'Probe the bed' })
    expect(macros.find((m) => m.name === 'CLEAR_NOZZLE')?.description).toBeUndefined()
  })

  it('puts macros of a deleted category back in uncategorized', () => {
    const macros = listMacros(objects, settings, { ...db, categories: [] })
    expect(macros.find((m) => m.name === 'VO_TEST_PROBE')?.categoryId).toBe('0')
  })
})

describe('macroGroups', () => {
  it('groups visible macros by category with uncategorized last', () => {
    const groups = macroGroups(listMacros(objects, settings, db), db)
    expect(groups.map((g) => [g.name, g.macros.map((m) => m.name)])).toEqual([
      ['VO', ['VO_TEST_PROBE']],
      [null, ['CLEAR_NOZZLE', 'M117']],
    ])
  })
})

describe('saveMacros', () => {
  it('updates a stored macro in place and adds new ones with Fluidd’s keys only', () => {
    const next = saveMacros(db, [
      { name: 'VO_TEST_PROBE', alias: 'Test probe', description: 'not stored' },
      { name: 'CLEAR_NOZZLE', visible: false },
    ])
    expect(next.stored).toEqual([
      { name: 'vo_test_probe', alias: 'Test probe', visible: true, disabledWhilePrinting: true, color: '', categoryId: 'vo' },
      { name: 'clear_nozzle', alias: '', visible: false, disabledWhilePrinting: false, color: '', categoryId: '0' },
    ])
  })
})

describe('removeCategory', () => {
  it('drops the category and uncategorizes its macros', () => {
    const next = removeCategory(db, 'vo')
    expect(next.categories).toEqual([])
    expect(next.stored?.[0].categoryId).toBeUndefined()
  })
})

describe('macroParams', () => {
  it('offers parameters the macro reads, except private ones', () => {
    expect(macroParams({ name: 'VO_TEST_PROBE', gcode: settings['gcode_macro vo_test_probe'].gcode })).toEqual([{ name: 'SAMPLES', value: '10' }])
  })
  it('gives M117 its message', () => {
    expect(macroParams({ name: 'M117' })).toEqual([{ name: 'Message', value: '' }])
  })
})

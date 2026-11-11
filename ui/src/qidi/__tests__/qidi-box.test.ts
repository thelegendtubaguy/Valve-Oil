import { describe, it, expect } from 'vitest'
import { matchQidiBoxSlots, parseQidiFilamentDatabase, parseQidiFilamentList, qidiBoxFileTools, qidiBoxReloadGroups, qidiBoxSlots } from '../qidi-box'

const filamentList = parseQidiFilamentList([
  '[fila18]',
  'filament                       = ASA',
  'box_max_temp                   = 45',
  'type                           = ASA',
  '',
  '[colordict]',
  '2                              = #060606',
  '',
  '[vendor_list]',
  '0                              = Generic'
].join('\n'))

const filamentInfo = (index: number, type: string, color: string) => ({
  index,
  filament_id: 'QFA90',
  filament_str: type,
  filament_type: type,
  vendor_str: 'Generic',
  color_rgb_str: [color],
  box_max_temp: 0
})

describe('qidiBoxSlots', () => {
  it('reads firmware 01.01.07.01 resolved slot filaments', () => {
    const slots = qidiBoxSlots({
      save_variables: { variables: { box_count: 1 } },
      box_extras: {
        boxes: [{
          filament_info: [
            filamentInfo(0, 'PLA', '#228332'),
            filamentInfo(1, 'PLA', 'ff362d'),
            filamentInfo(2, 'PETG', '#FFFFFF'),
            filamentInfo(3, 'PLA', '#000000')
          ]
        }]
      },
      'box_stepper slot0': { runout_button: 0 },
      'box_stepper slot1': { runout_button: 0 },
      'box_stepper slot2': { runout_button: 0 },
      'box_stepper slot3': { runout_button: 1 }
    }, null)

    expect(slots.map(slot => [slot.label, slot.present, slot.type, slot.color])).toStrictEqual([
      ['1A', true, 'PLA', '#228332'],
      ['1B', true, 'PLA', '#FF362D'],
      ['1C', true, 'PETG', '#FFFFFF'],
      ['1D', false, 'PLA', '#000000'],
      ['Ext', true, null, null]
    ])
  })

  it('resolves numeric IDs from earlier firmware through the filament list', () => {
    const [slot] = qidiBoxSlots({
      save_variables: { variables: { box_count: 1, filament_slot0: 18, color_slot0: 2, vendor_slot0: 0 } },
      'box_stepper slot0': { runout_button: 0 }
    }, filamentList)

    expect(slot).toMatchObject({ present: true, name: 'ASA', type: 'ASA', vendor: 'Generic', color: '#060606', boxMaxTemp: 45 })
  })
})

describe('matchQidiBoxSlots', () => {
  const slots = qidiBoxSlots({
    save_variables: { variables: { box_count: 1 } },
    box_extras: {
      boxes: [{
        filament_info: [
          filamentInfo(0, 'PLA', '#228332'),
          filamentInfo(1, 'PLA', '#FF362D'),
          filamentInfo(2, 'PETG', '#FFFFFF'),
          filamentInfo(3, 'PLA', '#000000')
        ]
      }]
    },
    'box_stepper slot0': { runout_button: 0 },
    'box_stepper slot1': { runout_button: 0 },
    'box_stepper slot2': { runout_button: 0 },
    'box_stepper slot3': { runout_button: 1 }
  }, null)

  it('gives each tool the nearest-colored unused loaded slot of its type', () => {
    expect(matchQidiBoxSlots([
      { tool: 0, type: 'PLA', color: '#FF0000' },
      { tool: 1, type: 'pla', color: '#FF0000' },
      { tool: 2, type: 'PETG', color: '#000000' }
    ], slots)).toStrictEqual({ 0: 1, 1: 0, 2: 2 })
  })

  it('leaves a tool unassigned when no unused slot of its type is loaded', () => {
    expect(matchQidiBoxSlots([
      { tool: 0, type: 'PETG', color: '#FFFFFF' },
      { tool: 1, type: 'PETG', color: '#FFFFFF' },
      { tool: 2, type: 'ABS', color: '#FFFFFF' }
    ], slots)).toStrictEqual({ 0: 2, 1: null, 2: null })
  })
})

describe('qidiBoxFileTools', () => {
  const project = {
    preset_colours: ['#111111', '#222222', '#333333'],
    filament_type: ['PLA', 'PETG', 'PLA']
  }

  it('lists the tools the file names once each, with their filament', () => {
    expect(qidiBoxFileTools({ ...project, used_extruders: [2, 0, 2] })).toStrictEqual({
      tools: [
        { tool: 2, type: 'PLA', color: '#333333' },
        { tool: 0, type: 'PLA', color: '#111111' }
      ],
      named: true
    })
  })

  it('lists every project filament when the file names no tools', () => {
    expect(qidiBoxFileTools({ ...project, used_extruders: [] })).toStrictEqual({
      tools: [
        { tool: 0, type: 'PLA', color: '#111111' },
        { tool: 1, type: 'PETG', color: '#222222' },
        { tool: 2, type: 'PLA', color: '#333333' }
      ],
      named: false
    })
  })

  it('lists T0 for a file with no filament information', () => {
    expect(qidiBoxFileTools({})).toStrictEqual({
      tools: [{ tool: 0, type: '', color: '' }],
      named: false
    })
  })
})

describe('qidiBoxReloadGroups', () => {
  it('groups loaded slots with the same filament, vendor, and color', () => {
    const slots = qidiBoxSlots({
      save_variables: { variables: { box_count: 1 } },
      box_extras: {
        boxes: [{
          filament_info: [
            filamentInfo(0, 'PLA', '#228332'),
            filamentInfo(1, 'PLA', '#FF362D'),
            filamentInfo(2, 'PLA', '#228332'),
            filamentInfo(3, 'PLA', '#228332')
          ]
        }]
      },
      'box_stepper slot0': { runout_button: 0 },
      'box_stepper slot1': { runout_button: 0 },
      'box_stepper slot2': { runout_button: 0 },
      'box_stepper slot3': { runout_button: 1 }
    }, null)

    expect(qidiBoxReloadGroups(slots)).toStrictEqual({
      0: { letter: 'A', color: '#228332', members: [0, 2] },
      2: { letter: 'A', color: '#228332', members: [0, 2] }
    })
  })
})

describe('parseQidiFilamentDatabase', () => {
  it('lists firmware filaments with QF IDs', () => {
    expect(parseQidiFilamentDatabase({
      'QIDI PLA Basic': { type: 'PLA', filament_id: 'QFA00', filament_vendor: 'QIDI' },
      'Bambu PETG': { type: 'PETG', filament_id: 'QFL31', filament_vendor: 'Bambu' },
      Broken: { type: 'PLA', filament_id: 7 }
    })).toStrictEqual([
      { id: 'QFA00', name: 'QIDI PLA Basic', type: 'PLA', vendor: 'QIDI' },
      { id: 'QFL31', name: 'Bambu PETG', type: 'PETG', vendor: 'Bambu' }
    ])
  })
})

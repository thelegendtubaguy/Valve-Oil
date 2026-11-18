import { describe, it, expect } from 'vitest'
import {
  applyQidiFilamentImport,
  diffQidiFilamentImport,
  nextQidiFilamentId,
  parseQidiFilamentConfig,
  qidiFilamentBaseKey,
  qidiFilamentDraft,
  qidiFilamentRows,
  removeQidiFilament,
  serializeQidiFilamentConfig,
  setQidiFilament,
  type QidiFilamentConfig
} from '../qidi-filament-config'

// Entries as QIDI firmware 01.01.07.01 ships them, including its mix of numbers, strings, and lists.
const qidiEntry = (id: string, type: string, vendor: string, boxMax: string) => ({
  type,
  filament_id: id,
  nozzle_temperature: [190, 240],
  box_temperature_range_high: [boxMax],
  box_temperature_range_low: ['0'],
  filament_vendor: vendor,
  filament_is_support: '0',
  chamber_temperatures: '0',
  filament_dev_ams_drying_temperature: ['45', '45', '45', '45'],
  filament_dev_chamber_drying_time: ['12.0']
})

const config = (): QidiFilamentConfig => ({
  'QIDI PLA Basic': qidiEntry('QFA00', 'PLA', 'QIDI', '0'),
  'Generic PLA': qidiEntry('QFA90', 'PLA', 'Generic', '0'),
  'Generic PLA Silk': qidiEntry('QFA91', 'PLA', 'Generic', '0'),
  'Generic ABS': qidiEntry('QFB91', 'ABS', 'Generic', '45')
})

describe('parseQidiFilamentConfig', () => {
  it('reads a filament database and writes it back unchanged', () => {
    const text = serializeQidiFilamentConfig(config())

    expect(serializeQidiFilamentConfig(parseQidiFilamentConfig(text)!)).toBe(text)
  })

  it.each([
    ['invalid JSON', '{ "Generic PLA": '],
    ['a list', '[]'],
    ['an empty object', '{}'],
    ['an entry without a filament ID', '{ "Generic PLA": { "type": "PLA" } }']
  ])('rejects %s', (_, text) => {
    expect(parseQidiFilamentConfig(text)).toBeNull()
  })
})

describe('qidiFilamentRows', () => {
  it('splits the vendor from the display name and reads QIDI field shapes', () => {
    expect(qidiFilamentRows(config())[3]).toEqual({
      key: 'Generic ABS',
      vendor: 'Generic',
      name: 'ABS',
      id: 'QFB91',
      type: 'ABS',
      nozzleMin: 190,
      nozzleMax: 240,
      boxMax: 45,
      chamber: 0,
      support: false
    })
  })

  it('keeps the whole display name when it does not start with the vendor', () => {
    expect(qidiFilamentDraft('El Cheapo PLA', qidiEntry('QFA93', 'PLA', 'Ur Mom', '0')).name).toBe('El Cheapo PLA')
  })
})

describe('setQidiFilament', () => {
  it('edits a filament in place, keeping its field shapes and other settings', () => {
    const draft = { ...qidiFilamentDraft('Generic ABS', config()['Generic ABS']), name: 'ABS+', nozzleMax: 260, boxMax: 50, chamber: 55, support: true }
    const result = setQidiFilament(config(), draft, { previousKey: 'Generic ABS' })

    expect(Object.keys(result)).toEqual(['QIDI PLA Basic', 'Generic PLA', 'Generic PLA Silk', 'Generic ABS+'])
    expect(result['Generic ABS+']).toEqual({
      ...qidiEntry('QFB91', 'ABS', 'Generic', '50'),
      nozzle_temperature: [190, 260],
      filament_is_support: '1',
      chamber_temperatures: '55'
    })
  })

  it('adds a filament with the settings of the filament it is based on', () => {
    const current = config()
    const baseKey = qidiFilamentBaseKey(current, 'pla')
    const id = nextQidiFilamentId(current, current[baseKey!].filament_id as string)!
    const result = setQidiFilament(current, {
      vendor: 'Ur Mom',
      name: 'PLA',
      id,
      type: 'PLA',
      nozzleMin: 200,
      nozzleMax: 220,
      boxMax: 0,
      chamber: 0,
      support: false
    }, { baseKey })

    expect(baseKey).toBe('Generic PLA')
    expect(id).toBe('QFA92')
    expect(result['Ur Mom PLA']).toEqual({
      ...qidiEntry('QFA92', 'PLA', 'Ur Mom', '0'),
      nozzle_temperature: [200, 220]
    })
    expect(current['Generic PLA'].filament_vendor).toBe('Generic')
  })

  it.each([
    ['a display name already listed', { vendor: 'Generic', name: 'PLA', id: 'QFA99' }],
    ['an ID another filament uses', { vendor: 'Generic', name: 'PLA Matte', id: 'QFA90' }]
  ])('refuses %s', (_, change) => {
    const draft = { ...qidiFilamentDraft('Generic ABS', config()['Generic ABS']), ...change }

    expect(() => setQidiFilament(config(), draft, { previousKey: 'Generic ABS' })).toThrow()
  })

  it('removes a filament', () => {
    expect(Object.keys(removeQidiFilament(config(), 'Generic PLA'))).toEqual(['QIDI PLA Basic', 'Generic PLA Silk', 'Generic ABS'])
  })
})

describe('filament import', () => {
  it('offers new and changed filaments, and refuses one whose ID is taken', () => {
    const current = config()
    const imported = {
      ...config(),
      'Generic ABS': { ...current['Generic ABS'], nozzle_temperature: [240, 280] },
      'Ur Mom PLA': qidiEntry('QFA92', 'PLA', 'Ur Mom', '0'),
      'Ur Mom PETG': qidiEntry('QFA00', 'PETG', 'Ur Mom', '45')
    }

    expect(diffQidiFilamentImport(current, imported)).toEqual([
      { key: 'Generic ABS', id: 'QFB91', status: 'changed' },
      { key: 'Ur Mom PLA', id: 'QFA92', status: 'new' },
      { key: 'Ur Mom PETG', id: 'QFA00', status: 'conflict', conflictKey: 'QIDI PLA Basic' }
    ])

    const result = applyQidiFilamentImport(current, imported, ['Ur Mom PLA', 'Ur Mom PETG'])

    expect(Object.keys(result)).toEqual([...Object.keys(current), 'Ur Mom PLA'])
    expect(result['Generic ABS']).toEqual(current['Generic ABS'])
  })
})

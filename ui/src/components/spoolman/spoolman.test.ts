import { describe, expect, it } from 'vitest'
import { applySocketMessage, isLoopback, selectableSpools, slotSpools, spoolChecks, spoolmanSocketUrl, spoolmanUrl, toSpool, unwrapProxy, type RawSpool } from './spoolman'

const vendor = { id: 1, registered: '2025-01-01T00:00:00Z', name: 'QIDI', empty_spool_weight: 180 }
const filament = { id: 1, registered: '2025-01-01T00:00:00Z', density: 1.24, diameter: 1.75, name: 'PLA Basic', material: 'PLA', vendor, weight: 1000, color_hex: '1d1d1f' }
const raw = (over: Partial<RawSpool> = {}): RawSpool => ({ id: 1, registered: '2025-01-02T00:00:00Z', filament, used_weight: 250, remaining_weight: 750, archived: false, ...over })

describe('toSpool', () => {
  it('derives the name, colors, lengths, and how full the spool is as Fluidd does', () => {
    const s = toSpool(raw())
    expect(s.filament_name).toBe('QIDI - PLA Basic')
    expect(s.filament.colors).toEqual(['#1d1d1f'])
    expect(s.initial_weight).toBe(1000)
    expect(s.spool_weight).toBe(180)
    expect(s.progress).toBe(75)
    // 1 kg of 1.75 mm PLA is about 335 m.
    expect(s.initial_length! / 1000).toBeCloseTo(335.3, 0)
    expect(s.remaining_length! / s.initial_length!).toBeCloseTo(0.75)
  })

  it('prefers multi-color hexes and falls back to the ID for a nameless filament', () => {
    const s = toSpool(raw({ id: 7, filament: { ...filament, name: undefined, vendor: undefined, multi_color_hexes: 'ff0000,00ff00' } }))
    expect(s.filament_name).toBe('7')
    expect(s.filament.colors).toEqual(['#ff0000', '#00ff00'])
  })
})

describe('unwrapProxy', () => {
  it('reads v2 responses, v2 errors, and bare v1 responses', () => {
    expect(unwrapProxy({ response: [1], error: null })).toEqual({ response: [1], error: null })
    expect(unwrapProxy({ response: null, error: { message: 'Spoolman unreachable' } })).toEqual({ response: null, error: 'Spoolman unreachable' })
    expect(unwrapProxy([1])).toEqual({ response: [1], error: null })
  })
})

describe("Spoolman's address", () => {
  it('replaces a loopback Spoolman host with the printer address', () => {
    expect(spoolmanUrl('127.0.0.1:7912', 'http://192.168.1.50')).toBe('http://192.168.1.50:7912/')
    expect(spoolmanUrl('http://localhost:7912', 'http://localhost:7125')).toBe('http://localhost:7912/')
    expect(spoolmanUrl('https://spoolman.lan/', 'http://192.168.1.50')).toBe('https://spoolman.lan/')
    expect(spoolmanUrl(undefined, 'http://192.168.1.50')).toBeUndefined()
  })

  it('knows loopback addresses', () => {
    for (const h of ['localhost', '127.0.0.1', '127.1.2.3', '[::1]', '::ffff:127.0.0.1', '::ffff:7f00:1']) expect(isLoopback(h), h).toBe(true)
    for (const h of ['192.168.1.50', 'spoolman.lan', '::ffff:c0a8:132', '128.0.0.1']) expect(isLoopback(h), h).toBe(false)
  })

  it("opens Spoolman's websocket under its API path", () => {
    expect(spoolmanSocketUrl('http://192.168.1.50:7912/')).toBe('ws://192.168.1.50:7912/api/v1/')
    expect(spoolmanSocketUrl('https://host/spoolman')).toBe('wss://host/spoolman/api/v1/')
  })
})

describe('applySocketMessage', () => {
  const list = [raw({ id: 1 }), raw({ id: 2 })]
  it('adds, updates, and deletes spools', () => {
    expect(applySocketMessage(list, { type: 'added', resource: 'spool', payload: raw({ id: 3 }) }).map((s) => s.id)).toEqual([1, 2, 3])
    expect(applySocketMessage(list, { type: 'updated', resource: 'spool', payload: raw({ id: 2, used_weight: 300 }) })[1].used_weight).toBe(300)
    expect(applySocketMessage(list, { type: 'deleted', resource: 'spool', payload: raw({ id: 1 }) }).map((s) => s.id)).toEqual([2])
  })
  it('carries filament and vendor edits onto their spools', () => {
    const f = applySocketMessage(list, { type: 'updated', resource: 'filament', payload: { ...filament, name: 'PLA Matte' } })
    expect(f.every((s) => s.filament.name === 'PLA Matte')).toBe(true)
    const v = applySocketMessage(list, { type: 'updated', resource: 'vendor', payload: { ...vendor, name: 'Qidi Tech' } })
    expect(v[0].filament.vendor?.name).toBe('Qidi Tech')
  })
})

describe('selectableSpools', () => {
  const spools = [
    raw({ id: 1, last_used: '2025-03-01T00:00:00Z', location: 'Shelf' }),
    raw({ id: 2, filament: { ...filament, id: 2, material: 'PETG', name: 'Tough' }, last_used: '2025-02-01T00:00:00Z' }),
    raw({ id: 3, comment: 'drying' }),
    raw({ id: 4, archived: true }),
  ].map(toSpool)

  it('hides archived spools and searches the ID, comment, name, material, and vendor', () => {
    expect(selectableSpools(spools, '', null, { key: 'id', desc: false }).map((s) => s.id)).toEqual([1, 2, 3])
    expect(selectableSpools(spools, 'petg', null, { key: 'id', desc: false }).map((s) => s.id)).toEqual([2])
    expect(selectableSpools(spools, 'DRY', null, { key: 'id', desc: false }).map((s) => s.id)).toEqual([3])
    expect(selectableSpools(spools, '', 'PLA', { key: 'id', desc: false }).map((s) => s.id)).toEqual([1, 3])
  })

  it('sorts by a Fluidd column key either way, with missing values last', () => {
    expect(selectableSpools(spools, '', null, { key: 'last_used', desc: false }).map((s) => s.id)).toEqual([2, 1, 3])
    expect(selectableSpools(spools, '', null, { key: 'last_used', desc: true }).map((s) => s.id)).toEqual([1, 2, 3])
    expect(selectableSpools(spools, '', null, { key: 'filament.material', desc: true }).map((s) => s.id)).toEqual([1, 3, 2])
  })
})

describe('spoolChecks', () => {
  const spool = toSpool(raw())
  const warn = { mismatch: true, notEnough: true }
  it('warns of a material mismatch and too little filament left for the rest of the print', () => {
    expect(spoolChecks(spool, { state: 'printing', filamentUsed: 0, filamentTotal: 10000, filamentTypes: ['PETG'] }, warn)).toEqual(['mismatched_filament'])
    expect(spoolChecks(spool, { state: 'printing', filamentUsed: 0, filamentTotal: 400000, filamentTypes: ['PLA'] }, warn)).toEqual(['no_filament'])
    // What's already printed doesn't count.
    expect(spoolChecks(spool, { state: 'printing', filamentUsed: 200000, filamentTotal: 400000, filamentTypes: ['PLA'] }, warn)).toEqual([])
  })
  it('asks when the file says nothing of its length, and not once the print ended', () => {
    expect(spoolChecks(spool, { state: 'standby', filamentUsed: 0, filamentTotal: undefined, filamentTypes: [] }, warn)).toEqual(['no_required_length'])
    expect(spoolChecks(spool, { state: 'complete', filamentUsed: 0, filamentTotal: 400000, filamentTypes: ['PETG'] }, warn)).toEqual([])
    expect(spoolChecks(spool, { state: 'printing', filamentUsed: 0, filamentTotal: 400000, filamentTypes: ['PETG'] }, { mismatch: false, notEnough: false })).toEqual([])
  })
})

describe('slotSpools', () => {
  it("reads vo_spoolman's spools and loaded slot, ignoring anything malformed", () => {
    const s = slotSpools({ loaded_slot: 'slot1', spools: { slot5: 12, slot0: 1, slot16: 3, slot17: 4, slotx: 5, slot2: 0 } })!
    expect(s.loadedSlot).toBe(1)
    expect([...s.spools]).toEqual([
      [0, 1],
      [5, 12],
      [16, 3],
    ])
    expect(slotSpools({ loaded_slot: null, spools: {} })).toEqual({ loadedSlot: null, spools: new Map() })
    expect(slotSpools(undefined)).toBeNull()
  })
})

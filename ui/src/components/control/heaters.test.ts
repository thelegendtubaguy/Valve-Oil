import { describe, it, expect } from 'vitest'
import { targetError, targetRange } from './heaters'

describe('targetRange', () => {
  it('keeps the nozzle within max_temp when QIDI reports a looser target_max_temp', () => {
    expect(targetRange({ min_temp: 0, max_temp: 375, target_min_temp: 0, target_max_temp: 999 })).toEqual({ min: 0, max: 375, locked: false })
  })

  it('limits a Box heater to target_max_temp', () => {
    expect(targetRange({ min_temp: -100, max_temp: 100, target_min_temp: 0, target_max_temp: 65 }).max).toBe(65)
  })

  it('narrows a Box heater to its loaded filament limit', () => {
    expect(targetRange({ max_temp: 100, target_max_temp: 65 }, 50).max).toBe(50)
  })

  it('locks a Box heater whose filament forbids heating, unless it is already heating', () => {
    expect(targetRange({ max_temp: 100, target_max_temp: 65 }, 0).locked).toBe(true)
    expect(targetRange({ max_temp: 100, target_max_temp: 65 }, 0, 40).locked).toBe(false)
  })

  it('raises the minimum to target_min_temp', () => {
    expect(targetRange({ min_temp: 0, max_temp: 300, target_min_temp: 40 }).min).toBe(40)
  })
})

describe('targetError', () => {
  const range = { min: 40, max: 65, locked: false }
  it('always allows 0 to turn the heater off', () => {
    expect(targetError(0, range)).toBeNull()
    expect(targetError(0, { ...range, locked: true })).toBeNull()
  })
  it('refuses targets outside the range', () => {
    expect(targetError(30, range)).not.toBeNull()
    expect(targetError(70, range)).not.toBeNull()
    expect(targetError(50, range)).toBeNull()
  })
  it('refuses any target while locked', () => {
    expect(targetError(50, { ...range, locked: true })).not.toBeNull()
  })
})

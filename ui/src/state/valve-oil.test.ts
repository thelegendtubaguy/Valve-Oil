import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { Status } from './printer'

const UPDATING_KEY = 'vo-status.updating-since'

function fakeStorage(entries: Record<string, string> = {}) {
  const values = new Map(Object.entries(entries))
  return {
    values,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, String(value)),
    removeItem: (key: string) => void values.delete(key),
  }
}

/**
 * A page that saw an update start, reloaded or left open while the status service stopped answering, with Klipper
 * as `klipper` describes it.
 */
async function pageDuringUpdate(klipper: { klippy: 'ready' | 'startup'; warnings?: readonly string[] }) {
  vi.resetModules()
  const session = fakeStorage({ [UPDATING_KEY]: String(Date.now() - 60_000) })
  vi.stubGlobal('location', new URL('http://printer.test/valve-oil/'))
  vi.stubGlobal('localStorage', fakeStorage())
  vi.stubGlobal('sessionStorage', session)
  vi.stubGlobal('document', { hidden: false, addEventListener: () => {} })
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
  const { printer } = await import('./printer')
  const { valveOil, startValveOilStatus, updateRunning, KLIPPER_QUIET_MS } = await import('./valve-oil')
  const warnings = klipper.warnings?.map((id) => ({ id, level: 'error', title: id, message: '' }))
  const status: Status = warnings ? { vo_printer_warnings: { warnings } } : {}
  printer.set((s) => ({ ...s, conn: 'open', klippy: klipper.klippy, status }))
  startValveOilStatus()
  await vi.advanceTimersByTimeAsync(0)
  return { running: () => updateRunning(valveOil.get()), valveOil, session, quiet: KLIPPER_QUIET_MS }
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('stops saying the printer is updating once Klipper has been ready a while without an update running', async () => {
  const page = await pageDuringUpdate({ klippy: 'ready', warnings: ['valve_oil_update_interrupted', 'valve_oil_release_waiting'] })
  expect(page.running()).toBe(true)

  // Update now's installer may not have recorded its run yet.
  await vi.advanceTimersByTimeAsync(page.quiet / 2)
  expect(page.running()).toBe(true)

  await vi.advanceTimersByTimeAsync(page.quiet)
  expect(page.running()).toBe(false)
  expect(page.valveOil.get().unavailable).toBe(true)
  // A reload doesn't bring it back.
  expect(page.session.values.has(UPDATING_KEY)).toBe(false)
})

it.each([
  ['Klipper says an update is running', { klippy: 'ready', warnings: ['valve_oil_update_running'] }],
  ['Klipper is restarting', { klippy: 'startup' }],
  ['Klipper has no Valve Oil warnings to say', { klippy: 'ready' }],
] as const)('keeps saying the printer is updating while %s', async (_, klipper) => {
  const page = await pageDuringUpdate(klipper)
  await vi.advanceTimersByTimeAsync(page.quiet * 5)
  expect(page.running()).toBe(true)
  expect(page.session.values.has(UPDATING_KEY)).toBe(true)
})

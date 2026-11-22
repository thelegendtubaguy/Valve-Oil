import { afterEach, expect, it, vi } from 'vitest'

function fakeStorage(entries: Record<string, string>) {
  const values = new Map(Object.entries(entries))
  return {
    values,
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => void values.set(key, String(value)),
    removeItem: (key: string) => void values.delete(key),
  }
}

async function load(local: ReturnType<typeof fakeStorage>, session: ReturnType<typeof fakeStorage>) {
  vi.resetModules()
  vi.stubGlobal('location', new URL('http://printer.test/valve-oil/'))
  vi.stubGlobal('localStorage', local)
  vi.stubGlobal('sessionStorage', session)
  const { valveOil, dismissUpdate } = await import('./valve-oil')
  const { addons, dismissJob } = await import('./addons')
  return { valveOil, dismissUpdate, addons, dismissJob }
}

afterEach(() => vi.unstubAllGlobals())

it('keeps closed update results, closed add-on jobs, and a running update saved under the old tltg- keys', async () => {
  const local = fakeStorage({
    'tltg-status.dismissed-updates': '["update-1"]',
    'tltg-addons.dismissed-jobs': '["job-1"]',
  })
  const session = fakeStorage({ 'tltg-status.updating-since': String(Date.now() - 60_000) })
  const { valveOil, dismissUpdate, addons, dismissJob } = await load(local, session)

  expect(valveOil.get().dismissed).toEqual(['update-1'])
  expect(valveOil.get().expectUpdate).toBe(true)
  expect(addons.get().dismissedJobs).toEqual(['job-1'])
  expect([...local.values.keys()].sort()).toEqual(['vo-addons.dismissed-jobs', 'vo-status.dismissed-updates'])
  expect([...session.values.keys()]).toEqual(['vo-status.updating-since'])

  dismissUpdate('update-2')
  dismissJob('job-2')
  expect(JSON.parse(local.values.get('vo-status.dismissed-updates')!)).toEqual(['update-1', 'update-2'])
  expect(JSON.parse(local.values.get('vo-addons.dismissed-jobs')!)).toEqual(['job-1', 'job-2'])
})

it('reads the new keys over anything left under the old ones', async () => {
  const local = fakeStorage({
    'vo-status.dismissed-updates': '["new"]',
    'tltg-status.dismissed-updates': '["old"]',
    'vo-addons.dismissed-jobs': '[]',
    'tltg-addons.dismissed-jobs': '["old"]',
  })
  const { valveOil, addons } = await load(local, fakeStorage({}))

  expect(valveOil.get().dismissed).toEqual(['new'])
  expect(valveOil.get().expectUpdate).toBe(false)
  expect(addons.get().dismissedJobs).toEqual([])
})

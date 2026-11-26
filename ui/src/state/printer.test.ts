import { afterEach, beforeEach, expect, it, vi } from 'vitest'

// Each RPC waits for an explicit answer so tests can interleave reconnects, notifications and slow history.
type Call = { method: string; resolve: (v: unknown) => void; reject: (e: Error) => void }
const fake = vi.hoisted(() => ({
  calls: [] as Call[],
  handlers: new Map<string, (params: unknown[]) => void>(),
  onState: null as ((s: string) => void) | null,
}))

vi.mock('../moonraker/client', () => ({
  resolveMoonrakerOrigin: () => 'http://printer.test',
  MoonrakerClient: class {
    call(method: string) {
      return new Promise((resolve, reject) => fake.calls.push({ method, resolve, reject }))
    }
    on(method: string, fn: (params: unknown[]) => void) {
      fake.handlers.set(method, fn)
    }
    onState(fn: (s: string) => void) {
      fake.onState = fn
    }
    connect() {}
  },
}))

const tick = () => vi.advanceTimersByTimeAsync(0)
function take(method: string) {
  const i = fake.calls.findIndex((c) => c.method === method)
  if (i < 0) throw new Error(`no pending ${method}; pending: ${fake.calls.map((c) => c.method).join(', ')}`)
  return fake.calls.splice(i, 1)[0]
}
async function answer(method: string, value: unknown) {
  take(method).resolve(value)
  await tick()
}
const READY_INFO = { klippy_state: 'ready', components: [], registered_directories: [], warnings: [] }
const SETTINGS = { status: { gcode: { commands: { M104: {} } }, configfile: { settings: { extruder: { max_temp: 280 } } } } }

async function load() {
  vi.resetModules()
  fake.calls.length = 0
  fake.handlers.clear()
  vi.stubGlobal('__APP_VERSION__', 'test')
  vi.stubGlobal('requestAnimationFrame', (fn: () => void) => setTimeout(fn, 0))
  const mod = await import('./printer')
  mod.startPrinterConnection()
  fake.onState?.('open')
  return mod
}
async function subscribe() {
  await answer('server.info', READY_INFO)
  await answer('printer.objects.list', { objects: ['idle_timeout', 'extruder'] })
  await answer('printer.objects.subscribe', { status: { idle_timeout: { state: 'Printing' }, extruder: { temperature: 22, target: 0 } } })
}
async function finishLoad() {
  await answer('printer.objects.query', SETTINGS)
  await answer('server.temperature_store', {})
}
beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('opens with safety settings without waiting for identification or temperature history', async () => {
  const { printer, onKlippyReady } = await load()
  const ready = vi.fn()
  onKlippyReady(ready)
  expect(printer.get().klippy).toBe('unknown')
  await subscribe()
  expect(printer.get().klippy).toBe('unknown')
  await answer('printer.objects.query', SETTINGS)
  expect(printer.get().klippy).toBe('ready')
  expect(printer.get().heaterSettings.extruder.max_temp).toBe(280)
  expect(printer.get().commands).toContain('M104')
  expect(ready).toHaveBeenCalledOnce()
  expect(fake.calls.map((c) => c.method)).toEqual(expect.arrayContaining(['server.connection.identify', 'server.temperature_store']))
  expect(fake.calls.some((c) => c.method === 'server.gcode_store')).toBe(false)
})

it('waits for the status snapshot even when configuration arrives first', async () => {
  const { printer } = await load()
  await answer('server.info', READY_INFO)
  await answer('printer.objects.list', { objects: ['idle_timeout'] })
  await answer('printer.objects.query', SETTINGS)
  expect(printer.get().klippy).toBe('unknown')
  await answer('printer.objects.subscribe', { status: { idle_timeout: { state: 'Ready' } } })
  expect(printer.get().klippy).toBe('ready')
})

it('keeps a status change that arrives while a Klipper restart is still loading', async () => {
  const { printer } = await load()
  await subscribe()
  fake.handlers.get('notify_status_update')?.([{ idle_timeout: { state: 'Ready' } }])
  await tick()
  await finishLoad()
  expect(printer.get().klippy).toBe('ready')
  expect(printer.get().status.idle_timeout?.state).toBe('Ready')
})

it('keeps the newest load when requests from before a restart finish last', async () => {
  const { printer } = await load()
  await answer('server.info', READY_INFO)
  await answer('printer.objects.list', { objects: ['idle_timeout'] })
  const oldStatus = take('printer.objects.subscribe')
  const oldSettings = take('printer.objects.query')
  fake.handlers.get('notify_klippy_ready')?.([])
  await subscribe()
  await finishLoad()
  oldStatus.resolve({ status: { idle_timeout: { state: 'Old' } } })
  oldSettings.resolve({ status: {} })
  await tick()
  expect(printer.get().status.idle_timeout?.state).toBe('Printing')
  expect(printer.get().heaterSettings.extruder.max_temp).toBe(280)
})

it('does not let bootstrap from before shutdown attach a stale status subscription', async () => {
  const { printer } = await load()
  const oldInfo = take('server.info')
  fake.handlers.get('notify_klippy_shutdown')?.([])
  const shutdownInfo = take('server.info')
  oldInfo.resolve(READY_INFO)
  await tick()
  expect(fake.calls.some((call) => call.method === 'printer.objects.list')).toBe(false)

  shutdownInfo.resolve({ ...READY_INFO, klippy_state: 'shutdown' })
  await tick()
  await answer('printer.info', { state_message: 'Klipper shutdown' })
  expect(printer.get().klippy).toBe('shutdown')
  expect(printer.get().klippyMessage).toBe('Klipper shutdown')
  expect(fake.calls.some((call) => call.method === 'printer.objects.subscribe')).toBe(false)
})

it.each(['closed', 'disconnected', 'shutdown'])('discards delayed optional state after %s', async (state) => {
  const { printer } = await load()
  await subscribe()
  await answer('printer.objects.query', SETTINGS)
  if (state === 'closed') fake.onState?.('closed')
  else fake.handlers.get(`notify_klippy_${state}`)?.([])
  await answer('server.temperature_store', { extruder: { temperatures: [999] } })
  expect(printer.get().klippy).toBe(state === 'closed' ? 'unknown' : state)
  expect(printer.get().temps.extruder.temperatures).toEqual([])
  expect(printer.get().console).toEqual([])
})

it('takes delayed temperature history in place of the live samples it already holds', async () => {
  const { printer, pushConsole } = await load()
  await subscribe()
  await answer('printer.objects.query', SETTINGS)
  pushConsole('live reply', 'response')
  await vi.advanceTimersByTimeAsync(3000)
  expect(printer.get().temps.extruder.temperatures).toEqual([22, 22, 22])
  // Moonraker sampled the same three seconds; joining them would chart those seconds twice.
  await answer('server.temperature_store', { extruder: { temperatures: [20, 21, 22, 22, 22], targets: [0, 0, 0, 0, 0] } })
  expect(printer.get().temps.extruder).toEqual({ temperatures: [20, 21, 22, 22, 22], targets: [0, 0, 0, 0, 0] })
  await vi.advanceTimersByTimeAsync(1000)
  expect(printer.get().temps.extruder).toEqual({ temperatures: [20, 21, 22, 22, 22, 22], targets: [0, 0, 0, 0, 0, 0], end: 6 })
  expect(printer.get().console.map((l) => [l.text, l.origin])).toEqual([['live reply', 'live']])
})

it('projects canonical console history for Box diagnostics', async () => {
  const { printer, replaceConsoleHistory } = await load()
  replaceConsoleHistory([
    { message: 'G28', time: 1, type: 'command' },
    { message: '!! Box failed', time: 2, type: 'response' },
  ])
  expect(printer.get().console.map((line) => [line.text, line.kind, line.origin])).toEqual([
    ['G28', 'command', 'history'],
    ['!! Box failed', 'error', 'history'],
  ])
})

it.each(['error', 'shutdown', 'disconnected', 'startup'])('shows confirmed %s before the detail request finishes', async (state) => {
  const { printer } = await load()
  await answer('server.info', { ...READY_INFO, klippy_state: state })
  expect(printer.get().klippy).toBe(state)
  await answer('printer.info', { state_message: 'Klipper detail' })
  expect(printer.get().klippyMessage).toBe('Klipper detail')
})

it.each(['server.info', 'printer.objects.list', 'printer.objects.subscribe'])('retries a failed required %s without claiming readiness', async (method) => {
  const { printer } = await load()
  if (method !== 'server.info') await answer('server.info', READY_INFO)
  if (method !== 'server.info' && method !== 'printer.objects.list') await answer('printer.objects.list', { objects: [] })
  take(method).reject(new Error('request failed'))
  await tick()
  expect(printer.get().klippy).toBe('unknown')
  expect(printer.get().klippyMessage).toBe('request failed')
  await vi.advanceTimersByTimeAsync(2000)
  expect(fake.calls.some((c) => c.method === 'server.info')).toBe(true)
})

it('goes ready without Klipper settings that fail to load, and loads them in the background', async () => {
  const { printer } = await load()
  await subscribe()
  take('printer.objects.query').reject(new Error('timed out'))
  await tick()
  expect(printer.get().klippy).toBe('ready')
  expect(printer.get().heaterSettings).toEqual({})
  await vi.advanceTimersByTimeAsync(2000)
  take('printer.objects.query').reject(new Error('timed out'))
  await vi.advanceTimersByTimeAsync(2000)
  await answer('printer.objects.query', SETTINGS)
  expect(printer.get().klippy).toBe('ready')
  expect(printer.get().commands).toEqual(['M104'])
  expect(printer.get().heaterSettings).toEqual({ extruder: { max_temp: 280 } })
  expect(fake.calls.some((c) => c.method === 'server.info')).toBe(false)
})

it('stops loading Klipper settings for a Klipper session that has ended', async () => {
  const { printer } = await load()
  await subscribe()
  take('printer.objects.query').reject(new Error('timed out'))
  await tick()
  fake.onState?.('closed')
  await vi.advanceTimersByTimeAsync(5000)
  expect(fake.calls.some((c) => c.method === 'printer.objects.query')).toBe(false)
  expect(printer.get().settings).toEqual({})
})

it('does not retry or accept an initialization response after the socket closes', async () => {
  const { printer } = await load()
  fake.onState?.('closed')
  await answer('server.info', READY_INFO)
  await vi.advanceTimersByTimeAsync(3000)
  expect(printer.get().klippy).toBe('unknown')
  expect(fake.calls.map((c) => c.method)).toEqual(['server.connection.identify'])
})

it('schedules only one retry when the Klipper error detail request fails', async () => {
  const { printer } = await load()
  await answer('server.info', { ...READY_INFO, klippy_state: 'error' })
  take('printer.info').reject(new Error('detail unavailable'))
  await tick()
  await vi.advanceTimersByTimeAsync(2000)
  expect(printer.get().klippy).toBe('error')
  expect(fake.calls.filter((c) => c.method === 'server.info')).toHaveLength(1)
})

it("starts a new page from the state the last one saw, until Klipper's own arrives", async () => {
  const stored = new Map<string, string>()
  vi.stubGlobal('localStorage', { getItem: (k: string) => stored.get(k) ?? null, setItem: (k: string, v: string) => void stored.set(k, v) })
  await load()
  await subscribe()
  await finishLoad()
  // The next page: Klipper not heard from yet, so it shows what the last page saw.
  const { printer } = await load()
  expect(printer.get().klippy).toBe('unknown')
  expect(printer.get().status.idle_timeout?.state).toBe('Printing')
  expect(printer.get().commands).toContain('M104')
  await answer('server.info', READY_INFO)
  await answer('printer.objects.list', { objects: ['idle_timeout'] })
  await answer('printer.objects.subscribe', { status: { idle_timeout: { state: 'Ready' } } })
  expect(printer.get().status.idle_timeout?.state).toBe('Ready')
})

it('holds requests that can wait until Klipper is ready', async () => {
  const { afterKlipperAnswers } = await load()
  const waited = vi.fn()
  void afterKlipperAnswers()?.then(waited)
  await subscribe()
  expect(waited).not.toHaveBeenCalled()
  await answer('printer.objects.query', SETTINGS)
  expect(waited).toHaveBeenCalledOnce()
  expect(afterKlipperAnswers()).toBeNull()
})

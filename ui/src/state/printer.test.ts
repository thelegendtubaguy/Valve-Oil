import { beforeEach, expect, it, vi } from 'vitest'

// A fake Moonraker connection: each call waits until the test answers it, so the test can deliver status updates
// between a reconnect's requests, as a restarting Klipper does.
type Call = { method: string; resolve: (v: unknown) => void }
const fake = vi.hoisted(() => ({
  calls: [] as { method: string; resolve: (v: unknown) => void }[],
  handlers: new Map<string, (params: unknown[]) => void>(),
  onState: null as ((s: string) => void) | null,
}))

vi.mock('../moonraker/client', () => ({
  resolveMoonrakerOrigin: () => 'http://printer.test',
  MoonrakerClient: class {
    call(method: string) {
      return new Promise((resolve) => fake.calls.push({ method, resolve }))
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

const tick = () => new Promise((r) => setTimeout(r, 0))

async function answer(method: string, value: unknown): Promise<void> {
  await tick()
  const i = fake.calls.findIndex((c: Call) => c.method === method)
  if (i < 0) throw new Error(`no pending ${method}; pending: ${fake.calls.map((c: Call) => c.method).join(', ')}`)
  fake.calls.splice(i, 1)[0].resolve(value)
  await tick()
}

const READY_INFO = { klippy_state: 'ready', components: [], registered_directories: [], warnings: [] }

async function load() {
  vi.resetModules()
  fake.calls.length = 0
  fake.handlers.clear()
  vi.stubGlobal('__APP_VERSION__', 'test')
  // Status updates apply once per animation frame; run the frame straight away.
  vi.stubGlobal('requestAnimationFrame', (fn: () => void) => setTimeout(fn, 0))
  const mod = await import('./printer')
  mod.startPrinterConnection()
  return mod
}

async function finishLoad() {
  await answer('server.temperature_store', {})
  await answer('printer.objects.query', { status: {} })
  await answer('server.gcode_store', { gcode_store: [] })
}

beforeEach(() => vi.unstubAllGlobals())

it('keeps a status change that arrives while a Klipper restart is still loading', async () => {
  const { printer } = await load()
  fake.onState?.('open')
  await answer('server.connection.identify', {})
  await answer('server.info', READY_INFO)
  await answer('printer.objects.list', { objects: ['idle_timeout'] })
  // Klipper is still running its start-up commands when the UI subscribes...
  await answer('printer.objects.subscribe', { status: { idle_timeout: { state: 'Printing' } } })
  // ...and finishes them before the rest of the load comes back. Moonraker won't send this field again.
  fake.handlers.get('notify_status_update')?.([{ idle_timeout: { state: 'Ready' } }])
  await tick()
  await finishLoad()
  expect(printer.get().klippy).toBe('ready')
  expect(printer.get().status.idle_timeout?.state).toBe('Ready')
})

it('keeps the newest load when an older one, started before a restart, finishes after it', async () => {
  const { printer } = await load()
  fake.onState?.('open')
  await answer('server.connection.identify', {})
  await answer('server.info', READY_INFO)
  await answer('printer.objects.list', { objects: ['idle_timeout'] })
  // The first load is still waiting on its subscribe when Klipper reports ready again and a second load starts.
  const first = fake.calls.find((c: Call) => c.method === 'printer.objects.subscribe')!
  fake.calls.splice(fake.calls.indexOf(first), 1)
  fake.handlers.get('notify_klippy_ready')?.([])
  await answer('server.info', READY_INFO)
  await answer('printer.objects.list', { objects: ['idle_timeout'] })
  await answer('printer.objects.subscribe', { status: { idle_timeout: { state: 'Ready' } } })
  await finishLoad()
  // The first load's subscribe answers last, with what Klipper said before.
  first.resolve({ status: { idle_timeout: { state: 'Printing' } } })
  await tick()
  // Answer whatever the older load still asks for.
  for (const method of ['server.temperature_store', 'printer.objects.query', 'server.gcode_store']) {
    if (fake.calls.some((c: Call) => c.method === method)) await answer(method, method === 'server.gcode_store' ? { gcode_store: [] } : method === 'printer.objects.query' ? { status: {} } : {})
  }
  expect(printer.get().status.idle_timeout?.state).toBe('Ready')
})

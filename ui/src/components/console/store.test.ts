import { afterEach, beforeEach, expect, it, vi } from 'vitest'

type Handler = (params: unknown[]) => void
type StateHandler = (state: 'connecting' | 'open' | 'closed') => void
type ConsoleLine = { id: number; time: number; text: string; kind: 'command' | 'response' | 'error'; origin: 'live' | 'history' }
type PrinterState = { console: ConsoleLine[]; klippy: string; commands: string[] }
type PendingCall = { method: string; resolve: (value: unknown) => void; reject: (error: Error) => void }

const fake = vi.hoisted(() => ({
  state: 'closed' as 'connecting' | 'open' | 'closed',
  calls: [] as PendingCall[],
  handlers: new Map<string, Set<Handler>>(),
  stateHandlers: new Set<StateHandler>(),
  printer: { console: [], klippy: 'unknown', commands: [] } as PrinterState,
  printerSubscribers: new Set<() => void>(),
  projected: [] as { message: string; time: number; type: 'command' | 'response' }[][],
  lineId: 0,
  clockOffset: 0,
  klipperAnswer: null as Promise<void> | null,
}))

vi.mock('../../state/printer', () => ({
  client: {
    get state() {
      return fake.state
    },
    call(method: string) {
      return new Promise((resolve, reject) => fake.calls.push({ method, resolve, reject }))
    },
    on(method: string, handler: Handler) {
      const handlers = fake.handlers.get(method) ?? new Set<Handler>()
      handlers.add(handler)
      fake.handlers.set(method, handlers)
      return () => handlers.delete(handler)
    },
    onState(handler: StateHandler) {
      fake.stateHandlers.add(handler)
      return () => fake.stateHandlers.delete(handler)
    },
    gcode: vi.fn(),
  },
  hostClockOffset: () => fake.clockOffset,
  // The history waits for Klipper's first answer; most tests start with it given.
  afterKlipperAnswers: () => fake.klipperAnswer,
  printer: {
    get: () => fake.printer,
    set: (next: PrinterState | ((state: PrinterState) => PrinterState)) => {
      fake.printer = typeof next === 'function' ? next(fake.printer) : next
      for (const subscriber of fake.printerSubscribers) subscriber()
    },
    subscribe: (subscriber: () => void) => {
      fake.printerSubscribers.add(subscriber)
      return () => fake.printerSubscribers.delete(subscriber)
    },
  },
  pushConsole: (text: string, kind: ConsoleLine['kind']) => {
    fake.printer = { ...fake.printer, console: [...fake.printer.console, { id: ++fake.lineId, time: Date.now(), text, kind, origin: 'live' }] }
    for (const subscriber of fake.printerSubscribers) subscriber()
  },
  replaceConsoleHistory: (entries: { message: string; time: number; type: 'command' | 'response' }[]) => {
    fake.projected.push(entries)
  },
  runGcode: vi.fn(),
  useConnected: vi.fn(),
  usePrinter: vi.fn(),
}))

vi.mock('../../state/database', () => ({ FLUIDD: 'fluidd', useDbItem: vi.fn() }))

function emit(method: string, params: unknown[]) {
  for (const handler of fake.handlers.get(method) ?? []) handler(params)
}

function setConnection(state: 'connecting' | 'open' | 'closed') {
  fake.state = state
  for (const handler of fake.stateHandlers) handler(state)
}

function pushPrinterLine(text: string, kind: ConsoleLine['kind']) {
  fake.printer = { ...fake.printer, console: [...fake.printer.console, { id: ++fake.lineId, time: Date.now(), text, kind, origin: 'live' }] }
  for (const subscriber of fake.printerSubscribers) subscriber()
}

function take(method: string) {
  const index = fake.calls.findIndex((call) => call.method === method)
  if (index < 0) throw new Error(`No pending ${method}`)
  return fake.calls.splice(index, 1)[0]
}

async function settle() {
  await Promise.resolve()
  await Promise.resolve()
}

async function load() {
  vi.resetModules()
  return import('./store')
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-01-01T00:00:00Z'))
  fake.state = 'closed'
  fake.calls.length = 0
  fake.handlers.clear()
  fake.stateHandlers.clear()
  fake.printer = { console: [], klippy: 'unknown', commands: [] }
  fake.printerSubscribers.clear()
  fake.projected.length = 0
  fake.lineId = 0
  fake.clockOffset = 0
  fake.klipperAnswer = null
})
afterEach(() => vi.useRealTimers())

it('preserves repeated events while merging the snapshot/live overlap once', async () => {
  const { consoleStore } = await load()
  setConnection('open')
  emit('notify_gcode_response', ['A'])
  emit('notify_gcode_response', ['A'])
  emit('notify_gcode_response', ['B'])
  take('server.gcode_store').resolve({
    gcode_store: [
      { message: 'A', time: Date.now() / 1000, type: 'response' },
      { message: 'A', time: Date.now() / 1000, type: 'response' },
    ],
  })
  await settle()

  expect(consoleStore.get().entries.map((entry) => entry.message)).toEqual(['A', 'A', 'B'])
  expect(fake.projected.at(-1)?.map((entry) => entry.message)).toEqual(['A', 'A', 'B'])
})

it('merges the overlap once when the printer clock is far from the browser clock', async () => {
  const { consoleStore } = await load()
  // The printer is 90 s behind, as a LAN-only printer without time sync can be.
  fake.clockOffset = -90
  setConnection('open')
  emit('notify_gcode_response', ['A'])
  emit('notify_gcode_response', ['B'])
  take('server.gcode_store').resolve({
    gcode_store: [
      { message: 'old', time: Date.now() / 1000 - 95, type: 'response' },
      { message: 'A', time: Date.now() / 1000 - 90, type: 'response' },
    ],
  })
  await settle()

  expect(consoleStore.get().entries.map((entry) => entry.message)).toEqual(['old', 'A', 'B'])
  expect(fake.projected.at(-1)?.map((entry) => entry.message)).toEqual(['old', 'A', 'B'])
})

it('does not treat an older identical snapshot message as the pending event', async () => {
  const { consoleStore } = await load()
  setConnection('open')
  emit('notify_gcode_response', ['A'])
  emit('notify_gcode_response', ['A'])
  emit('notify_gcode_response', ['B'])
  take('server.gcode_store').resolve({ gcode_store: [{ message: 'A', time: Date.now() / 1000 - 5, type: 'response' }] })
  await settle()

  expect(consoleStore.get().entries.map((entry) => entry.message)).toEqual(['A', 'A', 'A', 'B'])
})

it('keeps the same text when snapshot and live event types differ', async () => {
  const { consoleStore } = await load()
  setConnection('open')
  emit('notify_gcode_response', ['A'])
  take('server.gcode_store').resolve({ gcode_store: [{ message: 'A', time: Date.now() / 1000, type: 'command' }] })
  await settle()

  expect(consoleStore.get().entries.map((entry) => [entry.message, entry.type])).toEqual([
    ['A', 'command'],
    ['A', 'response'],
  ])
})

it('preserves commands and RPC errors that occur during history bootstrap', async () => {
  const { consoleStore } = await load()
  setConnection('open')
  pushPrinterLine('G28', 'command')
  pushPrinterLine('request failed', 'error')
  emit('notify_gcode_response', ['Done'])
  take('server.gcode_store').resolve({
    gcode_store: [
      { message: 'G28', time: Date.now() / 1000, type: 'command' },
      { message: 'Done', time: Date.now() / 1000, type: 'response' },
    ],
  })
  await settle()

  expect(consoleStore.get().entries.map((entry) => [entry.message, entry.type])).toEqual([
    ['G28', 'command'],
    ['!! request failed', 'response'],
    ['Done', 'response'],
  ])
})

it('reloads on reconnect without duplicating the overlapping live event', async () => {
  const { consoleStore } = await load()
  setConnection('open')
  take('server.gcode_store').resolve({ gcode_store: [{ message: 'older', time: Date.now() / 1000 - 5, type: 'response' }] })
  await settle()
  setConnection('closed')
  setConnection('open')
  emit('notify_gcode_response', ['new'])
  take('server.gcode_store').resolve({
    gcode_store: [
      { message: 'older', time: Date.now() / 1000 - 5, type: 'response' },
      { message: 'new', time: Date.now() / 1000, type: 'response' },
    ],
  })
  await settle()

  expect(consoleStore.get().entries.map((entry) => entry.message)).toEqual(['older', 'new'])
  expect(fake.handlers.get('notify_gcode_response')?.size).toBe(1)
})

it('detaches and fences a history request that resolves after stop', async () => {
  fake.state = 'open'
  const { consoleStore, stopConsoleStore } = await load()
  const history = take('server.gcode_store')
  stopConsoleStore()
  expect(fake.handlers.get('notify_gcode_response')?.size ?? 0).toBe(0)
  expect(fake.stateHandlers.size).toBe(0)
  expect(fake.printerSubscribers.size).toBe(0)

  history.resolve({ gcode_store: [{ message: 'late', time: Date.now() / 1000, type: 'response' }] })
  await settle()
  expect(consoleStore.get()).toEqual({ entries: [], loaded: false })
})

it('keeps exactly one attachment across repeated start and stop cycles', async () => {
  const { startConsoleStore, stopConsoleStore } = await load()
  startConsoleStore()
  expect(fake.handlers.get('notify_gcode_response')?.size).toBe(1)
  expect(fake.stateHandlers.size).toBe(1)
  expect(fake.printerSubscribers.size).toBe(1)

  stopConsoleStore()
  stopConsoleStore()
  expect(fake.handlers.get('notify_gcode_response')?.size ?? 0).toBe(0)
  expect(fake.stateHandlers.size).toBe(0)
  expect(fake.printerSubscribers.size).toBe(0)

  startConsoleStore()
  startConsoleStore()
  expect(fake.handlers.get('notify_gcode_response')?.size).toBe(1)
  expect(fake.stateHandlers.size).toBe(1)
  expect(fake.printerSubscribers.size).toBe(1)
})
it('asks for the history only once Klipper has answered, keeping the lines that arrive meanwhile', async () => {
  const { consoleStore } = await load()
  let answer!: () => void
  fake.klipperAnswer = new Promise((resolve) => (answer = resolve))
  setConnection('open')
  await settle()
  expect(fake.calls.some((call) => call.method === 'server.gcode_store')).toBe(false)
  emit('notify_gcode_response', ['while waiting'])
  answer()
  await settle()
  take('server.gcode_store').resolve({ gcode_store: [{ message: 'older', time: Date.now() / 1000 - 60, type: 'response' }] })
  await settle()
  expect(consoleStore.get().entries.map((entry) => entry.message)).toEqual(['older', 'while waiting'])
})

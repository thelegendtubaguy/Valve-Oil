// Evaluates Diagnostics collectors, the JavaScript expressions Fluidd's custom charts store, away from the page and
// with the network turned off, as Fluidd's sandboxedEval worker does. Collectors come from Moonraker's database, which
// anyone who can reach Moonraker can write, so they must not run with the page's access.

type CollectRequest = { id: number; printer: unknown; collectors: string[] }
type Scope = { onmessage: ((e: MessageEvent<CollectRequest>) => void) | null; postMessage: (m: unknown) => void }
const scope = self as unknown as Scope & Record<string, unknown>

const disable = (key: string, value?: unknown) => {
  try {
    Object.defineProperty(scope, key, { configurable: false, writable: false, value })
  } catch {}
}
const blocked = () => {
  throw new Error('network disabled in this worker')
}
disable('fetch', () => Promise.reject(new Error('fetch disabled in this worker')))
disable('XMLHttpRequest', blocked)
disable('importScripts', blocked)
disable('WebSocket')
disable('WebTransport')
disable('RTCPeerConnection')
disable('EventSource')
disable('indexedDB')
disable('caches')

// Indirect eval runs in the worker's global scope; `printer` is the only name a collector sees from us.
const evaluate = (0, eval)('(function (printer, collector) { return eval(collector) })') as (printer: unknown, collector: string) => unknown

scope.onmessage = (e) => {
  const { id, printer, collectors } = e.data
  const results: Record<string, number | string> = {}
  for (const c of collectors) {
    try {
      const v = evaluate(printer, c)
      results[c] = typeof v === 'number' ? v : typeof v === 'boolean' ? Number(v) : String(v)
    } catch (err) {
      results[c] = `Error: ${err instanceof Error ? err.message : String(err)}`
    }
  }
  scope.postMessage({ id, results })
}

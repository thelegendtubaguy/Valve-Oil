// Spoolman state, as the Fluidd fork's Spoolman store keeps it: the active spool from Moonraker, the spool list through
// Moonraker's Spoolman proxy, and live spool changes from Spoolman's own websocket, with why that websocket failed.
import { createStore, useStore } from '../../lib/store'
import { client, printer } from '../../state/printer'
import { applySocketMessage, spoolmanSocketUrl, spoolmanUrl, unwrapProxy, type RawSpool, type SocketMessage, type SpoolmanInfo } from './spoolman'

/** Why Spoolman's websocket failed, as Fluidd's diagnoseHttpEndpoint finds it. */
export type SocketDiagnostic = 'mixed-content' | 'cors' | 'reachable' | 'unreachable'

export type SpoolmanState = {
  /** The spool list loaded, so Spoolman is reachable through Moonraker. */
  connected: boolean
  /** Moonraker's proxy error, when the spool list couldn't be loaded. */
  error: string | null
  spools: readonly RawSpool[]
  activeSpool: number | null
  info: SpoolmanInfo | null
  /** Spoolman's address as the browser reaches it, from moonraker.conf. */
  url: string | null
  socketOpen: boolean
  socketDiagnostic: SocketDiagnostic | null
}

const store = createStore<SpoolmanState>({ connected: false, error: null, spools: [], activeSpool: null, info: null, url: null, socketOpen: false, socketDiagnostic: null })

export function useSpoolman<S>(selector: (s: SpoolmanState) => S): S {
  return useStore(store, selector)
}

const patch = (p: Partial<SpoolmanState>) => store.set((s) => ({ ...s, ...p }))

function proxyGet<T>(path: string) {
  return client.call<unknown>('server.spoolman.proxy', { request_method: 'GET', path, use_v2_response: true }).then((r) => unwrapProxy<T>(r))
}

async function loadActive() {
  try {
    const r = await client.call<{ spool_id: number | null }>('server.spoolman.get_spool_id')
    patch({ activeSpool: r.spool_id ?? null })
  } catch {}
}

async function loadSpools() {
  let r: Awaited<ReturnType<typeof proxyGet<RawSpool[]>>>
  try {
    r = await proxyGet<RawSpool[]>('/v1/spool')
  } catch (e) {
    r = { response: null, error: e instanceof Error ? e.message : String(e) }
  }
  if (r.error != null || !Array.isArray(r.response)) {
    patch({ connected: false, error: r.error })
    return
  }
  patch({ spools: r.response, connected: true, error: null })
  openSocket()
}

async function loadInfo() {
  try {
    const r = await proxyGet<SpoolmanInfo>('/v1/info')
    if (r.response) patch({ info: r.response })
  } catch {}
}

async function loadUrl() {
  try {
    const r = await client.call<{ config: { spoolman?: { server?: string } } }>('server.config')
    patch({ url: spoolmanUrl(r.config.spoolman?.server, client.httpBase) ?? null })
  } catch {}
}

/**
 * Refreshes one spool through the proxy: the active spool when it isn't in the list (archived, or added while the
 * websocket was down), and its weight during a print when Spoolman's websocket can't report it.
 */
async function refreshSpool(id: number) {
  try {
    const r = await proxyGet<RawSpool>(`/v1/spool/${id}`)
    if (!r.response) return
    const spool = r.response
    store.set((s) => {
      const i = s.spools.findIndex((x) => x.id === spool.id)
      const spools = [...s.spools]
      if (i >= 0) spools[i] = spool
      else spools.push(spool)
      return { ...s, spools }
    })
  } catch {}
}

async function init() {
  loadActive()
  loadInfo()
  // The websocket opens once the spools load, and needs the address first.
  await loadUrl()
  await loadSpools()
}

// ---- Spoolman's own websocket, for spool changes Moonraker doesn't pass on ----
let socket: WebSocket | null = null
let diagnoseAbort: AbortController | null = null

function openSocket() {
  const url = store.get().url
  if (!url) return
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return
  let ws: WebSocket
  try {
    ws = new WebSocket(spoolmanSocketUrl(url))
  } catch {
    patch({ socketOpen: false, socketDiagnostic: 'unreachable' })
    return
  }
  socket = ws
  let opened = false
  ws.onopen = () => {
    opened = true
    diagnoseAbort?.abort()
    patch({ socketOpen: true, socketDiagnostic: null })
  }
  ws.onclose = () => {
    if (socket !== ws) return
    socket = null
    patch({ socketOpen: false })
    if (!opened) diagnose(url)
  }
  ws.onmessage = (e) => {
    let msg: SocketMessage
    try {
      msg = JSON.parse(String(e.data))
    } catch {
      return
    }
    store.set((s) => ({ ...s, spools: applySocketMessage(s.spools, msg) }))
  }
}

function closeSocket() {
  const ws = socket
  socket = null
  ws?.close()
  patch({ socketOpen: false })
}

/**
 * Fluidd's diagnoseHttpEndpoint: a page served over HTTPS can't reach an HTTP Spoolman; a probe that fails with CORS
 * but succeeds without it means Spoolman refuses this page's origin; failing both means it's unreachable from here.
 */
async function diagnose(url: string) {
  if (location.protocol === 'https:' && url.startsWith('http://')) return patch({ socketDiagnostic: 'mixed-content' })
  diagnoseAbort?.abort()
  const abort = (diagnoseAbort = new AbortController())
  const signal = AbortSignal.any([abort.signal, AbortSignal.timeout(5000)])
  const probe = `${url.replace(/\/+$/, '')}/api/v1/info?t=${Date.now()}`
  try {
    await fetch(probe, { signal, cache: 'no-cache' })
    return patch({ socketDiagnostic: 'reachable' })
  } catch {
    if (abort.signal.aborted) return
  }
  try {
    await fetch(probe, { signal, mode: 'no-cors', cache: 'no-cache' })
    patch({ socketDiagnostic: 'cors' })
  } catch {
    if (!abort.signal.aborted) patch({ socketDiagnostic: 'unreachable' })
  }
}

// ---- actions ----

/** Sets Moonraker's active spool; with tltg_spoolman it also becomes the loaded slot's spool. Null clears it. */
export async function setActiveSpool(id: number | null) {
  const r = await client.call<{ spool_id: number | null }>('server.spoolman.post_spool_id', id == null ? {} : { spool_id: id })
  patch({ activeSpool: r?.spool_id ?? id })
}

export function ensureSpool(id: number) {
  if (!store.get().spools.some((s) => s.id === id)) refreshSpool(id)
}

// ---- lifecycle: started by the first card, kept for the page ----
let started = false
const PRINT_POLL_MS = 30000

export function startSpoolman() {
  if (started) return
  started = true
  if (client.state === 'open') init()
  client.onState((st) => {
    if (st === 'open') init()
    else closeSocket()
  })
  client.on('notify_active_spool_set', (p) => {
    const id = (p[0] as { spool_id?: number | null } | undefined)?.spool_id ?? null
    patch({ activeSpool: id })
    if (id != null) ensureSpool(id)
  })
  client.on('notify_spoolman_status_changed', (p) => {
    const ok = !!(p[0] as { spoolman_connected?: boolean } | undefined)?.spoolman_connected
    // Connected: reload, and connected is set once the spools arrive. Gone: say so now.
    if (ok) init()
    else (patch({ connected: false }), closeSocket())
  })
  // Without Spoolman's websocket the remaining weight would stay where it was when the page opened, so during a print
  // the active spool is read again through Moonraker now and then. Fluidd doesn't; it shows the stale weight.
  setInterval(() => {
    const s = store.get()
    const state = printer.get().status.print_stats?.state
    if (s.connected && !s.socketOpen && s.activeSpool != null && (state === 'printing' || state === 'paused')) refreshSpool(s.activeSpool)
  }, PRINT_POLL_MS)
}

// Minimal Moonraker JSON-RPC websocket client with reconnect.

export type ConnectionState = 'connecting' | 'open' | 'closed'

type Pending = {
  resolve: (value: unknown) => void
  reject: (error: Error) => void
  timer: ReturnType<typeof setTimeout>
}

export class RpcError extends Error {
  readonly code: number
  constructor(code: number, message: string) {
    super(message)
    this.code = code
  }
}

export type NotificationHandler = (params: unknown[]) => void

export class MoonrakerClient {
  readonly wsUrl: string
  readonly httpBase: string
  private ws: WebSocket | null = null
  private nextId = 1
  private pending = new Map<number, Pending>()
  private handlers = new Map<string, Set<NotificationHandler>>()
  private stateHandlers = new Set<(s: ConnectionState) => void>()
  private retryMs = 1000
  private stopped = false
  state: ConnectionState = 'closed'

  constructor(origin: string) {
    const url = new URL(origin)
    this.httpBase = `${url.protocol}//${url.host}`
    this.wsUrl = `${url.protocol === 'https:' ? 'wss' : 'ws'}://${url.host}/websocket`
  }

  connect() {
    this.stopped = false
    this.setState('connecting')
    const ws = new WebSocket(this.wsUrl)
    this.ws = ws
    ws.onopen = () => {
      this.retryMs = 1000
      this.setState('open')
    }
    ws.onmessage = (ev) => this.onMessage(ev.data)
    ws.onclose = () => {
      if (this.ws !== ws) return
      this.ws = null
      for (const [, p] of this.pending) {
        clearTimeout(p.timer)
        p.reject(new Error('Connection closed'))
      }
      this.pending.clear()
      this.setState('closed')
      if (!this.stopped) {
        setTimeout(() => this.connect(), this.retryMs)
        this.retryMs = Math.min(this.retryMs * 2, 10000)
      }
    }
  }

  close() {
    this.stopped = true
    this.ws?.close()
  }

  call<T = unknown>(method: string, params?: Record<string, unknown>, timeoutMs = 30000): Promise<T> {
    const ws = this.ws
    if (!ws || ws.readyState !== WebSocket.OPEN) return Promise.reject(new Error('Not connected to Moonraker'))
    const id = this.nextId++
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`${method} timed out`))
      }, timeoutMs)
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, timer })
      ws.send(JSON.stringify({ jsonrpc: '2.0', method, params, id }))
    })
  }

  /** Runs G-code. Resolves when Klipper finishes it; no timeout, as homing or heating can take minutes. */
  gcode(script: string) {
    return this.call<string>('printer.gcode.script', { script }, 24 * 3600 * 1000)
  }

  on(method: string, fn: NotificationHandler) {
    let set = this.handlers.get(method)
    if (!set) this.handlers.set(method, (set = new Set()))
    set.add(fn)
    return () => set!.delete(fn)
  }

  onState(fn: (s: ConnectionState) => void) {
    this.stateHandlers.add(fn)
    return () => this.stateHandlers.delete(fn)
  }

  fileUrl(root: string, path: string) {
    return `${this.httpBase}/server/files/${root}/${path.split('/').map(encodeURIComponent).join('/')}`
  }

  async upload(file: File, path = '', onProgress?: (fraction: number) => void): Promise<void> {
    const form = new FormData()
    form.append('root', 'gcodes')
    if (path) form.append('path', path)
    form.append('file', file, file.name)
    await new Promise<void>((resolve, reject) => {
      const xhr = new XMLHttpRequest()
      xhr.open('POST', `${this.httpBase}/server/files/upload`)
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total)
      xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new Error(`Upload failed (${xhr.status})`)))
      xhr.onerror = () => reject(new Error('Upload failed'))
      xhr.send(form)
    })
  }

  private setState(s: ConnectionState) {
    this.state = s
    for (const fn of this.stateHandlers) fn(s)
  }

  private onMessage(raw: string) {
    let msg: { id?: number; method?: string; params?: unknown[]; result?: unknown; error?: { code: number; message: string } }
    try {
      msg = JSON.parse(raw)
    } catch {
      return
    }
    if (msg.id !== undefined) {
      const p = this.pending.get(msg.id)
      if (!p) return
      this.pending.delete(msg.id)
      clearTimeout(p.timer)
      if (msg.error) p.reject(new RpcError(msg.error.code, msg.error.message))
      else p.resolve(msg.result)
      return
    }
    if (msg.method) {
      const set = this.handlers.get(msg.method)
      if (set) for (const fn of set) fn(msg.params ?? [])
    }
  }
}

/** Where Moonraker is: same origin by default, or `?moonraker=host:port` (remembered) for development. */
export function resolveMoonrakerOrigin(): string {
  const q = new URLSearchParams(location.search).get('moonraker')
  if (q) {
    const origin = q.includes('://') ? q : `http://${q}`
    try {
      localStorage.setItem('vo-moonraker', origin)
    } catch {}
    return origin
  }
  try {
    const saved = localStorage.getItem('vo-moonraker')
    if (saved && import.meta.env.DEV) return saved
  } catch {}
  return location.origin
}

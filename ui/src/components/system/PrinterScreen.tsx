import { useCallback, useEffect, useRef, useState, type PointerEvent as RPointerEvent } from 'react'
import { Lock, LockOpen, Maximize, MonitorSmartphone } from 'lucide-react'
import { Card, confirm } from '../ui'
import { client } from '../../state/printer'
import './system.css'
import { t } from '../../i18n'
import { tr } from '../../i18n/rich'

// Ported from the Fluidd fork's QidiScreenCard and QidiScreenView: a live view of the printer's touchscreen from Valve
// Oil's vo-remote-screen service, which the printer's nginx serves at /vo-screen/ws on the same origin as Moonraker.
// It connects only while this tab is shown in a visible browser tab, and the service reads the screen only while a
// viewer is connected.

const RETRY_DELAYS = [1000, 2000, 5000, 10000]
const MOVE_INTERVAL = 30

export function PrinterScreen() {
  const [control, setControl] = useState(false)
  const [touch, setTouch] = useState(false)
  const view = useRef<HTMLDivElement>(null)
  // Control presses the real touchscreen, so it starts off and needs confirming.
  const toggleControl = async () => {
    if (control) return setControl(false)
    const ok = await confirm({
      title: t('system.screen.control_title'),
      body: t('system.screen.control_body'),
      confirm: t('system.screen.control_confirm'),
      danger: true,
    })
    setControl(ok)
  }
  return (
    <Card
      title={t('system.tabs.screen')}
      icon={<MonitorSmartphone size={15} />}
      aside={
        <span className="flex flex-wrap justify-end gap-1.5">
          <button className={`btn btn-sm ${control ? 'btn-primary' : ''}`} disabled={!touch && !control} onClick={toggleControl}>
            {control ? <LockOpen size={14} /> : <Lock size={14} />}
            {control ? t('system.screen.control_on') : t('system.screen.view_only')}
          </button>
          <button className="btn btn-sm" onClick={() => view.current?.requestFullscreen?.()}>
            <Maximize size={14} /> {t('system.screen.full_screen')}
          </button>
        </span>
      }
    >
      <div className="mx-auto max-w-[880px]">
        <ScreenView ref={view} control={control} onTouch={setTouch} />
      </div>
      <p className="mb-0 text-[12.5px] text-ink-3">
        {/* i18n-ignore: a service name */}
        {tr('system.screen.about', { service: <span className="code">qidi-client</span> })}
      </p>
    </Card>
  )
}

type State = 'connecting' | 'live' | 'error' | 'disconnected' | 'paused'
type Point = { x: number; y: number }

/** The service accepts only same-origin connections; Moonraker's origin is the page's own on the printer. */
function socketUrl(token: string) {
  const url = new URL('/vo-screen/ws', client.httpBase)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  if (token) url.search = `?token=${encodeURIComponent(token)}`
  return url.toString()
}

function ScreenView({ control, onTouch, ref }: { control: boolean; onTouch: (v: boolean) => void; ref: React.Ref<HTMLDivElement> }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const [state, setState] = useState<State>('connecting')
  const [message, setMessage] = useState('')
  const [size, setSize] = useState({ width: 800, height: 480 })
  const [touch, setTouch] = useState(false)
  const [hasFrame, setHasFrame] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)
  const box = useRef<HTMLDivElement | null>(null)

  const socket = useRef<WebSocket | null>(null)
  const pointer = useRef<number | null>(null)
  const lastMove = useRef(0)
  const live = useRef({ state, size })
  live.current = { state, size }

  useEffect(() => onTouch(touch), [touch, onTouch])

  useEffect(() => {
    let closed = false
    let connecting = false
    let retry = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    let decoding = false
    let nextFrame: Blob | null = null

    const draw = async (blob: Blob) => {
      if (decoding) return void (nextFrame = blob)
      decoding = true
      try {
        const bitmap = await createImageBitmap(blob)
        const c = canvas.current
        if (c && !closed) {
          if (c.width !== bitmap.width) c.width = bitmap.width
          if (c.height !== bitmap.height) c.height = bitmap.height
          c.getContext('2d')?.drawImage(bitmap, 0, 0)
          setHasFrame(true)
        }
        bitmap.close()
      } catch {
        // A frame that fails to decode is dropped.
      }
      decoding = false
      const next = nextFrame
      nextFrame = null
      if (next && !closed) draw(next)
    }

    const disconnect = () => {
      clearTimeout(timer)
      timer = undefined
      const ws = socket.current
      socket.current = null
      pointer.current = null
      if (ws) {
        ws.onmessage = null
        ws.onclose = null
        ws.close()
      }
    }

    const connect = async () => {
      if (closed || document.hidden || socket.current || connecting) return
      clearTimeout(timer)
      connecting = true
      let token = ''
      try {
        // A one-shot token proves this session when Moonraker requires logins.
        token = (await client.call<string>('access.oneshot_token')) ?? ''
      } catch {
        token = ''
      }
      connecting = false
      if (closed || document.hidden || socket.current) return
      const ws = new WebSocket(socketUrl(token))
      ws.binaryType = 'blob'
      ws.onmessage = (ev) => {
        if (ws !== socket.current) return
        if (typeof ev.data !== 'string') return void draw(ev.data as Blob)
        let m: Record<string, unknown>
        try {
          m = JSON.parse(ev.data)
        } catch {
          return
        }
        if (m.type === 'hello') {
          setState('live')
          setMessage(m.touch ? '' : String(m.touch_error || t('system.screen.touch_unavailable')))
          setSize({ width: Number(m.width) || 800, height: Number(m.height) || 480 })
          setTouch(!!m.touch)
          retry = 0
        } else if (m.type === 'error') {
          setState('error')
          setMessage(String(m.message ?? ''))
        }
      }
      ws.onclose = () => {
        if (ws !== socket.current) return
        socket.current = null
        pointer.current = null
        if (closed) return
        if (live.current.state !== 'error') {
          setState('disconnected')
          setMessage('')
        }
        const delay = RETRY_DELAYS[Math.min(retry, RETRY_DELAYS.length - 1)]
        retry++
        timer = setTimeout(connect, delay)
      }
      socket.current = ws
    }

    const onVisibility = () => {
      if (document.hidden) {
        disconnect()
        setState('paused')
      } else connect()
    }
    const onFullscreen = () => setFullscreen(document.fullscreenElement === box.current)
    document.addEventListener('visibilitychange', onVisibility)
    document.addEventListener('fullscreenchange', onFullscreen)
    connect()
    return () => {
      closed = true
      document.removeEventListener('visibilitychange', onVisibility)
      document.removeEventListener('fullscreenchange', onFullscreen)
      disconnect()
    }
  }, [])

  const canControl = control && touch && state === 'live'

  const send = useCallback((action: 'down' | 'move' | 'up', p: Point) => {
    const ws = socket.current
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'touch', action, x: p.x, y: p.y }))
  }, [])
  const release = useCallback(() => {
    if (pointer.current == null) return
    pointer.current = null
    send('up', { x: 0, y: 0 })
  }, [send])
  useEffect(() => {
    if (!control) release()
  }, [control, release])

  // The canvas letterboxes the screen in full screen, so map through the fitted image.
  const position = (e: RPointerEvent): Point => {
    const rect = canvas.current!.getBoundingClientRect()
    const { width, height } = live.current.size
    const scale = Math.min(rect.width / width, rect.height / height)
    const left = rect.left + (rect.width - width * scale) / 2
    const top = rect.top + (rect.height - height * scale) / 2
    const clamp = (v: number, n: number) => Math.max(0, Math.min(n - 1, Math.round(v)))
    return { x: clamp((e.clientX - left) / scale, width), y: clamp((e.clientY - top) / scale, height) }
  }

  const status =
    state === 'error'
      ? t('system.screen.unavailable', { message })
      : state === 'disconnected'
        ? t('system.screen.disconnected')
        : state === 'paused'
          ? t('system.screen.paused')
          : !hasFrame
            ? t('system.screen.connecting')
            : ''

  return (
    <div
      ref={(el) => {
        box.current = el
        if (typeof ref === 'function') ref(el)
        else if (ref) (ref as React.RefObject<HTMLDivElement | null>).current = el
      }}
      className={`vo-screen ${fullscreen ? 'vo-screen--full' : ''}`}
    >
      <canvas
        ref={canvas}
        width={size.width}
        height={size.height}
        className={`vo-screen__canvas ${canControl ? 'vo-screen__canvas--control' : ''} ${!hasFrame || state !== 'live' ? 'vo-screen__canvas--dim' : ''}`}
        onPointerDown={(e) => {
          if (!canControl || pointer.current != null || e.button !== 0) return
          e.preventDefault()
          pointer.current = e.pointerId
          canvas.current?.setPointerCapture?.(e.pointerId)
          lastMove.current = performance.now()
          send('down', position(e))
        }}
        onPointerMove={(e) => {
          if (e.pointerId !== pointer.current) return
          e.preventDefault()
          const now = performance.now()
          if (now - lastMove.current >= MOVE_INTERVAL) {
            lastMove.current = now
            send('move', position(e))
          }
        }}
        onPointerUp={(e) => {
          if (e.pointerId !== pointer.current) return
          e.preventDefault()
          send('move', position(e))
          release()
        }}
        onPointerCancel={(e) => e.pointerId === pointer.current && release()}
        onLostPointerCapture={(e) => e.pointerId === pointer.current && release()}
        onContextMenu={(e) => canControl && e.preventDefault()}
      />
      {status ? <div className="vo-screen__status">{status}</div> : state === 'live' && message ? <div className="vo-screen__notice">{t('system.screen.view_only_notice', { message })}</div> : null}
    </div>
  )
}

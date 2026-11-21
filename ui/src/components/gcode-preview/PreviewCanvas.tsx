import { forwardRef, useCallback, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, type PointerEvent } from 'react'
import { buildLayerPaths } from './gcode-preview'
import type { BBox, BuildLayerPathsOptions, Layer, LayerPaths, ParsedGcode, Point } from './types'
import type { PreviewSettings } from './settings'
import { t } from '../../i18n'

// The top-down layer view, drawn on a canvas. Layer paths are built once as SVG path data (the Fluidd fork's
// buildLayerPaths) and turned into Path2D objects, so panning and zooming only re-strokes cached paths: smooth on a
// slow tablet even with a large layer. Coordinates are millimetres; the view maps them to CSS pixels.

export type ExcludeObject = { name: string; center?: number[]; polygon?: number[][]; excluded: boolean; current: boolean }

export type PreviewCanvasProps = {
  data: ParsedGcode
  layers: readonly Layer[]
  bounds: BBox
  bed: BBox
  layer: number
  /** Last move drawn in colour on the current layer. */
  moveEnd: number
  settings: PreviewSettings
  colors: Record<string, string>
  isDark: boolean
  /** Klipper's exclude_object objects, drawn instead of the file's parts while this file prints. */
  objects?: ExcludeObject[] | null
  onExclude?: (name: string) => void
  /** The printer's toolhead, drawn while following a print. */
  toolhead?: Point | null
  /** Pan, pinch, and wheel zoom; off for the compact live preview. */
  interactive?: boolean
  /** Fit to the print rather than the bed (Fluidd's auto zoom). */
  fitPrint: boolean
  className?: string
}

export type PreviewCanvasHandle = { fit: () => void; zoom: (factor: number) => void }

type View = { s: number; tx: number; ty: number }

const EMPTY_PATHS: LayerPaths = { extrusions: {}, moves: '', retractions: [], unretractions: [], toolhead: { x: 0, y: 0 }, tool: 'T0' }

function layerRange(layers: readonly Layer[], layer: number): [number, number] {
  return [layers[layer]?.move ?? 0, (layers[layer + 1]?.move ?? Number.POSITIVE_INFINITY) - 1]
}

/** One Path2D of a layer's extrusions, for the grey previous, current, and next layers. */
function greyLayer(data: ParsedGcode, layers: readonly Layer[], layer: number): Path2D | null {
  if (layer < 0 || layer >= layers.length) return null
  const [start, end] = layerRange(layers, layer)
  const paths = buildLayerPaths(data.moves, start, end)
  const d = Object.values(paths.extrusions).join('')
  return d ? new Path2D(d) : null
}

function cssVars(): Record<string, string> {
  const cs = getComputedStyle(document.documentElement)
  const v = (n: string) => cs.getPropertyValue(n).trim()
  return { inset: v('--inset'), line: v('--line'), lineStrong: v('--line-strong'), ink2: v('--ink-2'), ink3: v('--ink-3'), accent: v('--accent'), green: v('--green'), red: v('--red'), blue: v('--blue'), surface: v('--surface') }
}

export const PreviewCanvas = forwardRef<PreviewCanvasHandle, PreviewCanvasProps>(function PreviewCanvas(props, ref) {
  const { data, layers, layer, moveEnd, settings, objects, interactive = true, fitPrint, bed, bounds, isDark } = props
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const view = useRef<View>({ s: 1, tx: 0, ty: 0 })
  const size = useRef({ w: 0, h: 0 })
  const moved = useRef(false)
  const frame = useRef(0)

  const fx = settings.flip.horizontal ? -1 : 1
  const fy = settings.flip.vertical ? -1 : 1

  // ---- paths: rebuilt only when what they show changes ----
  const prev = useMemo(() => (settings.showPreviousLayer ? greyLayer(data, layers, layer - 1) : null), [data, layers, layer, settings.showPreviousLayer])
  const active = useMemo(() => (settings.showCurrentLayer ? greyLayer(data, layers, layer) : null), [data, layers, layer, settings.showCurrentLayer])
  const next = useMemo(() => (settings.showNextLayer ? greyLayer(data, layers, layer + 1) : null), [data, layers, layer, settings.showNextLayer])
  const current = useMemo(() => {
    if (!layers.length) return { paths: EMPTY_PATHS, extrusions: [] as [string, Path2D][], moves: null as Path2D | null }
    const options: BuildLayerPathsOptions = {
      colorBy: settings.colorMode,
      speedRange: data.speedRange,
      fanRange: data.fanRange,
      includeExtrusions: settings.showExtrusions,
      includeMoves: settings.showMoves,
      includeRetractions: settings.showRetractions,
      includeTools: true,
    }
    const paths = buildLayerPaths(data.moves, layers[layer]?.move ?? 0, moveEnd, options)
    return {
      paths,
      extrusions: Object.entries(paths.extrusions).map(([group, d]) => [group, new Path2D(d)] as [string, Path2D]),
      moves: paths.moves ? new Path2D(paths.moves) : null,
    }
  }, [data, layers, layer, moveEnd, settings.colorMode, settings.showExtrusions, settings.showMoves, settings.showRetractions])
  const parts = useMemo(
    () =>
      data.parts.map((p) => {
        const path = new Path2D()
        p.polygon.forEach((pt, i) => (i ? path.lineTo(pt.x, pt.y) : path.moveTo(pt.x, pt.y)))
        path.closePath()
        return path
      }),
    [data.parts],
  )
  const objectPaths = useMemo(
    () =>
      (objects ?? [])
        .filter((o) => o.polygon?.length && o.center && o.center.length >= 2)
        .map((o) => {
          const path = new Path2D()
          o.polygon!.forEach(([x, y], i) => (i ? path.lineTo(x, y) : path.moveTo(x, y)))
          path.closePath()
          return { ...o, path }
        }),
    [objects],
  )
  const colorsCss = useMemo(cssVars, [isDark])

  // ---- view ----
  const fitRect = useCallback(
    (r: BBox, pad: number) => {
      const { w, h } = size.current
      const rw = Math.max(1, r.x.max - r.x.min)
      const rh = Math.max(1, r.y.max - r.y.min)
      const s = Math.min(w / rw, h / rh) / pad
      const cx = (r.x.min + r.x.max) / 2
      const cy = (r.y.min + r.y.max) / 2
      view.current = { s, tx: w / 2 - cx * s * fx, ty: h / 2 - cy * s * fy }
    },
    [fx, fy],
  )
  const bedBox = useMemo(
    () => ({
      x: { min: Math.min(bed.x.min, bounds.x.min) - 2, max: Math.max(bed.x.max, bounds.x.max) + 2 },
      y: { min: Math.min(bed.y.min, bounds.y.min) - 2, max: Math.max(bed.y.max, bounds.y.max) + 2 },
    }),
    [bed, bounds],
  )
  const printFits = bounds.x.max - bounds.x.min > 0 && bounds.y.max - bounds.y.min > 0
  const fit = useCallback(() => {
    moved.current = false
    if (fitPrint && printFits) fitRect(bounds, 1.1)
    else fitRect(bedBox, 1)
  }, [fitPrint, printFits, bounds, bedBox, fitRect])
  const minScale = () => {
    const { w, h } = size.current
    return (Math.min(w / (bedBox.x.max - bedBox.x.min), h / (bedBox.y.max - bedBox.y.min)) || 1) * 0.5
  }

  // ---- drawing ----
  const draw = useCallback(() => {
    frame.current = 0
    const c = canvas.current
    if (!c) return
    const ctx = c.getContext('2d')
    if (!ctx) return
    const dpr = window.devicePixelRatio || 1
    const { w, h } = size.current
    const { s, tx, ty } = view.current
    const css = colorsCss
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, w, h)
    ctx.setTransform(dpr * s * fx, 0, 0, dpr * s * fy, dpr * tx, dpr * ty)
    ctx.lineCap = 'round'
    ctx.lineJoin = 'round'
    const px = 1 / s
    const extrusionWidth = Math.max(settings.extrusionLineWidth, 1.1 * px)

    if (settings.drawBackground) {
      const { x, y } = bed
      ctx.fillStyle = css.inset
      ctx.fillRect(x.min, y.min, x.max - x.min, y.max - y.min)
      // Fluidd's 10 mm squares, hidden when too dense to read.
      if (s * 10 >= 6) {
        ctx.beginPath()
        for (let gx = Math.ceil(x.min / 10) * 10; gx <= x.max; gx += 10) (ctx.moveTo(gx, y.min), ctx.lineTo(gx, y.max))
        for (let gy = Math.ceil(y.min / 10) * 10; gy <= y.max; gy += 10) (ctx.moveTo(x.min, gy), ctx.lineTo(x.max, gy))
        ctx.strokeStyle = css.line
        ctx.lineWidth = px
        ctx.stroke()
      }
      ctx.strokeStyle = css.lineStrong
      ctx.lineWidth = px
      ctx.strokeRect(x.min, y.min, x.max - x.min, y.max - y.min)
    }

    if (settings.drawOrigin) {
      const len = 10 * Math.max(1, 3 * px)
      ctx.lineWidth = 1.5 * px
      ctx.globalAlpha = 0.6
      ctx.strokeStyle = '#ff3b30'
      ctx.beginPath()
      ctx.moveTo(0, 0)
      ctx.lineTo(len, 0)
      ctx.stroke()
      ctx.strokeStyle = '#34c759'
      ctx.beginPath()
      ctx.moveTo(0, 0)
      ctx.lineTo(0, len)
      ctx.stroke()
      ctx.globalAlpha = 1
    }

    if (settings.showParts && !objectPaths.length && parts.length) {
      ctx.fillStyle = css.ink3
      ctx.globalAlpha = 0.2
      for (const p of parts) ctx.fill(p)
      ctx.globalAlpha = 1
    }

    const grey = isDark ? 'lightgrey' : '#555'
    const strokeGrey = (p: Path2D | null, color = grey) => {
      if (!p) return
      ctx.strokeStyle = color
      ctx.globalAlpha = 0.6
      ctx.lineWidth = extrusionWidth
      ctx.stroke(p)
      ctx.globalAlpha = 1
    }
    strokeGrey(prev)
    strokeGrey(active)

    ctx.lineWidth = extrusionWidth
    for (const [group, p] of current.extrusions) {
      ctx.strokeStyle = props.colors[group] ?? grey
      ctx.stroke(p)
    }
    if (current.moves) {
      ctx.strokeStyle = 'gray'
      ctx.lineWidth = Math.max(settings.moveLineWidth, 0.6 * px)
      ctx.stroke(current.moves)
    }

    // As in Fluidd, both marks sit just above their point on screen: a retraction points up from its base on the
    // point, an unretraction points down to it. At least a few pixels so they show at any zoom.
    const tri = Math.max(settings.retractionIconSize, 5 * px)
    const up = -fy * tri
    const marks = (points: Point[], color: string, retraction: boolean) => {
      if (!points.length) return
      ctx.beginPath()
      for (const { x, y } of points) {
        if (retraction) {
          ctx.moveTo(x - (tri / 2) * fx, y)
          ctx.lineTo(x + (tri / 2) * fx, y)
          ctx.lineTo(x, y + up)
        } else {
          ctx.moveTo(x, y)
          ctx.lineTo(x - (tri / 2) * fx, y + up)
          ctx.lineTo(x + (tri / 2) * fx, y + up)
        }
        ctx.closePath()
      }
      ctx.globalAlpha = 0.9
      ctx.fillStyle = color
      ctx.fill()
      ctx.globalAlpha = 1
    }
    marks(current.paths.retractions, 'red', true)
    marks(current.paths.unretractions, 'green', false)

    // Fluidd draws the next layer light grey in both themes; the light theme's darker grey keeps it visible there.
    strokeGrey(next, isDark ? 'lightgrey' : grey)

    // End of the drawn path.
    if (layers.length) {
      ctx.fillStyle = css.accent
      ctx.beginPath()
      ctx.arc(current.paths.toolhead.x, current.paths.toolhead.y, Math.max(0.6, 3 * px), 0, Math.PI * 2)
      ctx.fill()
    }

    if (settings.showParts && objectPaths.length) {
      for (const o of objectPaths) {
        const color = o.excluded ? css.red : o.current ? css.blue : css.green
        ctx.strokeStyle = color
        ctx.fillStyle = color
        ctx.globalAlpha = 0.15
        ctx.fill(o.path)
        ctx.globalAlpha = 0.7
        ctx.lineWidth = Math.max(0.5, 1.2 * px)
        ctx.stroke(o.path)
        ctx.globalAlpha = 1
      }
    }

    if (props.toolhead) {
      const { x, y } = props.toolhead
      ctx.strokeStyle = css.accent
      ctx.lineWidth = 2 * px
      ctx.beginPath()
      ctx.arc(x, y, 7 * px, 0, Math.PI * 2)
      ctx.moveTo(x - 11 * px, y)
      ctx.lineTo(x - 4 * px, y)
      ctx.moveTo(x + 4 * px, y)
      ctx.lineTo(x + 11 * px, y)
      ctx.moveTo(x, y - 11 * px)
      ctx.lineTo(x, y - 4 * px)
      ctx.moveTo(x, y + 4 * px)
      ctx.lineTo(x, y + 11 * px)
      ctx.stroke()
    }

    // Exclude buttons at each object's centre, in screen pixels so they stay tappable at any zoom.
    if (settings.showParts && objectPaths.length && props.onExclude) {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      for (const o of objectPaths) {
        const [sx, sy] = toScreen(o.center![0], o.center![1])
        const color = o.excluded ? css.red : o.current ? css.blue : css.green
        ctx.beginPath()
        ctx.arc(sx, sy, 9, 0, Math.PI * 2)
        ctx.fillStyle = css.surface
        ctx.globalAlpha = 0.85
        ctx.fill()
        ctx.globalAlpha = 1
        ctx.lineWidth = 1.5
        ctx.strokeStyle = color
        ctx.stroke()
        ctx.beginPath()
        ctx.moveTo(sx - 3.5, sy - 3.5)
        ctx.lineTo(sx + 3.5, sy + 3.5)
        ctx.moveTo(sx + 3.5, sy - 3.5)
        ctx.lineTo(sx - 3.5, sy + 3.5)
        ctx.stroke()
      }
    }
  }, [colorsCss, fx, fy, settings, bed, parts, objectPaths, prev, active, next, current, props.colors, props.toolhead, props.onExclude, isDark, layers.length])

  const schedule = useCallback(() => {
    if (!frame.current) frame.current = requestAnimationFrame(() => drawRef.current())
  }, [])
  const drawRef = useRef(draw)
  drawRef.current = draw
  useEffect(schedule, [draw, schedule])
  useEffect(() => () => cancelAnimationFrame(frame.current), [])

  function toScreen(x: number, y: number): [number, number] {
    const { s, tx, ty } = view.current
    return [x * s * fx + tx, y * s * fy + ty]
  }

  // Refit when the file, the fit, or the flips change, unless the view was moved by hand since.
  const fitRef = useRef(fit)
  fitRef.current = fit
  useLayoutEffect(() => {
    fit()
    schedule()
  }, [fit, schedule])

  // ---- size ----
  useLayoutEffect(() => {
    const el = wrap.current
    const c = canvas.current
    if (!el || !c) return
    const resize = () => {
      const r = el.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      const old = size.current
      size.current = { w: r.width, h: r.height }
      c.width = Math.max(1, Math.round(r.width * dpr))
      c.height = Math.max(1, Math.round(r.height * dpr))
      if (!moved.current || !old.w) fitRef.current()
      else {
        // Keep the same point centred.
        view.current.tx += (r.width - old.w) / 2
        view.current.ty += (r.height - old.h) / 2
      }
      drawRef.current()
    }
    resize()
    const ro = new ResizeObserver(resize)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const zoomAt = useCallback(
    (factor: number, cx: number, cy: number) => {
      const v = view.current
      const s = Math.min(Math.max(v.s * factor, minScale()), minScale() * 120)
      const k = s / v.s
      view.current = { s, tx: cx - (cx - v.tx) * k, ty: cy - (cy - v.ty) * k }
      moved.current = true
      schedule()
    },
    [schedule, bedBox],
  )

  useImperativeHandle(ref, () => ({
    fit: () => (fit(), schedule()),
    zoom: (factor) => zoomAt(factor, size.current.w / 2, size.current.h / 2),
  }))

  // ---- gestures: drag to pan, pinch or wheel to zoom, double-click to fit, tap an object's button to exclude it ----
  const pointers = useRef(new Map<number, { x: number; y: number }>())
  const gesture = useRef<{ dist: number; cx: number; cy: number; travel: number } | null>(null)
  useEffect(() => {
    const c = canvas.current
    if (!c || !interactive) return
    const wheel = (e: WheelEvent) => {
      e.preventDefault()
      const r = c.getBoundingClientRect()
      const delta = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaY
      zoomAt(Math.exp(-delta * 0.0015), e.clientX - r.left, e.clientY - r.top)
    }
    c.addEventListener('wheel', wheel, { passive: false })
    return () => c.removeEventListener('wheel', wheel)
  }, [interactive, zoomAt])

  const local = (e: PointerEvent) => {
    const r = canvas.current!.getBoundingClientRect()
    return { x: e.clientX - r.left, y: e.clientY - r.top }
  }
  const centroid = () => {
    const pts = [...pointers.current.values()]
    const cx = pts.reduce((a, p) => a + p.x, 0) / pts.length
    const cy = pts.reduce((a, p) => a + p.y, 0) / pts.length
    const dist = pts.length > 1 ? Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y) : 0
    return { cx, cy, dist }
  }
  const onPointerDown = (e: PointerEvent) => {
    if (!interactive) return
    canvas.current!.setPointerCapture(e.pointerId)
    pointers.current.set(e.pointerId, local(e))
    gesture.current = { ...centroid(), travel: pointers.current.size > 1 ? 99 : 0 }
  }
  const onPointerMove = (e: PointerEvent) => {
    if (!interactive || !pointers.current.has(e.pointerId) || !gesture.current) return
    pointers.current.set(e.pointerId, local(e))
    const g = centroid()
    const prevG = gesture.current
    const v = view.current
    v.tx += g.cx - prevG.cx
    v.ty += g.cy - prevG.cy
    if (g.dist && prevG.dist) {
      const s = Math.min(Math.max(v.s * (g.dist / prevG.dist), minScale()), minScale() * 120)
      const k = s / v.s
      view.current = { s, tx: g.cx - (g.cx - v.tx) * k, ty: g.cy - (g.cy - v.ty) * k }
    }
    gesture.current = { ...g, travel: prevG.travel + Math.hypot(g.cx - prevG.cx, g.cy - prevG.cy) }
    if (gesture.current.travel > 3) moved.current = true
    schedule()
  }
  const onPointerUp = (e: PointerEvent) => {
    if (!pointers.current.has(e.pointerId)) return
    pointers.current.delete(e.pointerId)
    const g = gesture.current
    if (pointers.current.size) gesture.current = { ...centroid(), travel: 99 }
    else gesture.current = null
    // A tap, not a drag: an exclude button under it?
    if (g && g.travel <= 3 && e.type === 'pointerup' && props.onExclude && settings.showParts) {
      const { x, y } = local(e)
      for (const o of objectPaths) {
        if (o.excluded) continue
        const [sx, sy] = toScreen(o.center![0], o.center![1])
        if (Math.hypot(sx - x, sy - y) <= 14) return props.onExclude(o.name)
      }
    }
  }

  return (
    <div ref={wrap} className={`overflow-hidden ${props.className || 'relative'}`}>
      <canvas
        ref={canvas}
        className="absolute inset-0 h-full w-full"
        style={{ touchAction: interactive ? 'none' : 'auto', cursor: interactive ? 'grab' : 'default' }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={() => interactive && (fit(), schedule())}
        aria-label={t('preview.canvas_label')}
        role="img"
      />
    </div>
  )
})

import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { bedCorners, gridX, gridY, meshColor, nearestCell, niceStep, type BedBounds, type MeshGrid, type MeshMarker } from './mesh'
import { useWidth } from './common'
import { saveUrl } from '../../lib/browser'
import { t } from '../../i18n'

type Props = {
  grid: MeshGrid
  bed: BedBounds
  zMin: number
  zMax: number
  zBoxMin: number
  zBoxMax: number
  wireframe: boolean
  /** Height of a flat plane to draw through the mesh, or null for none. */
  flatAt: number | null
  markers: MeshMarker[]
  height: number
  /** Formats a height for the tooltip and the Z axis. */
  unit?: 'mm' | 'µm'
}

export type MeshSurfaceHandle = { download: (name: string) => void; resetView: () => void }

const DEFAULT_VIEW = { az: -32, el: 32, zoom: 1 }
// The Z axis box is this tall relative to the bed's longer side, like ECharts' default 3D grid.
const BOX_HEIGHT = 0.55

type Projected = { sx: number; sy: number; depth: number }
type Point = { sx: number; sy: number }

/** A line from a to b in the current stroke style. */
function seg(ctx: CanvasRenderingContext2D, a: Point, b: Point) {
  ctx.beginPath()
  ctx.moveTo(a.sx, a.sy)
  ctx.lineTo(b.sx, b.sy)
  ctx.stroke()
}

/** A closed path through the points, to fill or stroke. */
function shape(ctx: CanvasRenderingContext2D, pts: Point[]) {
  ctx.beginPath()
  pts.forEach((p, i) => (i ? ctx.lineTo(p.sx, p.sy) : ctx.moveTo(p.sx, p.sy)))
  ctx.closePath()
}

/**
 * A bed mesh as a shaded surface on canvas: drag to rotate, scroll or pinch to zoom, hover for a point's height.
 * Drawn back to front, which is exact for a height field seen from above.
 */
export const MeshSurface3D = forwardRef<MeshSurfaceHandle, Props>(function MeshSurface3D(props, ref) {
  const { grid, bed, zMin, zMax, zBoxMin, zBoxMax, wireframe, flatAt, markers, height, unit = 'mm' } = props
  const [wrap, width] = useWidth<HTMLDivElement>()
  const canvas = useRef<HTMLCanvasElement>(null)
  const [view, setView] = useState(DEFAULT_VIEW)
  const [hover, setHover] = useState<{ x: number; y: number; z: number; sx: number; sy: number; label?: string } | null>(null)
  const drag = useRef<{ x: number; y: number; pointers: Map<number, { x: number; y: number }>; pinch: number | null } | null>(null)

  // World to screen. x/y are normalised to the bed's longer side, z to the box height.
  const project = useCallback(
    (x: number, y: number, z: number): Projected => {
      const span = Math.max(bed.maxX - bed.minX, bed.maxY - bed.minY) || 1
      const xn = (x - (bed.minX + bed.maxX) / 2) / span
      const yn = (y - (bed.minY + bed.maxY) / 2) / span
      const zn = ((z - (zBoxMin + zBoxMax) / 2) / (zBoxMax - zBoxMin || 1)) * BOX_HEIGHT
      const a = (view.az * Math.PI) / 180
      const e = (view.el * Math.PI) / 180
      const x1 = xn * Math.cos(a) - yn * Math.sin(a)
      const y1 = xn * Math.sin(a) + yn * Math.cos(a)
      const up = zn * Math.cos(e) + y1 * Math.sin(e)
      const depth = zn * Math.sin(e) - y1 * Math.cos(e)
      // A little perspective, so the box doesn't look inside out.
      const p = 3.2 / (3.2 - depth)
      // Fits the box's bounding sphere, so it stays the same size as it turns, with room for the axis labels.
      const scale = ((Math.min(width, height * 1.3) - 56) / 2 / 0.86) * view.zoom * p
      return { sx: width / 2 + x1 * scale, sy: height / 2 + 6 - up * scale, depth }
    },
    [bed, zBoxMin, zBoxMax, view, width, height],
  )

  const draw = useCallback(() => {
    const c = canvas.current
    if (!c) return
    const dpr = window.devicePixelRatio || 1
    c.width = Math.round(width * dpr)
    c.height = Math.round(height * dpr)
    const ctx = c.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    ctx.clearRect(0, 0, width, height)
    const css = getComputedStyle(c)
    const ink = css.getPropertyValue('--ink-3').trim() || '#888'
    const line = css.getPropertyValue('--line-strong').trim() || '#444'
    const warn = css.getPropertyValue('--accent').trim() || '#f2b05a'
    const font = '11px Montserrat Variable, Montserrat, sans-serif'

    // Floor of the box with a 50 mm grid, and the Z axis at the back corner.
    const { minX, maxX, minY, maxY } = bed
    ctx.lineWidth = 1
    ctx.strokeStyle = line
    ctx.globalAlpha = 0.6
    const step = niceStep(Math.max(maxX - minX, maxY - minY) / (width < 560 ? 3 : 6))
    for (let x = Math.ceil(minX / step) * step; x <= maxX; x += step) seg(ctx, project(x, minY, zBoxMin), project(x, maxY, zBoxMin))
    for (let y = Math.ceil(minY / step) * step; y <= maxY; y += step) seg(ctx, project(minX, y, zBoxMin), project(maxX, y, zBoxMin))
    ctx.globalAlpha = 1
    shape(ctx, [project(minX, minY, zBoxMin), project(maxX, minY, zBoxMin), project(maxX, maxY, zBoxMin), project(minX, maxY, zBoxMin)])
    ctx.stroke()
    // The front edge, where the printer's door is.
    ctx.strokeStyle = warn
    ctx.lineWidth = 2
    seg(ctx, project(minX, minY, zBoxMin), project(maxX, minY, zBoxMin))
    ctx.strokeStyle = line
    ctx.lineWidth = 1
    // The vertical edge farthest from the viewer carries the Z axis.
    const corners: [number, number][] = [
      [minX, minY],
      [maxX, minY],
      [maxX, maxY],
      [minX, maxY],
    ]
    const back = corners.reduce((best, c) => (project(c[0], c[1], zBoxMin).depth < project(best[0], best[1], zBoxMin).depth ? c : best))
    // The mesh's corners by name, out past each corner. Laid out first so the axis labels can make way for them.
    const surface = css.getPropertyValue('--surface').trim() || '#161c25'
    const ink2 = css.getPropertyValue('--ink-2').trim() || '#aaa'
    const accentInk = css.getPropertyValue('--accent-ink').trim() || warn
    const mid = project((grid.min[0] + grid.max[0]) / 2, (grid.min[1] + grid.max[1]) / 2, (zBoxMin + zBoxMax) / 2)
    const meshBed = { minX: grid.min[0], maxX: grid.max[0], minY: grid.min[1], maxY: grid.max[1] }
    ctx.font = `600 ${font}`
    const tags = bedCorners(meshBed).map((k) => {
      const { row, col } = nearestCell(grid, k.x, k.y)
      const p = project(k.x, k.y, grid.z[row][col])
      const len = Math.hypot(p.sx - mid.sx, p.sy - mid.sy) || 1
      const ux = (p.sx - mid.sx) / len
      const uy = (p.sy - mid.sy) / len
      const w = ctx.measureText(k.label).width + 14
      const h = 19
      // Far enough out along the diagonal that the tag's nearest edge clears the corner. The corner in front of the Z
      // axis puts its tag up and to the right, clear of the axis and its labels.
      const reach = 12 + (Math.abs(ux) * w) / 2 + (Math.abs(uy) * h) / 2
      const atAxis = (k.x === meshBed.minX) === (back[0] === minX) && (k.y === meshBed.minY) === (back[1] === minY)
      const cx = atAxis ? p.sx + w / 2 + 10 : p.sx + ux * reach
      const cy = atAxis ? p.sy - h / 2 - 8 : p.sy + uy * reach
      return {
        label: k.label,
        front: k.y === meshBed.minY,
        w,
        h,
        cx: Math.min(width - w / 2 - 2, Math.max(w / 2 + 2, cx)),
        cy: Math.min(height - h / 2 - 2, Math.max(h / 2 + 2, cy)),
      }
    })
    // Text at (x, y) in the current alignment, unless it would sit under a corner's tag.
    const label = (text: string, x: number, y: number) => {
      const w = ctx.measureText(text).width
      const left = ctx.textAlign === 'right' ? x - w : ctx.textAlign === 'center' ? x - w / 2 : x
      const top = ctx.textBaseline === 'top' ? y : y - 7
      if (tags.some((t) => left < t.cx + t.w / 2 + 2 && left + w > t.cx - t.w / 2 - 2 && top < t.cy + t.h / 2 + 2 && top + 13 > t.cy - t.h / 2 - 2)) return
      ctx.fillText(text, x, y)
    }
    seg(ctx, project(back[0], back[1], zBoxMin), project(back[0], back[1], zBoxMax))
    ctx.fillStyle = ink
    ctx.font = font
    ctx.textAlign = 'right'
    ctx.textBaseline = 'middle'
    for (const z of [zBoxMin, (zBoxMin + zBoxMax) / 2, zBoxMax]) {
      const p = project(back[0], back[1], z)
      seg(ctx, { sx: p.sx - 3, sy: p.sy }, { sx: p.sx + 3, sy: p.sy })
      label(fmtZ(z, unit), p.sx - 6, p.sy)
    }
    // Axis labels on the floor's front edges.
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    const frontY = project(minX, minY, zBoxMin).depth + project(maxX, minY, zBoxMin).depth > project(minX, maxY, zBoxMin).depth + project(maxX, maxY, zBoxMin).depth ? minY : maxY
    const frontX = project(minX, minY, zBoxMin).depth + project(minX, maxY, zBoxMin).depth > project(maxX, minY, zBoxMin).depth + project(maxX, maxY, zBoxMin).depth ? minX : maxX
    for (let x = Math.ceil(minX / step) * step; x <= maxX; x += step) {
      const p = project(x, frontY, zBoxMin)
      label(String(Math.round(x)), p.sx, p.sy + 5)
    }
    for (let y = Math.ceil(minY / step) * step; y <= maxY; y += step) {
      const p = project(frontX, y, zBoxMin)
      label(String(Math.round(y)), p.sx + (frontX === minX ? -12 : 12), p.sy + 3)
    }
    const xl = project((minX + maxX) / 2, frontY, zBoxMin)
    const yl = project(frontX, (minY + maxY) / 2, zBoxMin)
    ctx.font = `600 ${font}`
    label('X', xl.sx, xl.sy + 20)
    label('Y', yl.sx + (frontX === minX ? -30 : 30), yl.sy + 12)

    // Surface cells (and the flat plane's), back to front.
    type Quad = { pts: Projected[]; depth: number; fill: string; alpha: number; wire: boolean }
    const quads: Quad[] = []
    const pts = grid.z.map((row, r) => row.map((z, c) => project(gridX(grid, c), gridY(grid, r), z)))
    for (let r = 0; r < grid.rows - 1; r++)
      for (let c = 0; c < grid.cols - 1; c++) {
        const q = [pts[r][c], pts[r][c + 1], pts[r + 1][c + 1], pts[r + 1][c]]
        const z = (grid.z[r][c] + grid.z[r][c + 1] + grid.z[r + 1][c + 1] + grid.z[r + 1][c]) / 4
        quads.push({ pts: q, depth: q.reduce((a, p) => a + p.depth, 0) / 4, fill: meshColor(z, zMin, zMax), alpha: 1, wire: wireframe })
      }
    if (flatAt != null) {
      const f = (x: number, y: number) => project(x, y, flatAt)
      const n = 4
      for (let i = 0; i < n; i++)
        for (let j = 0; j < n; j++) {
          const x0 = grid.min[0] + ((grid.max[0] - grid.min[0]) * i) / n
          const x1 = grid.min[0] + ((grid.max[0] - grid.min[0]) * (i + 1)) / n
          const y0 = grid.min[1] + ((grid.max[1] - grid.min[1]) * j) / n
          const y1 = grid.min[1] + ((grid.max[1] - grid.min[1]) * (j + 1)) / n
          const q = [f(x0, y0), f(x1, y0), f(x1, y1), f(x0, y1)]
          quads.push({ pts: q, depth: q.reduce((a, p) => a + p.depth, 0) / 4, fill: 'rgb(128,128,128)', alpha: 0.28, wire: false })
        }
    }
    quads.sort((a, b) => a.depth - b.depth)
    ctx.lineJoin = 'round'
    for (const q of quads) {
      shape(ctx, q.pts)
      ctx.globalAlpha = q.alpha
      ctx.fillStyle = q.fill
      ctx.fill()
      if (q.alpha === 1) {
        // Hairline in the cell's own colour hides the seams between cells.
        ctx.strokeStyle = q.fill
        ctx.lineWidth = 0.6
        ctx.stroke()
      }
      if (q.wire) {
        ctx.strokeStyle = 'rgba(255,255,255,0.55)'
        ctx.lineWidth = 0.8
        ctx.stroke()
      }
    }
    ctx.globalAlpha = 1

    // Marked points, as pins standing on the surface.
    const lift = (zBoxMax - zBoxMin) * 0.12
    for (const m of markers) {
      const { row, col } = nearestCell(grid, m.x, m.y)
      const base = project(m.x, m.y, grid.z[row][col])
      const top = project(m.x, m.y, grid.z[row][col] + lift)
      ctx.strokeStyle = warn
      ctx.lineWidth = 2
      seg(ctx, base, top)
      ctx.fillStyle = warn
      ctx.beginPath()
      ctx.arc(top.sx, top.sy, 8, 0, Math.PI * 2)
      ctx.fill()
      ctx.strokeStyle = '#0b0b0b'
      ctx.lineWidth = 1
      ctx.stroke()
      ctx.fillStyle = '#0b0b0b'
      ctx.font = `800 11px Montserrat Variable, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'middle'
      ctx.fillText('!', top.sx, top.sy + 0.5)
    }

    // The corners' tags over the surface, and the printer turned the same way as the bed.
    ctx.font = `600 ${font}`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    for (const t of tags) {
      ctx.beginPath()
      ctx.roundRect(t.cx - t.w / 2, t.cy - t.h / 2, t.w, t.h, t.h / 2)
      ctx.globalAlpha = 0.9
      ctx.fillStyle = surface
      ctx.fill()
      ctx.globalAlpha = 1
      ctx.strokeStyle = t.front ? warn : line
      ctx.lineWidth = 1
      ctx.stroke()
      ctx.fillStyle = t.front ? accentInk : ink2
      ctx.fillText(t.label, t.cx, t.cy + 0.5)
    }
    drawPrinter(ctx, view, bed, width < 560 ? 36 : 46, dpr, { surface, line, warn, accentInk, ink: ink2 })

    if (hover) {
      ctx.fillStyle = '#fff'
      ctx.strokeStyle = '#0b0b0b'
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.arc(hover.sx, hover.sy, 4.5, 0, Math.PI * 2)
      ctx.fill()
      ctx.stroke()
    }
  }, [grid, bed, zMin, zMax, zBoxMin, zBoxMax, wireframe, flatAt, markers, width, height, project, hover, unit])

  useEffect(draw, [draw])
  // Redraw when the theme changes the colours.
  useEffect(() => {
    const mo = new MutationObserver(draw)
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] })
    return () => mo.disconnect()
  }, [draw])

  useImperativeHandle(ref, () => ({
    download: (name: string) => {
      const c = canvas.current
      if (!c) return
      // Paint the card's surface behind the transparent canvas so the image reads in any viewer.
      const out = document.createElement('canvas')
      out.width = c.width
      out.height = c.height
      const ctx = out.getContext('2d')!
      ctx.fillStyle = getComputedStyle(c).getPropertyValue('--surface').trim() || '#161c25'
      ctx.fillRect(0, 0, out.width, out.height)
      ctx.drawImage(c, 0, 0)
      saveUrl(out.toDataURL('image/png'), `${name}.png`)
    },
    resetView: () => setView(DEFAULT_VIEW),
  }))

  // Zoom with the wheel; React's onWheel is passive, so it can't stop the page scrolling.
  useEffect(() => {
    const c = canvas.current
    if (!c) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      setView((v) => ({ ...v, zoom: Math.min(3, Math.max(0.5, v.zoom * Math.exp(-e.deltaY * 0.0015))) }))
    }
    c.addEventListener('wheel', onWheel, { passive: false })
    return () => c.removeEventListener('wheel', onWheel)
  }, [])

  const pick = (sx: number, sy: number) => {
    let best: { d: number; x: number; y: number; z: number; sx: number; sy: number; label?: string } | null = null
    for (const m of markers) {
      const { row, col } = nearestCell(grid, m.x, m.y)
      const p = project(m.x, m.y, grid.z[row][col] + (zBoxMax - zBoxMin) * 0.12)
      const d = Math.hypot(p.sx - sx, p.sy - sy)
      if (d < 12 && (!best || d < best.d)) best = { d, x: m.x, y: m.y, z: grid.z[row][col], sx: p.sx, sy: p.sy, label: m.label }
    }
    if (best) return best
    for (let r = 0; r < grid.rows; r++)
      for (let c = 0; c < grid.cols; c++) {
        const x = gridX(grid, c)
        const y = gridY(grid, r)
        const p = project(x, y, grid.z[r][c])
        const d = Math.hypot(p.sx - sx, p.sy - sy)
        if (d < 18 && (!best || d < best.d)) best = { d, x, y, z: grid.z[r][c], sx: p.sx, sy: p.sy }
      }
    return best
  }

  return (
    <div ref={wrap} className="relative w-full select-none" style={{ height }}>
      <canvas
        ref={canvas}
        style={{ width: '100%', height, touchAction: 'none', cursor: drag.current ? 'grabbing' : 'grab', display: 'block' }}
        role="img"
        aria-label={t('tune.mesh.surface_label')}
        onPointerDown={(e) => {
          ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
          const d = drag.current ?? { x: e.clientX, y: e.clientY, pointers: new Map(), pinch: null }
          d.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
          d.x = e.clientX
          d.y = e.clientY
          d.pinch = null
          drag.current = d
          setHover(null)
        }}
        onPointerMove={(e) => {
          const d = drag.current
          if (d && d.pointers.has(e.pointerId)) {
            d.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY })
            if (d.pointers.size === 2) {
              const [p, q] = [...d.pointers.values()]
              const dist = Math.hypot(p.x - q.x, p.y - q.y)
              if (d.pinch) setView((v) => ({ ...v, zoom: Math.min(3, Math.max(0.5, (v.zoom * dist) / d.pinch!)) }))
              d.pinch = dist
              return
            }
            const dx = e.clientX - d.x
            const dy = e.clientY - d.y
            d.x = e.clientX
            d.y = e.clientY
            setView((v) => ({ ...v, az: v.az + dx * 0.45, el: Math.min(89, Math.max(5, v.el + dy * 0.35)) }))
            return
          }
          if (e.pointerType !== 'mouse') return
          const rect = (e.target as HTMLElement).getBoundingClientRect()
          const p = pick(e.clientX - rect.left, e.clientY - rect.top)
          setHover(p ? { x: p.x, y: p.y, z: p.z, sx: p.sx, sy: p.sy, label: p.label } : null)
        }}
        onPointerUp={(e) => {
          const d = drag.current
          d?.pointers.delete(e.pointerId)
          if (!d || d.pointers.size === 0) drag.current = null
        }}
        onPointerCancel={() => (drag.current = null)}
        onPointerLeave={() => setHover(null)}
        onDoubleClick={() => setView(DEFAULT_VIEW)}
      />
      {hover && (
        <div
          className="pointer-events-none absolute z-10 rounded-lg bg-surface px-2.5 py-1.5 text-[12px] leading-snug"
          style={{ left: Math.min(hover.sx + 12, width - 190), top: Math.max(0, hover.sy - 60), boxShadow: 'var(--shadow-overlay)' }}
        >
          {hover.label && <div className="mb-0.5 font-semibold text-warn">{hover.label}</div>}
          <div className="num text-ink-2">
            X {hover.x.toFixed(1)} · Y {hover.y.toFixed(1)}
          </div>
          <div className="num font-bold">Z {fmtZ(hover.z, unit, true)}</div>
        </div>
      )}
    </div>
  )
})

/**
 * A small printer in the canvas's top left corner, turned as the bed is, with its door at the front, so you can see
 * which side of the mesh faces you.
 */
function drawPrinter(
  ctx: CanvasRenderingContext2D,
  view: { az: number; el: number },
  bed: BedBounds,
  size: number,
  dpr: number,
  c: { surface: string; line: string; warn: string; accentInk: string; ink: string },
) {
  const a = (view.az * Math.PI) / 180
  const e = (view.el * Math.PI) / 180
  const span = Math.max(bed.maxX - bed.minX, bed.maxY - bed.minY) || 1
  const hx = (bed.maxX - bed.minX) / span / 2
  const hy = (bed.maxY - bed.minY) / span / 2
  const hz = 0.5
  const ox = 14 + size * 1.25
  const oy = 12 + size * 1.25
  type V = [number, number, number]
  const P = ([x, y, z]: V) => {
    const x1 = x * Math.cos(a) - y * Math.sin(a)
    const y1 = x * Math.sin(a) + y * Math.cos(a)
    return { sx: ox + x1 * size, sy: oy - (z * Math.cos(e) + y1 * Math.sin(e)) * size }
  }
  // A face shows when its outward normal points at the viewer.
  const faces: { n: V; pts: V[]; front?: boolean }[] = [
    { n: [0, -1, 0], pts: [[-hx, -hy, -hz], [hx, -hy, -hz], [hx, -hy, hz], [-hx, -hy, hz]], front: true },
    { n: [0, 1, 0], pts: [[hx, hy, -hz], [-hx, hy, -hz], [-hx, hy, hz], [hx, hy, hz]] },
    { n: [-1, 0, 0], pts: [[-hx, hy, -hz], [-hx, -hy, -hz], [-hx, -hy, hz], [-hx, hy, hz]] },
    { n: [1, 0, 0], pts: [[hx, -hy, -hz], [hx, hy, -hz], [hx, hy, hz], [hx, -hy, hz]] },
    { n: [0, 0, 1], pts: [[-hx, -hy, hz], [hx, -hy, hz], [hx, hy, hz], [-hx, hy, hz]] },
  ]
  const facing = ([x, y, z]: V) => z * Math.sin(e) - (x * Math.sin(a) + y * Math.cos(a)) * Math.cos(e) > 1e-6
  const poly = (pts: V[]) => shape(ctx, pts.map(P))
  ctx.lineJoin = 'round'
  for (const f of faces) {
    if (!facing(f.n)) continue
    poly(f.pts)
    ctx.fillStyle = c.surface
    ctx.fill()
    if (f.front) {
      ctx.globalAlpha = 0.22
      ctx.fillStyle = c.warn
      ctx.fill()
      ctx.globalAlpha = 1
    }
    ctx.strokeStyle = f.front ? c.warn : c.line
    ctx.lineWidth = f.front ? 1.5 : 1
    ctx.stroke()
  }
  // Text written on a face, as if printed on it: `dir` and `up` are the text's directions seen from outside.
  const label = (text: string, at: V, dir: V, fill: string, up: V = [0, 0, 1]) => {
    const o = P(at)
    const u = P([at[0] + dir[0] / size, at[1] + dir[1] / size, at[2] + dir[2] / size])
    const v = P([at[0] + up[0] / size, at[1] + up[1] / size, at[2] + up[2] / size])
    ctx.save()
    ctx.setTransform(dpr * (u.sx - o.sx), dpr * (u.sy - o.sy), -dpr * (v.sx - o.sx), -dpr * (v.sy - o.sy), dpr * o.sx, dpr * o.sy)
    ctx.fillStyle = fill
    ctx.font = `700 ${Math.round(size * 0.2)}px Montserrat Variable, Montserrat, sans-serif`
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(text, 0, 0)
    ctx.restore()
  }
  // Names only on faces turned far enough to read.
  const readable = (n: V) => facing(n) && Math.abs(n[1] * Math.cos(a) + n[0] * Math.sin(a)) > 0.45
  if (facing([0, -1, 0])) {
    // The door, with FRONT across it.
    poly([
      [-hx * 0.78, -hy, -hz * 0.75],
      [hx * 0.78, -hy, -hz * 0.75],
      [hx * 0.78, -hy, hz * 0.6],
      [-hx * 0.78, -hy, hz * 0.6],
    ])
    ctx.strokeStyle = c.warn
    ctx.lineWidth = 1
    ctx.stroke()
    if (readable([0, -1, 0])) label(t('tune.mesh.printer_front'), [0, -hy, -hz * 0.08], [1, 0, 0], c.accentInk)
  }
  // From above, the top's front edge and FRONT written along it.
  ctx.strokeStyle = c.warn
  ctx.lineWidth = 2
  seg(ctx, P([-hx, -hy, hz]), P([hx, -hy, hz]))
  if (!readable([0, -1, 0]) && facing([0, 0, 1])) label(t('tune.mesh.printer_front'), [0, -hy * 0.62, hz], [1, 0, 0], c.accentInk, [0, 1, 0])
  if (readable([0, 1, 0])) label(t('tune.mesh.printer_back'), [0, hy, 0], [-1, 0, 0], c.ink)
  if (readable([-1, 0, 0])) label(t('tune.mesh.printer_left'), [-hx, 0, 0], [0, -1, 0], c.ink)
  if (readable([1, 0, 0])) label(t('tune.mesh.printer_right'), [hx, 0, 0], [0, 1, 0], c.ink)
}

function fmtZ(z: number, unit: 'mm' | 'µm', precise = false) {
  if (unit === 'µm') return `${z < 0 ? '−' : z > 0 ? '+' : ''}${Math.abs(z * 1000).toFixed(precise ? 1 : 0)} µm`
  return `${z < 0 ? '−' : ''}${Math.abs(z).toFixed(precise ? 4 : 2)}`
}

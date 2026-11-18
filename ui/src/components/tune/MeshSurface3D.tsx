import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { gridX, gridY, meshColor, nearestCell, type BedBounds, type MeshGrid, type MeshMarker } from './mesh'

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

/**
 * A bed mesh as a shaded surface on canvas: drag to rotate, scroll or pinch to zoom, hover for a point's height.
 * Drawn back to front, which is exact for a height field seen from above.
 */
export const MeshSurface3D = forwardRef<MeshSurfaceHandle, Props>(function MeshSurface3D(props, ref) {
  const { grid, bed, zMin, zMax, zBoxMin, zBoxMax, wireframe, flatAt, markers, height, unit = 'mm' } = props
  const wrap = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [width, setWidth] = useState(600)
  const [view, setView] = useState(DEFAULT_VIEW)
  const [hover, setHover] = useState<{ x: number; y: number; z: number; sx: number; sy: number; label?: string } | null>(null)
  const drag = useRef<{ x: number; y: number; pointers: Map<number, { x: number; y: number }>; pinch: number | null } | null>(null)

  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth || 600))
    ro.observe(el)
    setWidth(el.clientWidth || 600)
    return () => ro.disconnect()
  }, [])

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
    const path = (pts: Projected[]) => {
      ctx.beginPath()
      pts.forEach((p, i) => (i ? ctx.lineTo(p.sx, p.sy) : ctx.moveTo(p.sx, p.sy)))
      ctx.closePath()
    }

    // Floor of the box with a 50 mm grid, and the Z axis at the back corner.
    const { minX, maxX, minY, maxY } = bed
    ctx.lineWidth = 1
    ctx.strokeStyle = line
    ctx.globalAlpha = 0.6
    const step = niceStep(Math.max(maxX - minX, maxY - minY) / (width < 560 ? 3 : 6))
    for (let x = Math.ceil(minX / step) * step; x <= maxX; x += step) {
      const a = project(x, minY, zBoxMin)
      const b = project(x, maxY, zBoxMin)
      ctx.beginPath()
      ctx.moveTo(a.sx, a.sy)
      ctx.lineTo(b.sx, b.sy)
      ctx.stroke()
    }
    for (let y = Math.ceil(minY / step) * step; y <= maxY; y += step) {
      const a = project(minX, y, zBoxMin)
      const b = project(maxX, y, zBoxMin)
      ctx.beginPath()
      ctx.moveTo(a.sx, a.sy)
      ctx.lineTo(b.sx, b.sy)
      ctx.stroke()
    }
    ctx.globalAlpha = 1
    path([project(minX, minY, zBoxMin), project(maxX, minY, zBoxMin), project(maxX, maxY, zBoxMin), project(minX, maxY, zBoxMin)])
    ctx.stroke()
    // The vertical edge farthest from the viewer carries the Z axis.
    const corners: [number, number][] = [
      [minX, minY],
      [maxX, minY],
      [maxX, maxY],
      [minX, maxY],
    ]
    const back = corners.reduce((best, c) => (project(c[0], c[1], zBoxMin).depth < project(best[0], best[1], zBoxMin).depth ? c : best))
    const zb = project(back[0], back[1], zBoxMin)
    const zt = project(back[0], back[1], zBoxMax)
    ctx.beginPath()
    ctx.moveTo(zb.sx, zb.sy)
    ctx.lineTo(zt.sx, zt.sy)
    ctx.stroke()
    ctx.fillStyle = ink
    ctx.font = font
    ctx.textAlign = 'right'
    ctx.textBaseline = 'middle'
    for (const z of [zBoxMin, (zBoxMin + zBoxMax) / 2, zBoxMax]) {
      const p = project(back[0], back[1], z)
      ctx.beginPath()
      ctx.moveTo(p.sx - 3, p.sy)
      ctx.lineTo(p.sx + 3, p.sy)
      ctx.stroke()
      ctx.fillText(fmtZ(z, unit), p.sx - 6, p.sy)
    }
    // Axis labels on the floor's front edges.
    ctx.textAlign = 'center'
    ctx.textBaseline = 'top'
    const frontY = project(minX, minY, zBoxMin).depth + project(maxX, minY, zBoxMin).depth > project(minX, maxY, zBoxMin).depth + project(maxX, maxY, zBoxMin).depth ? minY : maxY
    const frontX = project(minX, minY, zBoxMin).depth + project(minX, maxY, zBoxMin).depth > project(maxX, minY, zBoxMin).depth + project(maxX, maxY, zBoxMin).depth ? minX : maxX
    for (let x = Math.ceil(minX / step) * step; x <= maxX; x += step) {
      const p = project(x, frontY, zBoxMin)
      ctx.fillText(String(Math.round(x)), p.sx, p.sy + 5)
    }
    for (let y = Math.ceil(minY / step) * step; y <= maxY; y += step) {
      const p = project(frontX, y, zBoxMin)
      ctx.fillText(String(Math.round(y)), p.sx + (frontX === minX ? -12 : 12), p.sy + 3)
    }
    const xl = project((minX + maxX) / 2, frontY, zBoxMin)
    const yl = project(frontX, (minY + maxY) / 2, zBoxMin)
    ctx.font = `600 ${font}`
    ctx.fillText('X', xl.sx, xl.sy + 20)
    ctx.fillText('Y', yl.sx + (frontX === minX ? -30 : 30), yl.sy + 12)

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
      path(q.pts)
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
      ctx.beginPath()
      ctx.moveTo(base.sx, base.sy)
      ctx.lineTo(top.sx, top.sy)
      ctx.stroke()
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
      const a = document.createElement('a')
      a.href = out.toDataURL('image/png')
      a.download = `${name}.png`
      a.click()
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
        aria-label="Bed mesh, 3D. Drag to rotate."
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
            setView((v) => ({ ...v, az: v.az - dx * 0.45, el: Math.min(89, Math.max(5, v.el + dy * 0.35)) }))
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

function niceStep(raw: number) {
  const p = 10 ** Math.floor(Math.log10(raw))
  const n = raw / p
  return (n < 1.5 ? 1 : n < 3.5 ? 2.5 : n < 7.5 ? 5 : 10) * p
}

function fmtZ(z: number, unit: 'mm' | 'µm', precise = false) {
  if (unit === 'µm') return `${z < 0 ? '−' : z > 0 ? '+' : ''}${Math.abs(z * 1000).toFixed(precise ? 1 : 0)} µm`
  return `${z < 0 ? '−' : ''}${Math.abs(z).toFixed(precise ? 4 : 2)}`
}

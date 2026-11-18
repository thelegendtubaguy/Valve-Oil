import { useEffect, useRef, useState } from 'react'
import { gridX, gridY, meshColor, nearestCell, type BedBounds, type MeshGrid, type MeshMarker } from './mesh'

/**
 * The mesh from above, back of the bed at the top, on the bed's outline. Each point's height shows on hover, and in
 * the cell when there's room. Marked points get an amber ring.
 */
export function MeshHeatmap({ grid, bed, zMin, zMax, markers, height, unit = 'mm' }: { grid: MeshGrid; bed: BedBounds; zMin: number; zMax: number; markers: MeshMarker[]; height: number; unit?: 'mm' | 'µm' }) {
  const wrap = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(600)
  const [hover, setHover] = useState<{ row: number; col: number } | null>(null)
  useEffect(() => {
    const el = wrap.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth || 600))
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const padL = 34
  const padB = 22
  const padT = 6
  const padR = 6
  const bedW = bed.maxX - bed.minX || 1
  const bedH = bed.maxY - bed.minY || 1
  const scale = Math.min((width - padL - padR) / bedW, (height - padT - padB) / bedH)
  const plotW = bedW * scale
  const plotH = bedH * scale
  const ox = padL + (width - padL - padR - plotW) / 2
  const sx = (x: number) => ox + (x - bed.minX) * scale
  const sy = (y: number) => padT + plotH - (y - bed.minY) * scale

  // Cells are centred on their grid point and meet halfway to the next, so the edge cells are half cut off by the
  // mesh area; draw them whole, extending past it, to keep every point the same size.
  const dx = (grid.max[0] - grid.min[0]) / (grid.cols - 1)
  const dy = (grid.max[1] - grid.min[1]) / (grid.rows - 1)
  const cw = dx * scale
  const ch = dy * scale
  const gap = cw > 22 && ch > 22 ? 1.5 : 0
  const showValues = cw >= 38 && ch >= 22
  const marked = new Map<string, string[]>()
  for (const m of markers) {
    const { row, col } = nearestCell(grid, m.x, m.y)
    const k = `${row}:${col}`
    marked.set(k, [...(marked.get(k) ?? []), m.label])
  }
  const fmt = (z: number) => (unit === 'µm' ? `${z < 0 ? '−' : z > 0 ? '+' : ''}${Math.abs(z * 1000).toFixed(1)}` : `${z < 0 ? '−' : ''}${Math.abs(z).toFixed(3)}`)
  const ticks = (lo: number, hi: number) => {
    const step = niceStep((hi - lo) / 5)
    const out: number[] = []
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-6; v += step) out.push(v)
    return out
  }
  const hz = hover ? grid.z[hover.row][hover.col] : null
  const hx = hover ? gridX(grid, hover.col) : 0
  const hy = hover ? gridY(grid, hover.row) : 0

  return (
    <div ref={wrap} className="relative w-full" style={{ height }}>
      <svg width={width} height={height} role="img" aria-label="Bed mesh heat map, back of the bed at the top" onPointerLeave={() => setHover(null)}>
        <rect x={sx(bed.minX)} y={sy(bed.maxY)} width={plotW} height={plotH} rx={6} fill="var(--inset)" stroke="var(--line-strong)" />
        {ticks(bed.minX, bed.maxX).map((x) => (
          <text key={`x${x}`} x={sx(x)} y={padT + plotH + 15} textAnchor="middle" fontSize={11} fill="var(--ink-3)">
            {Math.round(x)}
          </text>
        ))}
        {ticks(bed.minY, bed.maxY).map((y) => (
          <text key={`y${y}`} x={ox - 6} y={sy(y) + 4} textAnchor="end" fontSize={11} fill="var(--ink-3)">
            {Math.round(y)}
          </text>
        ))}
        {grid.z.map((row, r) =>
          row.map((z, c) => {
            const k = `${r}:${c}`
            const x = sx(gridX(grid, c)) - cw / 2
            const y = sy(gridY(grid, r)) - ch / 2
            const on = hover?.row === r && hover.col === c
            return (
              <g key={k} onPointerEnter={() => setHover({ row: r, col: c })}>
                <rect x={x + gap / 2} y={y + gap / 2} width={cw - gap} height={ch - gap} rx={gap ? 3 : 0} fill={meshColor(z, zMin, zMax)} stroke={on ? 'var(--ink)' : 'none'} strokeWidth={1.5} />
                {showValues && (
                  <text x={x + cw / 2} y={y + ch / 2 + 4} textAnchor="middle" fontSize={Math.min(12, cw / 4.6)} fontWeight={600} fill={contrast(meshColor(z, zMin, zMax))} pointerEvents="none">
                    {fmt(z)}
                  </text>
                )}
              </g>
            )
          }),
        )}
        {[...marked.keys()].map((k) => {
          const [r, c] = k.split(':').map(Number)
          const x = sx(gridX(grid, c)) - cw / 2
          const y = sy(gridY(grid, r)) - ch / 2
          const b = Math.max(5, Math.min(9, cw / 4))
          return (
            <g key={`m${k}`} pointerEvents="none">
              <rect x={x + 2} y={y + 2} width={cw - 4} height={ch - 4} rx={3} fill="none" stroke="var(--accent)" strokeWidth={2.5} />
              <circle cx={x + cw - b - 1} cy={y + b + 1} r={b} fill="var(--accent)" />
              <text x={x + cw - b - 1} y={y + b + 4.5} textAnchor="middle" fontSize={10} fontWeight={800} fill="#0b0b0b">
                !
              </text>
            </g>
          )
        })}
      </svg>
      {hover && hz != null && (
        <div
          className="pointer-events-none absolute z-10 rounded-lg bg-surface px-2.5 py-1.5 text-[12px] leading-snug"
          style={{ left: Math.min(sx(hx) + cw / 2 + 6, width - 200), top: Math.max(0, sy(hy) - 30), boxShadow: 'var(--shadow-overlay)' }}
        >
          {marked.get(`${hover.row}:${hover.col}`)?.map((l) => (
            <div key={l} className="mb-0.5 font-semibold text-warn">
              {l}
            </div>
          ))}
          <div className="num text-ink-2">
            X {hx.toFixed(1)} · Y {hy.toFixed(1)}
          </div>
          <div className="num font-bold">
            Z {fmt(hz)} {unit}
          </div>
        </div>
      )}
    </div>
  )
}

function niceStep(raw: number) {
  const p = 10 ** Math.floor(Math.log10(raw || 1))
  const n = raw / p
  return (n < 1.5 ? 1 : n < 3.5 ? 2.5 : n < 7.5 ? 5 : 10) * p
}

/** Dark or light text over a ramp colour. */
function contrast(rgb: string) {
  const [r, g, b] = rgb.match(/\d+/g)!.map(Number)
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? '#14100a' : '#ffffff'
}

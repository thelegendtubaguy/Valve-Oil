import { createStore } from '../../lib/store'
import { t } from '../../i18n'

// Klipper's bed_mesh status. Rows of a matrix run along Y from mesh_min, columns along X.
export type BedMeshProfile = { points: number[][]; mesh_params: Record<string, number | string> }
export type BedMeshStatus = {
  profile_name?: string
  mesh_min?: [number, number]
  mesh_max?: [number, number]
  probed_matrix?: number[][]
  mesh_matrix?: number[][]
  profiles?: Record<string, BedMeshProfile>
}

export type MatrixType = 'probed_matrix' | 'mesh_matrix'

export type MeshProfileEntry = { name: string; active: boolean; adaptive: boolean; range: number }

/**
 * Saved profiles, with the loaded one marked. A loaded mesh that isn't saved (an adaptive mesh calibrated without a
 * profile) is listed too, as Fluidd lists it. `default` first, then by name.
 */
export function meshProfiles(bedMesh: BedMeshStatus | undefined): MeshProfileEntry[] {
  if (!bedMesh) return []
  const profiles = bedMesh.profiles ?? {}
  const out: MeshProfileEntry[] = Object.entries(profiles).map(([name, p]) => {
    const flat = p.points.flat()
    return { name, active: name === bedMesh.profile_name, adaptive: false, range: flat.length ? Math.max(...flat) - Math.min(...flat) : 0 }
  })
  if (bedMesh.profile_name && !(bedMesh.profile_name in profiles)) {
    const flat = bedMesh.mesh_matrix?.flat() ?? []
    out.push({ name: bedMesh.profile_name, active: true, adaptive: true, range: flat.length ? Math.max(...flat) - Math.min(...flat) : 0 })
  }
  return out.sort((a, b) => +(b.name === 'default') - +(a.name === 'default') || a.name.localeCompare(b.name))
}

export type MeshGrid = {
  /** z by [row][col]; row 0 is the front of the bed (mesh_min Y). */
  z: number[][]
  rows: number
  cols: number
  min: [number, number]
  max: [number, number]
}

export type MeshStats = { min: number; max: number; mid: number; range: number; mean: number; deviation: number }

/** A matrix placed on the bed, or null when it's too small to draw (Klipper meshes are at least 3 × 3). */
export function meshGrid(matrix: number[][] | undefined, min: [number, number] | undefined, max: [number, number] | undefined): MeshGrid | null {
  if (!matrix || !min || !max || matrix.length < 2 || !matrix[0] || matrix[0].length < 2) return null
  if (matrix.some((r) => r.length !== matrix[0].length)) return null
  return { z: matrix, rows: matrix.length, cols: matrix[0].length, min, max }
}

export function gridX(g: MeshGrid, col: number) {
  return g.min[0] + (col * (g.max[0] - g.min[0])) / (g.cols - 1)
}
export function gridY(g: MeshGrid, row: number) {
  return g.min[1] + (row * (g.max[1] - g.min[1])) / (g.rows - 1)
}

/** Range, extremes, and spread. `mid` is 0 when the mesh crosses zero, as Fluidd centres its flat plane. */
export function meshStats(z: number[][]): MeshStats {
  const flat = z.flat()
  if (!flat.length) return { min: 0, max: 0, mid: 0, range: 0, mean: 0, deviation: 0 }
  const min = Math.min(...flat)
  const max = Math.max(...flat)
  const mean = flat.reduce((a, v) => a + v, 0) / flat.length
  const deviation = Math.sqrt(flat.reduce((a, v) => a + (v - mean) ** 2, 0) / flat.length)
  return { min, max, mid: min <= 0 && max >= 0 ? 0 : (min + max) / 2, range: max - min, mean, deviation }
}

/** The nearest grid point to a bed position. */
export function nearestCell(g: MeshGrid, x: number, y: number) {
  const col = Math.round(((x - g.min[0]) / (g.max[0] - g.min[0] || 1)) * (g.cols - 1))
  const row = Math.round(((y - g.min[1]) / (g.max[1] - g.min[1] || 1)) * (g.rows - 1))
  return { row: Math.min(g.rows - 1, Math.max(0, row)), col: Math.min(g.cols - 1, Math.max(0, col)) }
}

/**
 * Colour and box ranges, as Fluidd's Bed mesh card sets them. `colorScale` widens the colour range around the mesh
 * (0 fits it to the mesh); `boxScale` is the height of the Z axis in mm. A bed soak change map is centred on no change.
 */
export function meshScale(stats: MeshStats, colorScale: number, boxScale: number | 'fit', centred = false) {
  if (centred) {
    const extent = Math.max(Math.abs(stats.min), Math.abs(stats.max), 0.005)
    const box = Math.ceil(extent * 2 * 100) / 100
    return { zMin: -extent, zMax: extent, zBoxMin: -box, zBoxMax: box }
  }
  const zMin = colorScale === 0 ? stats.min : -Math.abs(colorScale / 2 - stats.min)
  const zMax = colorScale === 0 ? stats.max : colorScale / 2 + stats.max
  if (boxScale === 'fit') {
    const pad = Math.max(stats.range * 0.15, 0.005)
    return { zMin, zMax, zBoxMin: stats.min - pad, zBoxMax: stats.max + pad }
  }
  return { zMin, zMax, zBoxMin: -Math.abs(stats.mid - boxScale / 2), zBoxMax: stats.mid + boxScale / 2 }
}

// Fluidd's bed mesh colour ramp, low (blue) to high (red).
const RAMP = ['#313695', '#4575b4', '#74add1', '#abd9e9', '#e0f3f8', '#ffffbf', '#fee090', '#fdae61', '#f46d43', '#d73027', '#a50026'].map((h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)))

export function meshColor(z: number, zMin: number, zMax: number): string {
  const t = zMax > zMin ? Math.min(1, Math.max(0, (z - zMin) / (zMax - zMin))) : 0.5
  const f = t * (RAMP.length - 1)
  const i = Math.min(RAMP.length - 2, Math.floor(f))
  const k = f - i
  const c = RAMP[i].map((v, j) => Math.round(v + (RAMP[i + 1][j] - v) * k))
  return `rgb(${c.join(',')})`
}

export const MESH_GRADIENT = `linear-gradient(90deg, ${RAMP.map((c) => `rgb(${c.join(',')})`).join(', ')})`

/** A round step for axis ticks near `raw`: 1, 2.5, 5, or 10 times a power of ten. */
export function niceStep(raw: number) {
  const p = 10 ** Math.floor(Math.log10(raw))
  const n = raw / p
  return (n < 1.5 ? 1 : n < 3.5 ? 2.5 : n < 7.5 ? 5 : 10) * p
}

/** A point marked on the mesh, in bed coordinates. */
export type MeshMarker = { x: number; y: number; label: string }

/** A mesh shown on the Bed mesh tab in place of the loaded one, such as a bed soak change map. */
export type MeshPreview = { title: string; mesh_min: [number, number]; mesh_max: [number, number]; matrix: number[][]; markers: MeshMarker[] }

export const meshPreview = createStore<MeshPreview | null>(null)

/** How the mesh is drawn. Kept for the session, as Fluidd keeps its mesh settings. The Z axis starts fitted to the
 * mesh, where Fluidd starts at 2 mm and most beds look flat. */
export type MeshView = { mode: '3d' | 'map'; matrix: MatrixType; wireframe: boolean; flat: boolean; colorScale: number; boxScale: number | 'fit' }
export const meshView = createStore<MeshView>({ mode: '3d', matrix: 'mesh_matrix', wireframe: false, flat: false, colorScale: 0.2, boxScale: 'fit' })

/** Bed size for the axes: the toolhead's limits, as Fluidd uses, else the mesh's own. */
export function bedBounds(axisMin: number[] | undefined, axisMax: number[] | undefined, g: MeshGrid) {
  if (axisMin && axisMax && axisMax[0] > axisMin[0] && axisMax[1] > axisMin[1])
    return { minX: Math.min(axisMin[0], g.min[0]), maxX: Math.max(axisMax[0], g.max[0]), minY: Math.min(axisMin[1], g.min[1]), maxY: Math.max(axisMax[1], g.max[1]) }
  return { minX: g.min[0], maxX: g.max[0], minY: g.min[1], maxY: g.max[1] }
}
export type BedBounds = ReturnType<typeof bedBounds>

/**
 * The bed's corners named as you stand at the printer's door. On every supported printer, Max 4, Plus 5, and Smart 5,
 * X runs left to right and Y front to back, so 0,0 is at the front left.
 */
export function bedCorners(bed: BedBounds) {
  return [
    { x: bed.minX, y: bed.minY, label: t('tune.mesh.corner_front_left') },
    { x: bed.maxX, y: bed.minY, label: t('tune.mesh.corner_front_right') },
    { x: bed.maxX, y: bed.maxY, label: t('tune.mesh.corner_back_right') },
    { x: bed.minX, y: bed.maxY, label: t('tune.mesh.corner_back_left') },
  ]
}

export const fmtMm = (v: number, digits = 3) => `${v < 0 ? '−' : ''}${Math.abs(v).toFixed(digits)}`

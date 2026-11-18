// What the preview derives from a parsed file: layers, bounds, colors, and file-position lookups. Ported from the
// Valve Oil Fluidd fork's src/store/gcodePreview/getters.ts as plain functions; keep behavior in step with it.
import type { BBox, ColorMode, ColorStop, ExtrusionRole as ExtrusionRoleType, Layer, MoveStore, ParsedGcode, Tool, ValueRange } from './types'
import { ExtrusionRole, MoveFlags } from './types'
import { binarySearch, rangeColors } from './gcode-preview'
import decimalRound from './decimal-round'

const defaultColors = ['#1fb0ff', '#ff5252', '#D67600', '#830EE3', '#B366F2', '#E06573', '#E38819', '#795548', '#607D8B']
const lightDefaultColors = Object.freeze(['#000', ...defaultColors])
const darkDefaultColors = Object.freeze(['#FFF', ...defaultColors])

const roleColors = [
  '#E6B3B3', // None
  '#FFE64D', // Perimeter
  '#FF7D38', // ExternalPerimeter
  '#1F1FFF', // OverhangPerimeter
  '#B03029', // InternalInfill
  '#9654CC', // SolidInfill
  '#F04040', // TopSolidInfill
  '#FF8C69', // Ironing
  '#4D80BA', // BridgeInfill
  '#FFFFFF', // GapFill
  '#00876E', // Skirt
  '#00FF00', // SupportMaterial
  '#008000', // SupportMaterialInterface
  '#B3E3AB', // WipeTower
  '#5ED194', // Custom
]

const groupColors = (prefix: string, colors: readonly string[]) =>
  Object.freeze(
    colors.reduce<Record<string, string>>((groups, color, index) => {
      groups[`${prefix}${index}`] = color
      return groups
    }, {}),
  )

const darkRoleColors = groupColors('R', roleColors)
// white gap fill disappears on a light background
const lightRoleColors = groupColors(
  'R',
  roleColors.map((color, role) => (role === ExtrusionRole.GapFill ? '#333333' : color)),
)
const speedColors = groupColors('S', rangeColors)
const fanColors = groupColors('F', rangeColors)

/** Fluidd's names for each feature type (app.gcode.role). */
export const roleNames: Record<ExtrusionRoleType, string> = {
  [ExtrusionRole.None]: 'Unknown',
  [ExtrusionRole.Perimeter]: 'Perimeter',
  [ExtrusionRole.ExternalPerimeter]: 'External perimeter',
  [ExtrusionRole.OverhangPerimeter]: 'Overhang perimeter',
  [ExtrusionRole.InternalInfill]: 'Internal infill',
  [ExtrusionRole.SolidInfill]: 'Solid infill',
  [ExtrusionRole.TopSolidInfill]: 'Top solid infill',
  [ExtrusionRole.Ironing]: 'Ironing',
  [ExtrusionRole.BridgeInfill]: 'Bridge infill',
  [ExtrusionRole.GapFill]: 'Gap fill',
  [ExtrusionRole.Skirt]: 'Skirt/Brim',
  [ExtrusionRole.SupportMaterial]: 'Support material',
  [ExtrusionRole.SupportMaterialInterface]: 'Support material interface',
  [ExtrusionRole.WipeTower]: 'Wipe tower',
  [ExtrusionRole.Custom]: 'Custom',
}

// same stops as PrusaSlicer's range legend: one per distinct value when there are at most 2
const rangeStops = (range: Readonly<ValueRange> | null, scale: number): readonly ColorStop[] => {
  if (!range) return []

  const min = range.min * scale
  const max = range.max * scale

  if (range.count <= 2) {
    return [
      { value: min, color: rangeColors[0] },
      { value: max, color: rangeColors[rangeColors.length - 1] },
    ].slice(0, range.count)
  }

  const step = (max - min) / (rangeColors.length - 1)

  return rangeColors.map((color, index) => ({ value: min + step * index, color }))
}

/** mm/s stops for the speed legend. */
export const speedStops = (range: Readonly<ValueRange> | null) => rangeStops(range, 1 / 60)

/** Percent stops for the fan legend. */
export const fanStops = (range: Readonly<ValueRange> | null) => rangeStops(range, 100 / 255)

/**
 * The parser's layers, or, for files without SET_PRINT_STATS_INFO layer changes, layers found from Z changes of at
 * least `minLayerHeight` between extruding moves.
 */
export function getLayers(parsed: Pick<ParsedGcode, 'layers' | 'moves'>, minLayerHeight: number): readonly Layer[] {
  if (parsed.layers.length) return parsed.layers

  const output: Layer[] = []
  const { x, y, z, flags, filePosition, length } = parsed.moves

  let currentZ = NaN
  let zStart = 0
  let zLast = NaN
  let zNext = NaN

  for (let index = 0; index < length; index++) {
    if (z[index] !== currentZ) {
      currentZ = z[index]
      zStart = index
    }

    const moveFlags = flags[index]

    if (
      (moveFlags & MoveFlags.Extruding) !== 0 &&
      (Number.isNaN(zLast) || currentZ < zLast || currentZ >= zNext) &&
      (index === 0 || x[index] !== x[index - 1] || y[index] !== y[index - 1] || (moveFlags & MoveFlags.Arc) !== 0)
    ) {
      zLast = currentZ
      zNext = decimalRound(currentZ + minLayerHeight, 4)

      output.push({ z: decimalRound(currentZ, 4), move: zStart, filePosition: filePosition[zStart] })
    }
  }

  // If moves exist but there are no layers, add a single "default" layer at z=0
  // This can happen for gcode that only contains travel moves (eg: 2d plotters without Z or E steppers)
  if (output.length === 0 && length) {
    output.push({ z: 0, move: 0, filePosition: filePosition[0] })
  }

  return output
}

/** The parser's print bounds, or the extent of the moves between the first and last layer. */
export function getBounds(parsed: Pick<ParsedGcode, 'bounds' | 'moves'>, layers: readonly Layer[]): BBox {
  if (parsed.bounds != null) return parsed.bounds

  const { x, y, length } = parsed.moves

  // ignore first and last layer (priming and parking), unless doing so would
  // leave nothing to measure
  const useLayerRange = layers.length > 1 && layers[1].move < layers[layers.length - 1].move

  const start = useLayerRange ? layers[1].move : 0
  const end = useLayerRange ? Math.min(layers[layers.length - 1].move, length) : length

  let minX = Number.POSITIVE_INFINITY
  let maxX = Number.NEGATIVE_INFINITY
  let minY = Number.POSITIVE_INFINITY
  let maxY = Number.NEGATIVE_INFINITY

  for (let index = start; index < end; index++) {
    const moveX = x[index]
    const moveY = y[index]

    if (moveX < minX) minX = moveX
    if (moveX > maxX) maxX = moveX
    if (moveY < minY) minY = moveY
    if (moveY > maxY) maxY = moveY
  }

  return {
    x: {
      min: Number.isFinite(minX) ? decimalRound(minX, 4) : 0,
      max: Number.isFinite(maxX) ? decimalRound(maxX, 4) : 0,
    },
    y: {
      min: Number.isFinite(minY) ? decimalRound(minY, 4) : 0,
      max: Number.isFinite(maxY) ? decimalRound(maxY, 4) : 0,
    },
  }
}

/** Each tool's color: the file's filament colors where the slicer wrote them, then Fluidd's defaults. */
export function getToolColors(tools: readonly number[], fileColors: readonly string[], isDark: boolean): Record<Tool, string> {
  const [toolIndexes, colorsFromFileMetadata] = tools.length === 0 ? [[0], []] : [tools, fileColors]
  const defaults = isDark ? darkDefaultColors : lightDefaultColors
  const out: Record<Tool, string> = {}

  toolIndexes.forEach((toolIndex, index) => {
    out[`T${toolIndex}`] = colorsFromFileMetadata[toolIndex] || defaults[index - colorsFromFileMetadata.length] || defaults[0]
  })

  return out
}

/** Colors for buildLayerPaths' extrusion groups in a color mode. */
export function getExtrusionColors(mode: ColorMode, toolColors: Record<Tool, string>, isDark: boolean): Record<string, string> {
  switch (mode) {
    case 'role':
      return isDark ? darkRoleColors : lightRoleColors
    case 'speed':
      return speedColors
    case 'fan':
      return fanColors
    default:
      return toolColors
  }
}

/** The last move at or before a file position (Klipper's virtual_sdcard.file_position). */
export function moveIndexByFilePosition(moves: MoveStore, filePosition: number): number {
  if (filePosition <= 0) return 0

  return Math.max(0, binarySearch(moves.length, (index) => filePosition - moves.filePosition[index]))
}

/** The layer holding a file position. */
export function layerByFilePosition(layers: readonly Layer[], filePosition: number): number {
  if (filePosition <= 0) return 0

  return Math.max(0, binarySearch(layers.length, (index) => filePosition - layers[index].filePosition))
}

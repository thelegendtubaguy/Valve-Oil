// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/util/qidi-box-flow.ts at
// github.com/thelegendtubaguy/fluidd qidi@6405679. Keep behavior in step with the fork.
import { QIDI_BOX_EXTERNAL_SLOT } from './qidi-box'

// The QIDI Box flow diagram: Box units above (and, when they don't fit in one row, below) the filament path
// hub → toolhead sensor → extruder → nozzle. Connectors run between named ports: `inlet:<slot>` on each Box slot's
// inlet sensor, `ext` where the external spool leaves its frame, and `hub`, `th`, `ex`, `noz` along the path.

export type QidiBoxFlowDirection = 'h' | 'v'

export interface QidiBoxFlowConnection {
  key: string;
  via: string[];
  // Curves leave and enter each port horizontally ('h') or vertically ('v').
  dir: QidiBoxFlowDirection;
  // The slot whose filament the strand carries; null for the path guide.
  slot: number | null;
}

export interface QidiBoxFlowLayout {
  spoolSize: number;
  // The loaded-filament summary sits above the diagram rather than beside the path.
  narrow: boolean;
  // Too narrow for the diagram: the path sits on top and the units wrap below it, without slot strands.
  compact: boolean;
  // Units above the path, then below it (mirrored to face it); Box numbers, with 0 for the external spool.
  top: number[];
  bottom: number[];
}

export const QIDI_BOX_FLOW_NARROW_WIDTH = 520
export const QIDI_BOX_FLOW_UNIT_GAP = 10
export const QIDI_BOX_FLOW_EXTERNAL_MIN_WIDTH = 80

const PATH = ['hub', 'th', 'ex', 'noz']

// A spool column is the spool plus its hover padding; a unit is its columns plus padding.
export const qidiBoxFlowColumnWidth = (spoolSize: number): number => spoolSize + 6

export const qidiBoxFlowUnitWidth = (box: number, spoolSize: number): number => box === 0
  ? Math.max(qidiBoxFlowColumnWidth(spoolSize) + 16, QIDI_BOX_FLOW_EXTERNAL_MIN_WIDTH)
  : 4 * qidiBoxFlowColumnWidth(spoolSize) + 3 * 4 + 16

// One row when every unit fits; otherwise the first half of the Boxes above the path and the rest, with the
// external spool, below it. When neither fits, the compact layout.
export const qidiBoxFlowLayout = (width: number, boxCount: number): QidiBoxFlowLayout => {
  const narrow = width <= QIDI_BOX_FLOW_NARROW_WIDTH
  const spoolSize = narrow ? (boxCount >= 3 ? 32 : 44) : (boxCount >= 2 ? 44 : 52)
  const units = [...Array.from({ length: boxCount }, (_, i) => i + 1), 0]
  const fits = (row: number[]) => row.reduce((sum, box) => sum + qidiBoxFlowUnitWidth(box, spoolSize), 0) +
    (row.length - 1) * QIDI_BOX_FLOW_UNIT_GAP <= width

  if (fits(units)) return { spoolSize, narrow, compact: false, top: units, bottom: [] }

  const half = Math.ceil(boxCount / 2)
  const top = units.slice(0, half)
  const bottom = units.slice(half)

  if (fits(top) && fits(bottom)) return { spoolSize, narrow, compact: false, top, bottom }

  return { spoolSize: 44, narrow: true, compact: true, top: units, bottom: [] }
}

// The path guide, plus a strand for each filament the sensors see: the hub's from its slot inlet, and the
// toolhead's along the path to the nozzle. The external spool skips the hub. The compact layout draws only the path.
export const qidiBoxFlowConnections = (hubSlot: number | null, toolheadSlot: number | null, compact: boolean): QidiBoxFlowConnection[] => {
  const connections: QidiBoxFlowConnection[] = [{ key: 'guide', via: PATH, dir: 'h', slot: null }]

  if (hubSlot != null && hubSlot !== QIDI_BOX_EXTERNAL_SLOT && !compact) {
    connections.push({ key: 'hub', via: [`inlet:${hubSlot}`, 'hub'], dir: 'v', slot: hubSlot })
  }

  if (toolheadSlot === QIDI_BOX_EXTERNAL_SLOT) {
    if (!compact) connections.push({ key: 'external', via: ['ext', 'th'], dir: 'v', slot: toolheadSlot })

    connections.push({ key: 'toolhead', via: ['th', 'ex', 'noz'], dir: 'h', slot: toolheadSlot })
  } else if (toolheadSlot != null) {
    connections.push({ key: 'toolhead', via: PATH, dir: 'h', slot: toolheadSlot })
  }

  return connections
}

const round = (value: number) => Math.round(value * 10) / 10

// SVG path data through the points, each step a cubic curve that leaves and enters along the direction, or a
// straight line when the points already line up.
export const qidiBoxFlowPath = (points: [number, number][], dir: QidiBoxFlowDirection): string => {
  if (points.length < 2) return ''

  let d = `M${round(points[0][0])} ${round(points[0][1])}`

  for (let i = 1; i < points.length; i++) {
    const [x1, y1] = points[i - 1].map(round)
    const [x2, y2] = points[i].map(round)

    if (dir === 'h' && Math.abs(y2 - y1) > 0.5) {
      const mid = round((x1 + x2) / 2)

      d += ` C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}`
    } else if (dir === 'v' && Math.abs(x2 - x1) > 0.5) {
      const mid = round((y1 + y2) / 2)

      d += ` C${x1} ${mid} ${x2} ${mid} ${x2} ${y2}`
    } else {
      d += ` L${x2} ${y2}`
    }
  }

  return d
}

import { describe, expect, it } from 'vitest'
import { MoveFlags, type MoveStore } from '../types'
import { getLayers, getToolColors, layerByFilePosition, moveIndexByFilePosition } from '../model'
import { mergePreviewSettings, PREVIEW_DEFAULTS } from '../preview-settings'

const store = (moves: { x: number; y: number; z: number; e?: boolean }[]): MoveStore => ({
  x: Float32Array.from(moves, (m) => m.x),
  y: Float32Array.from(moves, (m) => m.y),
  z: Float32Array.from(moves, (m) => m.z),
  i: new Float32Array(0),
  j: new Float32Array(0),
  tool: new Uint8Array(moves.length),
  flags: Uint8Array.from(moves, (m) => (m.e ? MoveFlags.Extruding : 0)),
  role: new Uint8Array(moves.length),
  speed: new Uint16Array(moves.length),
  fan: new Uint8Array(moves.length),
  filePosition: Uint32Array.from(moves, (_, i) => i * 10),
  length: moves.length,
})

describe('getLayers', () => {
  it('keeps the layers the file marks', () => {
    const layers = [{ z: 0.2, move: 0, filePosition: 0 }]
    expect(getLayers({ layers, moves: store([]) }, 0.1)).toBe(layers)
  })

  it('finds layers from Z for files without layer changes, ignoring hops below the minimum height', () => {
    const moves = store([
      { x: 0, y: 0, z: 0.2 },
      { x: 10, y: 0, z: 0.2, e: true },
      { x: 10, y: 0, z: 0.25 }, // z hop
      { x: 20, y: 0, z: 0.25, e: true },
      { x: 20, y: 0, z: 0.4 },
      { x: 30, y: 0, z: 0.4, e: true },
    ])
    expect(getLayers({ layers: [], moves }, 0.1)).toEqual([
      { z: 0.2, move: 0, filePosition: 0 },
      { z: 0.4, move: 4, filePosition: 40 },
    ])
  })

  it('gives travel-only files one layer', () => {
    expect(getLayers({ layers: [], moves: store([{ x: 1, y: 1, z: 0 }]) }, 0.1)).toHaveLength(1)
  })
})

describe('file positions', () => {
  const moves = store([0, 1, 2, 3].map((x) => ({ x, y: 0, z: 0 })))
  const layers = [
    { z: 0.2, move: 0, filePosition: 0 },
    { z: 0.4, move: 2, filePosition: 20 },
  ]

  it.each([
    [0, 0, 0],
    [15, 1, 0],
    [20, 2, 1],
    [999, 3, 1],
  ])('maps position %i to move %i on layer %i', (position, move, layer) => {
    expect(moveIndexByFilePosition(moves, position)).toBe(move)
    expect(layerByFilePosition(layers, position)).toBe(layer)
  })

  it('stays on the first move and layer while the print is still in the file’s header', () => {
    // A file's header and thumbnails come before its first move.
    const late = { ...moves, filePosition: Uint32Array.from([100, 110, 120, 130]) }
    const lateLayers = layers.map((l) => ({ ...l, filePosition: l.filePosition + 100 }))
    expect(moveIndexByFilePosition(late, 50)).toBe(0)
    expect(layerByFilePosition(lateLayers, 50)).toBe(0)
  })

  it('answers 0 for a file with no moves or layers', () => {
    expect(moveIndexByFilePosition(store([]), 50)).toBe(0)
    expect(layerByFilePosition([], 50)).toBe(0)
  })
})

describe('getToolColors', () => {
  it('uses the file colours by tool number, then the defaults', () => {
    expect(getToolColors([0, 1], ['#F2EFE8', '#2F6BFF'], true)).toEqual({ T0: '#F2EFE8', T1: '#2F6BFF' })
    expect(getToolColors([0, 1], [], true)).toEqual({ T0: '#FFF', T1: '#1fb0ff' })
    expect(getToolColors([], [], false)).toEqual({ T0: '#000' })
  })
})

describe('mergePreviewSettings', () => {
  it('keeps stored values of the right type over Fluidd defaults', () => {
    const s = mergePreviewSettings({ autoZoom: true, colorMode: 'role', showMoves: 'yes', flip: { horizontal: true }, extra: 1 })
    expect(s.autoZoom).toBe(true)
    expect(s.colorMode).toBe('role')
    expect(s.showMoves).toBe(PREVIEW_DEFAULTS.showMoves)
    expect(s.flip).toEqual({ horizontal: true, vertical: true })
    expect(mergePreviewSettings({ colorMode: 'rainbow' }).colorMode).toBe('tool')
  })
})

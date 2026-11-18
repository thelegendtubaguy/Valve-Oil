import { describe, it, expect } from 'vitest'
import {
  bedSoakChangeGrid,
  bedSoakMilestones,
  bedSoakProblemPoints,
  bedSoakRepeatedProblemPoints,
  bedSoakStartCommand,
  parseBedSoakRun,
  type BedSoakRun
} from '../tltg-bed-soak'

const settings = {
  type: 'settings',
  version: 1,
  started: 1762790400,
  bed: 100,
  chamber: 45,
  nozzle: 140,
  probe_count: 3,
  mesh_min: [15, 15],
  mesh_max: [375, 375],
  time_limit: 90,
  stop_when_settled: true,
  keep_heating: false,
  limits: { speed: 0.002, out_of_line: 0.015, jump: 0.015, bed_wait: 1, chamber_wait: 3 },
  check_point: [195, 195]
}

const cold = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]

const mesh = (index: number, time: number, matrix: number[][], extra: Record<string, unknown> = {}) => ({
  type: 'mesh',
  index,
  start: time - 60,
  end: time + 60,
  time,
  matrix,
  offset: 0,
  height: 0,
  shape: 0,
  speed: null,
  used: true,
  retried: [],
  out_of_line: [],
  check: { before: null, after: null, jump: null },
  temps: [[time - 5, 30, 25], [time, 31, 25.5]],
  ...extra
})

const runFile = (...lines: unknown[]) => lines.map(line => typeof line === 'string' ? line : JSON.stringify(line)).join('\n')

const parse = (...lines: unknown[]): BedSoakRun => {
  const run = parseBedSoakRun(runFile(...lines))

  if (run == null) throw new Error('run not read')

  return run
}

describe('parseBedSoakRun', () => {
  it('needs a settings line', () => {
    expect(parseBedSoakRun(runFile(mesh(0, -60, cold)))).toBeNull()
  })

  it('reads a run Klipper is still writing, skipping the half-written line', () => {
    const run = parse(
      settings,
      mesh(0, -60, cold),
      { type: 'heaters_on', time: 0, epoch: 1762790600 },
      mesh(1, 120, cold),
      '{"type":"mesh","index":2,"matr'
    )

    expect(run.heatersOn).toBe(1762790600)
    expect(run.meshes.map(m => m.index)).toEqual([0, 1])
    expect(run.temps.map(sample => sample[0])).toEqual([-65, -60, 115, 120])
    expect(run.result).toBeNull()
  })

  it('stops using a mesh once a jump is confirmed in it', () => {
    const run = parse(
      settings,
      mesh(0, -60, cold),
      mesh(1, 120, cold),
      mesh(2, 240, cold),
      { type: 'jump', mesh: 1, time: 110, size: 0.055 }
    )

    expect(run.meshes.map(m => m.used)).toEqual([true, false, true])
    expect(run.jumps).toEqual([{ mesh: 1, time: 110, size: 0.055 }])
  })

  it('reads the result line', () => {
    const run = parse(
      settings,
      mesh(0, -60, cold),
      {
        type: 'result',
        state: 'finished',
        reason: 'The bed stopped moving',
        ended: 1762795000,
        milestones: { bed: 570, chamber: 960, chamber_max: null, settled: 1530, ready: 1530 },
        meshes: 1,
        jumps: [],
        problem_points: [],
        warning: false
      }
    )

    expect(run.result?.state).toBe('finished')
    expect(bedSoakMilestones(run).settled).toBe(1530)
  })
})

describe('bedSoakMilestones', () => {
  it('takes the temperature milestones from the saved temperatures of a run without a result', () => {
    const run = parse(
      settings,
      mesh(0, -60, cold, { temps: [[-60, 25, 25]] }),
      mesh(1, 600, cold, { temps: [[300, 98.9, 40], [400, 99.1, 41], [500, 100, 42]] })
    )

    expect(bedSoakMilestones(run)).toEqual({ bed: 400, chamber: 500, chamber_max: null, settled: null, ready: null })
  })
})

describe('bedSoakChangeGrid', () => {
  const run = parse(
    settings,
    mesh(0, -60, [[0.1, 0.1, 0.1], [0.1, 0.1, 0.1], [0.1, 0.1, 0.1]]),
    mesh(1, 120, [[0.2, 0.2, 0.2], [0.2, 0.2, 0.2], [0.2, 0.2, 0.5]], { offset: 0.05 })
  )

  it('is the change from the cold mesh with the jump correction taken off', () => {
    const grid = bedSoakChangeGrid(run, run.meshes[1], true)

    expect(grid?.[0][0]).toBeCloseTo(0.05)
    expect(grid?.[2][2]).toBeCloseTo(0.35)
  })

  it('leaves out the whole-bed change for the shape', () => {
    const grid = bedSoakChangeGrid(run, run.meshes[1], false) ?? []

    expect(grid.flat().reduce((sum, value) => sum + value, 0)).toBeCloseTo(0)
    expect(grid[2][2] - grid[0][0]).toBeCloseTo(0.3)
  })
})

describe('bedSoakProblemPoints', () => {
  const run = parse(
    settings,
    mesh(0, -60, cold, { retried: [{ x: 195, y: 15, row: 0, col: 1, tries: 3, spread: 0.054 }] }),
    mesh(1, 120, cold, { retried: [{ x: 195, y: 15, row: 0, col: 1, tries: 2, spread: 0.031 }] }),
    mesh(2, 240, cold, { out_of_line: [{ row: 2, col: 2, change: -0.022 }] })
  )

  it('collects each point with the meshes it was flagged in', () => {
    expect(bedSoakProblemPoints(run)).toEqual([
      { row: 0, col: 1, x: 195, y: 15, retried: [0, 1], maxTries: 3, maxSpread: 0.054, outOfLine: [], maxChange: 0 },
      { row: 2, col: 2, x: 375, y: 375, retried: [], maxTries: 0, maxSpread: 0, outOfLine: [2], maxChange: -0.022 }
    ])
  })

  it('warns only about points flagged in more than one mesh', () => {
    expect(bedSoakRepeatedProblemPoints(run).map(point => [point.x, point.y])).toEqual([[195, 15]])
  })
})

describe('bedSoakStartCommand', () => {
  it('leaves the chamber out when it is not heated', () => {
    expect(bedSoakStartCommand({ bed: 100, chamber: 0, probeCount: 5, timeLimit: 90, stopWhenSettled: true, keepHeating: false }))
      .toBe('TLTG_BED_SOAK BED=100 PROBE_COUNT=5 TIME_LIMIT=90 STOP_WHEN_SETTLED=1 KEEP_HEATING=0')
  })

  it('passes every setting', () => {
    expect(bedSoakStartCommand({ bed: 110, chamber: 60, probeCount: 9, timeLimit: 240, stopWhenSettled: false, keepHeating: true }))
      .toBe('TLTG_BED_SOAK BED=110 CHAMBER=60 PROBE_COUNT=9 TIME_LIMIT=240 STOP_WHEN_SETTLED=0 KEEP_HEATING=1')
  })
})

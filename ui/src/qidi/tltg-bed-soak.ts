// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/util/tltg-bed-soak.ts at
// github.com/thelegendtubaguy/fluidd qidi@6405679, with the fork's Klipper status types for tltg_bed_soak
// (src/typings/klipper.d.ts) added at the end. Keep behavior in step with the fork.
/**
 * Run files of QIDI Max 4 Optimized's bed soak test (tltg_bed_soak).
 *
 * Each run is a JSON Lines file in the config root's tltg_bed_soak folder: a
 * settings line, one line per mesh, jump lines when a probe jump is confirmed,
 * and a result line once the test ends. Lengths are in mm, speeds in mm/min,
 * and times in seconds after the heaters turned on, so the cold mesh's are
 * negative.
 */

export const BED_SOAK_ROOT = 'config'
export const BED_SOAK_FOLDER = 'tltg_bed_soak'
export const BED_SOAK_PATH = `${BED_SOAK_ROOT}/${BED_SOAK_FOLDER}`

/** Chart and status colours, checked against the dark card surface #28282B */
export const BedSoakColors = Object.freeze({
  bed: '#3987e5',
  chamber: '#d95926',
  height: '#199e70',
  shape: '#c98500',
  warning: '#fab219',
  good: '#0ca30c'
})

/** Run colours when comparing runs */
export const BedSoakRunColors = Object.freeze(['#3987e5', '#d95926', '#199e70'])

/** Meshes in a row under the speed limit for the bed to count as stopped moving */
export const BED_SOAK_SETTLED_MESHES = 3

export type BedSoakEndState = 'finished' | 'stopped' | 'failed'

export interface BedSoakSettings {
  started: number;
  bed: number;
  chamber: number | null;
  nozzle: number | null;
  probe_count: number;
  mesh_min: [number, number];
  mesh_max: [number, number];
  time_limit: number | null;
  stop_when_settled: boolean;
  keep_heating: boolean;
  limits: Partial<TltgBedSoakLimits>;
  check_point: [number, number] | null;
}

export interface BedSoakRetriedPoint {
  x: number;
  y: number;
  row: number;
  col: number;
  tries: number;
  spread: number;
}

export interface BedSoakOutOfLinePoint {
  row: number;
  col: number;
  change: number;
}

/** [seconds after heaters on, bed °C, chamber °C] */
export type BedSoakTemp = [number, number | null, number | null]

export interface BedSoakMesh {
  index: number;
  start: number | null;
  end: number | null;
  time: number;
  matrix: number[][];
  offset: number;
  height: number | null;
  shape: number | null;
  speed: number | null;
  /** false when the mesh was measured but isn't used, such as after a probe jump */
  used: boolean;
  retried: BedSoakRetriedPoint[];
  out_of_line: BedSoakOutOfLinePoint[];
  check: {
    before: number | null;
    after: number | null;
    jump: number | null;
  } | null;
}

export interface BedSoakJump {
  mesh: number;
  time: number;
  size: number;
}

export interface BedSoakResult {
  state: BedSoakEndState;
  reason: string;
  ended: number | null;
  milestones: TltgBedSoakMilestones;
  meshes: number;
  warning: boolean;
}

export interface BedSoakRun {
  settings: BedSoakSettings;
  /** Epoch the heaters turned on, null until they have */
  heatersOn: number | null;
  meshes: BedSoakMesh[];
  jumps: BedSoakJump[];
  temps: BedSoakTemp[];
  /** null while the test runs, or when Klipper stopped before the test ended */
  result: BedSoakResult | null;
}

export interface BedSoakProblemPoint {
  row: number;
  col: number;
  x: number;
  y: number;
  /** Meshes the point needed a probe retry in */
  retried: number[];
  maxTries: number;
  maxSpread: number;
  /** Meshes the point moved out of line with the rest of the bed in */
  outOfLine: number[];
  maxChange: number;
}

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' &&
  value != null &&
  !Array.isArray(value)
)

const num = (value: unknown): number | null => (
  typeof value === 'number' && Number.isFinite(value)
    ? value
    : null
)

const pair = (value: unknown): [number, number] | null => {
  if (!Array.isArray(value) || value.length < 2) return null

  const x = num(value[0])
  const y = num(value[1])

  return x != null && y != null ? [x, y] : null
}

const isMatrix = (value: unknown): value is number[][] => (
  Array.isArray(value) &&
  value.length > 0 &&
  value.every(row => (
    Array.isArray(row) &&
    row.length === (value[0] as unknown[]).length &&
    row.every(z => num(z) != null)
  ))
)

const parseMilestones = (value: unknown): TltgBedSoakMilestones => {
  const milestones = isRecord(value) ? value : {}

  return {
    bed: num(milestones.bed),
    chamber: num(milestones.chamber),
    chamber_max: num(milestones.chamber_max),
    settled: num(milestones.settled),
    ready: num(milestones.ready)
  }
}

const parseSettings = (line: Record<string, unknown>): BedSoakSettings | null => {
  const bed = num(line.bed)
  const probeCount = num(line.probe_count)
  const meshMin = pair(line.mesh_min)
  const meshMax = pair(line.mesh_max)

  if (bed == null || probeCount == null || meshMin == null || meshMax == null) return null

  return {
    started: num(line.started) ?? 0,
    bed,
    chamber: num(line.chamber),
    nozzle: num(line.nozzle),
    probe_count: probeCount,
    mesh_min: meshMin,
    mesh_max: meshMax,
    time_limit: num(line.time_limit),
    stop_when_settled: line.stop_when_settled === true,
    keep_heating: line.keep_heating === true,
    limits: isRecord(line.limits) ? line.limits as Partial<TltgBedSoakLimits> : {},
    check_point: pair(line.check_point)
  }
}

const parseRetried = (value: unknown): BedSoakRetriedPoint[] => (
  Array.isArray(value)
    ? value
      .filter(isRecord)
      .map(point => ({
        x: num(point.x) ?? 0,
        y: num(point.y) ?? 0,
        row: num(point.row) ?? -1,
        col: num(point.col) ?? -1,
        tries: num(point.tries) ?? 0,
        spread: num(point.spread) ?? 0
      }))
      .filter(point => point.row >= 0 && point.col >= 0)
    : []
)

const parseOutOfLine = (value: unknown): BedSoakOutOfLinePoint[] => (
  Array.isArray(value)
    ? value
      .filter(isRecord)
      .map(point => ({
        row: num(point.row) ?? -1,
        col: num(point.col) ?? -1,
        change: num(point.change) ?? 0
      }))
      .filter(point => point.row >= 0 && point.col >= 0)
    : []
)

const parseTemps = (value: unknown): BedSoakTemp[] => (
  Array.isArray(value)
    ? value
      .filter((sample): sample is unknown[] => Array.isArray(sample) && num(sample[0]) != null)
      .map(sample => [num(sample[0]) ?? 0, num(sample[1]), num(sample[2])])
    : []
)

const parseMesh = (line: Record<string, unknown>): BedSoakMesh | null => {
  const index = num(line.index)
  const time = num(line.time)

  if (index == null || time == null || !isMatrix(line.matrix)) return null

  const check = isRecord(line.check)
    ? {
        before: num(line.check.before),
        after: num(line.check.after),
        jump: num(line.check.jump)
      }
    : null

  return {
    index,
    start: num(line.start),
    end: num(line.end),
    time,
    matrix: line.matrix,
    offset: num(line.offset) ?? 0,
    height: num(line.height),
    shape: num(line.shape),
    speed: num(line.speed),
    used: line.used !== false,
    retried: parseRetried(line.retried),
    out_of_line: parseOutOfLine(line.out_of_line),
    check
  }
}

const parseJump = (value: unknown): BedSoakJump | null => {
  if (!isRecord(value)) return null

  const mesh = num(value.mesh)
  const time = num(value.time)
  const size = num(value.size)

  return mesh != null && time != null && size != null
    ? { mesh, time, size }
    : null
}

const isEndState = (value: unknown): value is BedSoakEndState => (
  value === 'finished' || value === 'stopped' || value === 'failed'
)

/**
 * Reads a run file. Lines that can't be read, such as one Klipper is still
 * writing, are skipped. Returns null without a settings line.
 */
export const parseBedSoakRun = (text: string): BedSoakRun | null => {
  let settings: BedSoakSettings | null = null
  let heatersOn: number | null = null
  let result: BedSoakResult | null = null
  const meshes = new Map<number, BedSoakMesh>()
  const jumps = new Map<number, BedSoakJump>()
  const temps: BedSoakTemp[] = []

  for (const raw of text.split('\n')) {
    const text = raw.trim()

    if (text === '') continue

    let line: unknown

    try {
      line = JSON.parse(text)
    } catch {
      continue
    }

    if (!isRecord(line)) continue

    switch (line.type) {
      case 'settings':
        if (settings == null) settings = parseSettings(line)
        break

      case 'heaters_on':
        heatersOn = num(line.epoch)
        break

      case 'mesh': {
        const mesh = parseMesh(line)

        if (mesh) {
          meshes.set(mesh.index, mesh)
          temps.push(...parseTemps(line.temps))
        }
        break
      }

      case 'jump': {
        const jump = parseJump(line)

        if (jump) jumps.set(jump.mesh, jump)
        break
      }

      case 'result':
        if (isEndState(line.state)) {
          if (Array.isArray(line.jumps)) {
            for (const jump of line.jumps.map(parseJump)) {
              if (jump && !jumps.has(jump.mesh)) jumps.set(jump.mesh, jump)
            }
          }

          result = {
            state: line.state,
            reason: typeof line.reason === 'string' ? line.reason : '',
            ended: num(line.ended),
            milestones: parseMilestones(line.milestones),
            meshes: num(line.meshes) ?? meshes.size,
            warning: line.warning === true
          }
        }
        break
    }
  }

  if (settings == null) return null

  return {
    settings,
    heatersOn,
    meshes: [...meshes.values()]
      .sort((a, b) => a.index - b.index)
      .map(mesh => jumps.has(mesh.index) ? { ...mesh, used: false } : mesh),
    jumps: [...jumps.values()].sort((a, b) => a.mesh - b.mesh),
    temps: temps.sort((a, b) => a[0] - b[0]),
    result
  }
}

export const bedSoakColdMesh = (run: BedSoakRun): BedSoakMesh | undefined => (
  run.meshes.find(mesh => mesh.index === 0)
)

/** Nozzle position of a mesh point; rows run along Y from mesh_min, columns along X */
export const bedSoakPointPosition = (settings: BedSoakSettings, rows: number, cols: number, row: number, col: number) => {
  const [minX, minY] = settings.mesh_min
  const [maxX, maxY] = settings.mesh_max

  return {
    x: cols > 1 ? minX + col * (maxX - minX) / (cols - 1) : minX,
    y: rows > 1 ? minY + row * (maxY - minY) / (rows - 1) : minY
  }
}

/**
 * A mesh's change from the cold mesh in mm, with jumps corrected. Without the
 * height, the whole bed's average change is taken out, leaving the shape change.
 */
export const bedSoakChangeGrid = (run: BedSoakRun, mesh: BedSoakMesh, withHeight: boolean): number[][] | null => {
  const cold = bedSoakColdMesh(run)

  if (
    cold == null ||
    cold.matrix.length !== mesh.matrix.length ||
    cold.matrix[0].length !== mesh.matrix[0].length
  ) {
    return null
  }

  const change = mesh.matrix.map((row, r) => row.map((z, c) => z - mesh.offset - cold.matrix[r][c]))

  if (withHeight) return change

  const values = change.flat()
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length

  return change.map(row => row.map(value => value - mean))
}

/** Points flagged in any mesh, with the meshes they were flagged in */
export const bedSoakProblemPoints = (run: BedSoakRun, upToIndex = Infinity): BedSoakProblemPoint[] => {
  const points = new Map<string, BedSoakProblemPoint>()
  const rows = run.meshes[0]?.matrix.length ?? run.settings.probe_count
  const cols = run.meshes[0]?.matrix[0].length ?? run.settings.probe_count

  const pointAt = (row: number, col: number) => {
    const key = `${row}:${col}`
    let point = points.get(key)

    if (point == null) {
      point = {
        row,
        col,
        ...bedSoakPointPosition(run.settings, rows, cols, row, col),
        retried: [],
        maxTries: 0,
        maxSpread: 0,
        outOfLine: [],
        maxChange: 0
      }
      points.set(key, point)
    }

    return point
  }

  for (const mesh of run.meshes) {
    if (mesh.index > upToIndex) break

    for (const retried of mesh.retried) {
      const point = pointAt(retried.row, retried.col)

      point.x = retried.x
      point.y = retried.y
      point.retried.push(mesh.index)
      point.maxTries = Math.max(point.maxTries, retried.tries)
      point.maxSpread = Math.max(point.maxSpread, retried.spread)
    }

    for (const outOfLine of mesh.out_of_line) {
      const point = pointAt(outOfLine.row, outOfLine.col)

      point.outOfLine.push(mesh.index)
      if (Math.abs(outOfLine.change) > Math.abs(point.maxChange)) {
        point.maxChange = outOfLine.change
      }
    }
  }

  return [...points.values()]
    .map(point => ({
      ...point,
      retried: [...new Set(point.retried)],
      outOfLine: [...new Set(point.outOfLine)]
    }))
    .sort((a, b) => (
      (b.retried.length + b.outOfLine.length) - (a.retried.length + a.outOfLine.length) ||
      a.row - b.row ||
      a.col - b.col
    ))
}

/** Points flagged in more than one mesh, which the results warn about */
export const bedSoakRepeatedProblemPoints = (run: BedSoakRun) => (
  bedSoakProblemPoints(run)
    .filter(point => new Set([...point.retried, ...point.outOfLine]).size > 1)
)

/** The first time after the heaters turned on that a temperature reached a value */
const firstReached = (temps: BedSoakTemp[], column: 1 | 2, value: number): number | null => (
  temps.find(sample => sample[0] >= 0 && sample[column] != null && (sample[column] ?? 0) >= value)?.[0] ?? null
)

/**
 * The run's milestones: the result's, or for a run without one, the
 * temperature milestones its saved temperatures show.
 */
export const bedSoakMilestones = (run: BedSoakRun): TltgBedSoakMilestones => {
  if (run.result) return run.result.milestones

  const { bed, chamber, limits } = run.settings

  return {
    bed: firstReached(run.temps, 1, bed - (limits.bed_wait ?? 1)),
    chamber: chamber ? firstReached(run.temps, 2, chamber - (limits.chamber_wait ?? 3)) : null,
    chamber_max: null,
    settled: null,
    ready: null
  }
}

/** Temperatures when the heaters turned on: the last sample before, or the first one */
export const bedSoakStartTemps = (run: BedSoakRun): BedSoakTemp | null => (
  [...run.temps].reverse().find(sample => sample[0] <= 0) ??
  run.temps[0] ??
  null
)

/** Used meshes after the bed reached temperature with speed under the limit, counted back from the latest */
export const bedSoakMeshesUnderLimit = (run: BedSoakRun, bedTime: number | null, limit: number): number => {
  if (bedTime == null) return 0

  let count = 0

  for (const mesh of [...run.meshes].reverse()) {
    if (!mesh.used) continue
    if (mesh.time < bedTime || mesh.speed == null || mesh.speed >= limit) break
    count++
  }

  return count
}

export interface BedSoakStartOptions {
  bed: number;
  chamber: number | null;
  probeCount: number;
  timeLimit: number;
  stopWhenSettled: boolean;
  keepHeating: boolean;
}

export const bedSoakStartCommand = (options: BedSoakStartOptions): string => [
  'TLTG_BED_SOAK',
  `BED=${Math.round(options.bed)}`,
  ...(options.chamber != null && options.chamber > 0 ? [`CHAMBER=${Math.round(options.chamber)}`] : []),
  `PROBE_COUNT=${Math.round(options.probeCount)}`,
  `TIME_LIMIT=${Math.round(options.timeLimit)}`,
  `STOP_WHEN_SETTLED=${options.stopWhenSettled ? 1 : 0}`,
  `KEEP_HEATING=${options.keepHeating ? 1 : 0}`
].join(' ')

/** Start time, bed and chamber targets from a run file's name, YYYYMMDD-HHMMSS-bed<B>[-chamber<C>].jsonl */
export const parseBedSoakFilename = (filename: string) => {
  const match = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-bed(\d+)(?:-chamber(\d+))?\.jsonl$/.exec(filename)

  if (match == null) return null

  const [, year, month, day, hours, minutes, seconds, bed, chamber] = match

  return {
    started: new Date(+year, +month - 1, +day, +hours, +minutes, +seconds).getTime() / 1000,
    bed: +bed,
    chamber: chamber != null ? +chamber : null
  }
}

const hexToRgb = (hex: string) => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16))

/** Diverging heat map colour: lower toward blue, higher toward red, the neutral colour at no change */
export const bedSoakHeatmapColor = (value: number, maxAbs: number, neutral: string) => {
  const end = hexToRgb(value < 0 ? '#3987e5' : '#e66767')
  const mid = hexToRgb(neutral)
  const t = maxAbs > 0 ? Math.min(1, Math.abs(value) / maxAbs) : 0

  return `rgb(${mid.map((v, i) => Math.round(v + (end[i] - v) * t)).join(',')})`
}

// ---- tltg_bed_soak status, from the fork's src/typings/klipper.d.ts ----

export type TltgBedSoakRunState = 'idle' | 'running' | 'finished' | 'stopped' | 'failed'

export type TltgBedSoakStep = '' | 'nozzle' | 'home' | 'wipe' | 'z_tilt' | 'home_z' | 'cold_mesh' | 'mesh' | 'finishing'

export interface TltgBedSoakSettings {
  bed: number;
  chamber: number | null;
  nozzle: number;
  probe_count: number;
  time_limit: number;
  stop_when_settled: boolean;
  keep_heating: boolean;
}

// Lengths in mm, speed in mm/min
export interface TltgBedSoakLimits {
  speed: number;
  out_of_line: number;
  jump: number;
  bed_wait: number;
  chamber_wait: number;
}

// Seconds after the heaters turned on, and °C for chamber_max
export interface TltgBedSoakMilestones {
  bed: number | null;
  chamber: number | null;
  chamber_max: number | null;
  settled: number | null;
  ready: number | null;
}

export interface TltgBedSoakLatest {
  index: number;
  time: number;
  height: number;
  shape: number;
  speed: number | null;
  used: boolean;
}

// QIDI Max 4 Optimized's bed soak test. Grids are in the run file, not here.
export interface TltgBedSoakState {
  state: TltgBedSoakRunState;
  step: TltgBedSoakStep;
  stop_requested: boolean;
  chamber_available: boolean;
  settings: TltgBedSoakSettings | null;
  limits: TltgBedSoakLimits;
  started: number | null;
  heaters_on: number | null;
  elapsed: number | null;
  milestones: TltgBedSoakMilestones;
  meshes: number;
  latest: TltgBedSoakLatest | null;
  mesh_progress: number | null;
  problems: {
    points: number;
    jumps: number;
  };
  file: string;
  reason: string;
}

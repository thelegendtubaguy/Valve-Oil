import { describe, it, expect } from 'vitest'
import { consoleLines, lastProbeAccuracy, shaperResults, tiltScrews } from './calibrate'
import { meshProfiles, meshScale, meshStats } from './mesh'

const lines = (...texts: string[]) => consoleLines(texts.map((text, time) => ({ text, time })))

describe('lastProbeAccuracy', () => {
  it('reads the latest result with where it probed', () => {
    const r = lastProbeAccuracy(
      lines(
        '// PROBE_ACCURACY at X:195.000 Y:195.000 Z:10.000 (samples=20 retract=2.000 speed=5.0 lift_speed=5.0)\n',
        '// probe at 195.000,195.000 is z=0.012500',
        '// probe accuracy results: maximum 0.012500, minimum 0.002500, range 0.010000, average 0.007875, median 0.007500, standard deviation 0.002658',
      ),
    )
    expect(r).toMatchObject({ range: 0.01, deviation: 0.002658, samples: 20, at: 'X195 Y195' })
  })

  it('is null without a result', () => {
    expect(lastProbeAccuracy(lines('// probe at 195.000,195.000 is z=0.012500'))).toBeNull()
  })
})

describe('shaperResults', () => {
  it('keeps the latest run of each axis with its fitted shapers and recommendation', () => {
    const r = shaperResults(
      lines(
        '// Calculating the best input shaper parameters for x axis',
        "// Fitted shaper 'zv' frequency = 58.6 Hz (vibrations = 6.2%, smoothing ~= 0.044)",
        "// To avoid too much smoothing with 'zv', suggested max_accel <= 14000 mm/sec^2",
        "// Fitted shaper 'mzv' frequency = 54.2 Hz (vibrations = 1.1%, smoothing ~= 0.063)",
        "// To avoid too much smoothing with 'mzv', suggested max_accel <= 9600 mm/sec^2",
        '// Recommended shaper_type_x = mzv, shaper_freq_x = 54.2 Hz',
        '// Calculating the best input shaper parameters for y axis',
        "// Fitted shaper 'mzv' frequency = 41.0 Hz (vibrations = 0.9%, smoothing ~= 0.110)",
      ),
    )
    expect(r.x.recommended).toEqual({ type: 'mzv', freq: 54.2 })
    expect(r.x.fitted.map((f) => [f.type, f.maxAccel])).toEqual([
      ['zv', 14000],
      ['mzv', 9600],
    ])
    expect(r.y.recommended).toBeNull()
  })
})

describe('tiltScrews', () => {
  const config = { screw1: [30, 30], screw1_name: 'Front left', screw2: [345, 30], screw2_name: 'Front right' }

  it('lists the screws before the first run, when Klipper reports an empty list', () => {
    expect(tiltScrews(config, { results: [] }).map((s) => [s.name, s.adjust])).toEqual([
      ['Front left', null],
      ['Front right', null],
    ])
  })

  it('gives each screw its turn and its height against the base screw', () => {
    const [base, other] = tiltScrews(config, {
      results: { screw1: { z: 2.5, sign: 'CW', adjust: '00:00', is_base: true }, screw2: { z: 2.4, sign: 'CCW', adjust: '01:15', is_base: false } },
    })
    expect(base.isBase).toBe(true)
    expect(other).toMatchObject({ sign: 'CCW', minutes: 75 })
    expect(other.relativeZ).toBeCloseTo(-0.1)
  })
})

describe('meshProfiles', () => {
  it('lists an unsaved loaded mesh as adaptive, default first', () => {
    const profiles = meshProfiles({
      profile_name: 'adaptive',
      mesh_matrix: [
        [0, 0.1],
        [0.2, 0.3],
      ],
      profiles: { pei: { points: [[0, 0.05]], mesh_params: {} }, default: { points: [[-0.1, 0.1]], mesh_params: {} } },
    })
    expect(profiles.map((p) => [p.name, p.active, p.adaptive])).toEqual([
      ['default', false, false],
      ['adaptive', true, true],
      ['pei', false, false],
    ])
    expect(profiles[0].range).toBeCloseTo(0.2)
  })
})

describe('meshScale', () => {
  it('centres a bed soak change map on no change', () => {
    const s = meshScale(meshStats([[-0.02, 0.01]]), 0.2, 2, true)
    expect(s.zMin).toBeCloseTo(-0.02)
    expect(s.zMax).toBeCloseTo(0.02)
  })

  it('widens the colour range by the colour scale, as Fluidd does', () => {
    const s = meshScale(meshStats([[-0.05, 0.05]]), 0.2, 2)
    expect(s.zMin).toBeCloseTo(-0.15)
    expect(s.zMax).toBeCloseTo(0.15)
    expect([s.zBoxMin, s.zBoxMax]).toEqual([-1, 1])
  })
})

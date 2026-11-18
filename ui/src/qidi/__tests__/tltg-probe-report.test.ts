import { describe, it, expect } from 'vitest'
import tltgProbeReportPoints from '../tltg-probe-report'

const point = { x: 195, y: 105, tries: 3, spread: 0.054 }

describe('tltgProbeReportPoints', () => {
  it.each([
    ['absent', undefined],
    ['describing a mesh that is no longer loaded', { loaded: false, points: [point] }],
    ['without points', { loaded: true, points: null }]
  ])('marks nothing when the report is %s', (_, state) => {
    expect(tltgProbeReportPoints(state)).toEqual([])
  })

  it('marks the loaded mesh\'s retried points, skipping malformed ones', () => {
    expect(tltgProbeReportPoints({ loaded: true, points: [point, { x: 15 }] })).toEqual([point])
  })
})

import { describe, it, expect } from 'vitest'
import voProbeReportPoints from '../vo-probe-report'

const point = { x: 195, y: 105, tries: 3, spread: 0.054 }

describe('voProbeReportPoints', () => {
  it.each([
    ['absent', undefined],
    ['describing a mesh that is no longer loaded', { loaded: false, points: [point] }],
    ['without points', { loaded: true, points: null }]
  ])('marks nothing when the report is %s', (_, state) => {
    expect(voProbeReportPoints(state)).toEqual([])
  })

  it('marks the loaded mesh\'s retried points, skipping malformed ones', () => {
    expect(voProbeReportPoints({ loaded: true, points: [point, { x: 15 }] })).toEqual([point])
  })
})

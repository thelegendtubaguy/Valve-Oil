import { describe, expect, it } from 'vitest'
import { bucketPoints, type ChartPoint } from './temp-chart-points'

// A nozzle holding 220° with a reading that wobbles by up to a degree each second.
const history = Array.from({ length: 1300 }, (_, k) => 220 + Math.sin(k * 2.7) + ((k * 7919) % 13) / 13 - 0.5)

/** The points a 600-sample window ending at sample `end` draws, keyed by their sample number in the whole history. */
function drawn(end: number, stride: number, how: 'mean' | 'last' = 'mean') {
  const first = end - 600
  return new Map(bucketPoints(history.slice(first, end), first, stride, how).map((p: ChartPoint) => [first + p.i, p.v]))
}

describe('temperature chart buckets', () => {
  it.each([2, 4, 5])('draws the same points for full buckets after the window slides a second (stride %i)', (stride) => {
    for (const end of [700, 701, 1003]) {
      const before = drawn(end, stride)
      const after = drawn(end + 1, stride)
      // The window's first and last buckets can be partial; every bucket between them is unchanged.
      const keys = [...before.keys()].slice(1, -1)
      expect(keys.length).toBeGreaterThan(100)
      for (const k of keys) expect(after.get(k)).toBe(before.get(k))
    }
  })

  it('leaves a gap only where every reading in a bucket is 0, and keeps a target step a step', () => {
    const temps = [200, 201, 0, 0, 0, 0, 0, 202]
    expect(bucketPoints(temps, 0, 2, 'mean').map((p) => p.v)).toEqual([200.5, null, null, 202])
    const targets = [0, 0, 220, 220, 220, 0]
    expect(bucketPoints(targets, 1, 2, 'last').map((p) => p.v)).toEqual([null, 220, 220, null])
  })
})

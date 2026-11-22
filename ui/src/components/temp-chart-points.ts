/** A chart point: `i` is a sample index into the drawn values (a bucket's middle), `v` is null for a gap. */
export type ChartPoint = { i: number; v: number | null }

/**
 * Thins a heater's samples to one point per `stride` samples. Buckets are fixed to each sample's number in the whole
 * history (`first` is the number of `vals[0]`), not to its place in the window, so as the window slides a sample a
 * second, a full bucket draws the same point every time instead of a different sample of a noisy reading. Temperatures
 * draw a bucket's mean; targets draw its last value, so a target's step stays a step. Values of 0 or less are gaps:
 * Moonraker records 0 °C while Klipper restarts, and a target of 0 means the heater is off.
 */
export function bucketPoints(vals: number[], first: number, stride: number, how: 'mean' | 'last'): ChartPoint[] {
  const out: ChartPoint[] = []
  for (let i = 0; i < vals.length; ) {
    const end = Math.min(vals.length, i + stride - (((first + i) % stride) + stride) % stride)
    if (how === 'last') out.push({ i: end - 1, v: vals[end - 1] > 0 ? vals[end - 1] : null })
    else {
      let sum = 0
      let n = 0
      for (let k = i; k < end; k++)
        if (vals[k] > 0) {
          sum += vals[k]
          n++
        }
      out.push({ i: (i + end - 1) / 2, v: n ? sum / n : null })
    }
    i = end
  }
  return out
}

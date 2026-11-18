// Results Klipper reports only in the console (probe accuracy, input shaper calibration), and screws_tilt_adjust's
// status, read into something the Calibrate tab can draw.

/** Console entries as lines, without Klipper's "// " prefix. An entry can hold several lines. */
export function consoleLines(entries: { text: string; time: number }[]): { text: string; time: number }[] {
  return entries.flatMap((e) => e.text.split('\n').map((l) => ({ text: l.replace(/^\/\/\s?/, '').trim(), time: e.time })))
}

export type ProbeAccuracy = { maximum: number; minimum: number; range: number; average: number; median: number; deviation: number; samples: number | null; at: string | null; time: number }

const NUM = '(-?[\\d.]+)'
const ACCURACY = new RegExp(`probe accuracy results: maximum ${NUM}, minimum ${NUM}, range ${NUM}, average ${NUM}, median ${NUM}, standard deviation ${NUM}`, 'i')

/** The latest PROBE_ACCURACY result, with where it probed and how many samples, when the console still has them. */
export function lastProbeAccuracy(lines: { text: string; time: number }[]): ProbeAccuracy | null {
  for (let i = lines.length - 1; i >= 0; i--) {
    const m = ACCURACY.exec(lines[i].text)
    if (!m) continue
    let samples: number | null = null
    let at: string | null = null
    for (let j = i - 1; j >= 0 && j >= i - 400; j--) {
      const s = /PROBE_ACCURACY at X:(-?[\d.]+) Y:(-?[\d.]+) Z:(-?[\d.]+) \(samples=(\d+)/.exec(lines[j].text)
      if (s) {
        samples = Number(s[4])
        at = `X${Math.round(Number(s[1]))} Y${Math.round(Number(s[2]))}`
        break
      }
    }
    const [maximum, minimum, range, average, median, deviation] = m.slice(1).map(Number)
    return { maximum, minimum, range, average, median, deviation, samples, at, time: lines[i].time }
  }
  return null
}

export type FittedShaper = { type: string; freq: number; vibrations: number; smoothing: number; maxAccel: number | null }
export type ShaperResult = { axis: string; fitted: FittedShaper[]; recommended: { type: string; freq: number } | null; time: number }

/** The latest SHAPER_CALIBRATE results for each axis: every shaper it fitted and the one it recommends. */
export function shaperResults(lines: { text: string; time: number }[]): Record<string, ShaperResult> {
  const out: Record<string, ShaperResult> = {}
  let current: ShaperResult | null = null
  for (const { text, time } of lines) {
    let m = /^Calculating the best input shaper parameters for (\w+) axis/i.exec(text)
    if (m) {
      current = { axis: m[1].toLowerCase(), fitted: [], recommended: null, time }
      out[current.axis] = current
      continue
    }
    if (!current) continue
    m = /^Fitted shaper '(\w+)' frequency = ([\d.]+) Hz \(vibrations = ([\d.]+)%, smoothing ~= ([\d.]+)\)/.exec(text)
    if (m) {
      current.fitted.push({ type: m[1], freq: Number(m[2]), vibrations: Number(m[3]), smoothing: Number(m[4]), maxAccel: null })
      continue
    }
    m = /^To avoid too much smoothing with '(\w+)', suggested max_accel <= ([\d.]+)/.exec(text)
    if (m) {
      const f = current.fitted.find((s) => s.type === m![1])
      if (f) f.maxAccel = Number(m[2])
      continue
    }
    m = /^Recommended shaper_type_(\w+) = (\w+), shaper_freq_\w+ = ([\d.]+) Hz/.exec(text)
    if (m && m[1].toLowerCase() === current.axis) current.recommended = { type: m[2], freq: Number(m[3]) }
  }
  return out
}

export type TiltScrew = { key: string; name: string; x: number; y: number; z: number | null; sign: 'CW' | 'CCW' | null; adjust: string | null; minutes: number | null; isBase: boolean; relativeZ: number | null }

/**
 * Screws from [screws_tilt_adjust] with SCREWS_TILT_CALCULATE's results. Klipper reports results keyed screw1…,
 * and an empty list before the first run.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function tiltScrews(config: Record<string, any> | undefined, status: Record<string, any> | undefined): TiltScrew[] {
  if (!config) return []
  const results = status?.results && typeof status.results === 'object' && !Array.isArray(status.results) ? status.results : {}
  const screws: TiltScrew[] = []
  for (let i = 1; Array.isArray(config[`screw${i}`]); i++) {
    const key = `screw${i}`
    const r = results[key]
    const [h, m] = typeof r?.adjust === 'string' ? r.adjust.split(':').map(Number) : [NaN, NaN]
    screws.push({
      key,
      name: config[`${key}_name`] || `Screw ${i}`,
      x: config[key][0],
      y: config[key][1],
      z: typeof r?.z === 'number' ? r.z : null,
      sign: r?.sign === 'CW' || r?.sign === 'CCW' ? r.sign : null,
      adjust: typeof r?.adjust === 'string' ? r.adjust : null,
      minutes: Number.isFinite(h) && Number.isFinite(m) ? h * 60 + m : null,
      isBase: !!r?.is_base,
      relativeZ: null,
    })
  }
  const base = screws.find((s) => s.isBase)?.z
  if (base != null) for (const s of screws) s.relativeZ = s.z != null ? s.z - base : null
  return screws
}

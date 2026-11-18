import { createStore } from '../../lib/store'
import { printer } from '../../state/printer'
import { consoleLines } from './calibrate'

// Probe accuracy and input shaper results reach the UI only as console lines, and a shaper calibration prints over a
// hundred "Testing frequency" lines per axis, more than the console keeps. So the result lines are kept here as they
// arrive, from page load on.
const RESULT = /probe accuracy results|^PROBE_ACCURACY at|input shaper parameters for|^Fitted shaper|^To avoid too much smoothing|^Recommended shaper_type|^Resonance test with/i

export const calibrationLog = createStore<{ text: string; time: number }[]>([])

let lastId = 0
printer.subscribe(() => {
  const c = printer.get().console
  const last = c[c.length - 1]
  if (!last || last.id <= lastId) return
  const fresh = c.filter((l) => l.id > lastId && l.kind === 'response')
  lastId = last.id
  const lines = consoleLines(fresh).filter((l) => RESULT.test(l.text))
  if (!lines.length) return
  // A reconnect reloads the console from Moonraker's store, repeating lines already kept.
  calibrationLog.set((prev) => {
    const seen = new Set(prev.map((l) => `${l.time}|${l.text}`))
    return [...prev, ...lines.filter((l) => !seen.has(`${l.time}|${l.text}`))].slice(-400)
  })
})

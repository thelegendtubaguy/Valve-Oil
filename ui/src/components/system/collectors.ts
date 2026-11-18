// Names and paths in Fluidd's collector expressions, e.g. printer.motion_report.live_velocity or
// printer['tmc2209 extruder'].run_current.

/** The Klipper objects a collector reads, so ones this page doesn't subscribe to can be queried. */
export function referencedObjects(collector: string): string[] {
  const out = new Set<string>()
  for (const m of collector.matchAll(/\bprinter\s*\.\s*([A-Za-z_$][\w$]*)/g)) out.add(m[1])
  for (const m of collector.matchAll(/\bprinter\s*\[\s*(['"`])(.+?)\1\s*\]/g)) out.add(m[2])
  return [...out]
}

/** The collector path to a value in the state explorer: printer.toolhead.position[0], printer['mcu THR'].mcu_version. */
export function collectorPath(segments: (string | number)[]): string {
  let out = 'printer'
  for (const s of segments) {
    if (typeof s === 'number') out += `[${s}]`
    else if (/^[A-Za-z_$][\w$]*$/.test(s)) out += `.${s}`
    else out += `['${s.replace(/\\/g, '\\\\').replace(/'/g, "\\'")}']`
  }
  return out
}

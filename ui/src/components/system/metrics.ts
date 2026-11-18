import { useSyncExternalStore } from 'react'
import { printer, type HostStats, type Status } from '../../state/printer'
import { createBuffer, klipperChartValues, mcuChartValues, pushSample, round2, type SeriesBuffer } from './chart-data'

// Rolling buffers behind the System screen's charts, sampled once a second from the printer state whether or not the
// screen is open, so the charts already have history when it's opened (as Fluidd's do).

/** Seconds of history the system charts keep, as Fluidd's CHART_SYSTEM_RETENTION. */
export const SYSTEM_RETENTION = 600

export type SystemBuffers = { host: SeriesBuffer; klipper: SeriesBuffer; mcus: Record<string, SeriesBuffer> }
const buffers: SystemBuffers = { host: createBuffer(), klipper: createBuffer(), mcus: {} }

let rev = 0
const subs = new Set<() => void>()
const bump = () => {
  rev++
  for (const fn of subs) fn()
}
/** Re-renders once a second with the buffers (mutated in place). */
export function useSystemBuffers(): SystemBuffers {
  useSyncExternalStore(
    (fn) => (subs.add(fn), () => subs.delete(fn)),
    () => rev,
  )
  return buffers
}

function hostValues(host: HostStats | null, stats: Status[string] | undefined): Record<string, number> {
  const out: Record<string, number> = {}
  if (host?.cpu != null) out.cpu = round2(host.cpu)
  if (host?.cpuTemp != null) out.cpu_temp = round2(host.cpuTemp)
  if (host?.moonrakerCpu != null) out.moonraker = round2(host.moonrakerCpu)
  if (host?.memory?.total) out.memused = round2(((host.memory.total - host.memory.available) / host.memory.total) * 100)
  else if (typeof stats?.memavail === 'number' && totalMemoryKb) out.memused = round2(((totalMemoryKb - stats.memavail) / totalMemoryKb) * 100)
  return out
}

/** machine.system_info's total memory, for memory use when Moonraker hasn't sent its own figures yet. */
let totalMemoryKb = 0
export function setTotalMemory(kb: number | undefined) {
  if (kb) totalMemoryKb = kb
}

let lastCpu: { cputime: number; at: number } | null = null
function sample() {
  const s = printer.get()
  if (s.conn !== 'open') return
  const now = Date.now()
  const host = hostValues(s.host, s.status.system_stats)
  if (Object.keys(host).length) pushSample(buffers.host, now, host, SYSTEM_RETENTION)
  if (s.klippy === 'ready') {
    const stats = s.status.system_stats
    const seconds = lastCpu ? (now - lastCpu.at) / 1000 : 0
    pushSample(buffers.klipper, now, klipperChartValues(stats, lastCpu?.cputime ?? null, seconds, s.status.tltg_klipper_health), SYSTEM_RETENTION)
    lastCpu = typeof stats?.cputime === 'number' ? { cputime: stats.cputime, at: now } : null
    for (const name of s.objects) {
      if (name !== 'mcu' && !name.startsWith('mcu ')) continue
      const v = mcuChartValues(s.status[name]?.last_stats)
      if (!Object.keys(v).length) continue
      pushSample((buffers.mcus[name] ??= createBuffer()), now, v, SYSTEM_RETENTION)
    }
  } else lastCpu = null
  bump()
}

if (typeof window !== 'undefined') setInterval(sample, 1000)

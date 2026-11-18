import { useState } from 'react'
import { BedSoakColors, type BedSoakRun, type BedSoakTemp, type TltgBedSoakMilestones } from '../../qidi/tltg-bed-soak'
import { BedSoakChart, type BedSoakChartEvent, type BedSoakChartSeries } from './BedSoakChart'
import { bedSoakClock, bedSoakMeshName } from './bed-soak-format'

/** Temperature, bed movement, and still-moving charts of one run, on one time axis with a shared crosshair. */
export function BedSoakRunCharts({ run, temps, milestones, elapsed }: { run: BedSoakRun; temps: BedSoakTemp[]; milestones: TltgBedSoakMilestones; elapsed?: number }) {
  const [hoverX, setHoverX] = useState<number | null>(null)
  const speedLimit = (run.settings.limits.speed ?? 0.002) * 1000
  const used = run.meshes.filter((m) => m.used)

  const times = [0, ...(temps.length ? [temps[0][0], temps[temps.length - 1][0]] : []), ...run.meshes.flatMap((m) => [m.start ?? m.time, m.end ?? m.time]), elapsed ?? 0].map((t) => t / 60)
  const xMin = Math.floor(Math.min(...times))
  const xMax = Math.max(xMin + 10, Math.ceil(Math.max(...times) / 5) * 5)
  const timeLabel = (minutes: number) => (minutes < 0 ? `${bedSoakClock(-minutes * 60)} before heaters on` : `${bedSoakClock(minutes * 60)} after heaters on`)

  const degrees = (v: number) => `${v.toFixed(1)} °C`
  const temperature: BedSoakChartSeries[] = [{ name: 'Bed', color: BedSoakColors.bed, format: degrees, data: temps.filter((s) => s[1] != null).map((s) => ({ x: s[0] / 60, y: s[1] ?? 0 })) }]
  if (temps.some((s) => s[2] != null)) temperature.push({ name: 'Chamber', color: BedSoakColors.chamber, format: degrees, data: temps.filter((s) => s[2] != null).map((s) => ({ x: s[0] / 60, y: s[2] ?? 0 })) })

  const temperatureEvents: BedSoakChartEvent[] = []
  if (run.heatersOn != null || elapsed != null) temperatureEvents.push({ x: 0, label: 'heaters on' })
  if (milestones.bed != null) temperatureEvents.push({ x: milestones.bed / 60, label: 'bed at temp' })
  if (milestones.chamber != null) temperatureEvents.push({ x: milestones.chamber / 60, label: 'chamber at temp' })

  const um = (v: number) => `${Math.round(v)} µm`
  const point = (time: number, value: number | null, index: number) => ({ x: time / 60, y: (value ?? 0) * 1000, name: bedSoakMeshName(index) })
  const movement: BedSoakChartSeries[] = [
    { name: 'Height', color: BedSoakColors.height, markers: true, format: um, data: used.filter((m) => m.height != null).map((m) => point(m.time, m.height, m.index)) },
    { name: 'Shape', color: BedSoakColors.shape, markers: true, format: um, data: used.filter((m) => m.shape != null).map((m) => point(m.time, m.shape, m.index)) },
  ]
  const movementEvents: BedSoakChartEvent[] = run.jumps.map((j) => ({ x: j.time / 60, label: `probe jump, mesh ${j.mesh} not used` }))
  if (milestones.settled != null) movementEvents.push({ x: milestones.settled / 60, label: 'stopped moving' })
  movementEvents.sort((a, b) => a.x - b.x)

  const speed: BedSoakChartSeries[] = [
    {
      name: 'Change',
      color: 'var(--ink-2)',
      markers: true,
      format: (v) => `${v.toFixed(1)} µm per min`,
      data: used.filter((m) => m.speed != null).map((m) => ({ x: m.time / 60, y: (m.speed ?? 0) * 1000, name: bedSoakMeshName(m.index) })),
    },
  ]

  const common = { xMin, xMax, timeLabel, hoverX, onHover: setHoverX }
  return (
    <div className="grid min-w-0 gap-3">
      <BedSoakChart title="Temperature" unit="°C" series={temperature} events={temperatureEvents} references={[{ y: run.settings.bed }, ...(run.settings.chamber ? [{ y: run.settings.chamber }] : [])]} height={150} {...common} />
      <BedSoakChart title="Bed movement since the cold mesh" unit="µm" series={movement} events={movementEvents} height={150} {...common} />
      <BedSoakChart
        title="Still moving"
        note="change between meshes"
        unit="µm per min"
        series={speed}
        yMin={0}
        events={movementEvents}
        references={[{ y: speedLimit, label: 'stopped-moving limit', color: BedSoakColors.good }]}
        band={{ from: 0, to: speedLimit, color: 'rgba(12,163,12,0.12)' }}
        height={120}
        {...common}
      />
    </div>
  )
}

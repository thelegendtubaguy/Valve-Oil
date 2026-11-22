import { useState } from 'react'
import { BedSoakColors, type BedSoakRun, type BedSoakTemp, type VoBedSoakMilestones } from '../../qidi/vo-bed-soak'
import { BedSoakChart, type BedSoakChartEvent, type BedSoakChartSeries } from './BedSoakChart'
import { bedSoakClock, bedSoakMeshName } from './bed-soak-format'
import { t } from '../../i18n'

/** Temperature, bed movement, and still-moving charts of one run, on one time axis with a shared crosshair. */
export function BedSoakRunCharts({ run, temps, milestones, elapsed }: { run: BedSoakRun; temps: BedSoakTemp[]; milestones: VoBedSoakMilestones; elapsed?: number }) {
  const [hoverX, setHoverX] = useState<number | null>(null)
  const speedLimit = (run.settings.limits.speed ?? 0.002) * 1000
  const used = run.meshes.filter((m) => m.used)

  const times = [0, ...(temps.length ? [temps[0][0], temps[temps.length - 1][0]] : []), ...run.meshes.flatMap((m) => [m.start ?? m.time, m.end ?? m.time]), elapsed ?? 0].map((s) => s / 60)
  const xMin = Math.floor(Math.min(...times))
  const xMax = Math.max(xMin + 10, Math.ceil(Math.max(...times) / 5) * 5)
  const timeLabel = (minutes: number) => (minutes < 0 ? t('tune.soak.time_before', { time: bedSoakClock(-minutes * 60) }) : t('tune.soak.time_after', { time: bedSoakClock(minutes * 60) }))

  const degrees = (v: number) => `${v.toFixed(1)} °C`
  const temperature: BedSoakChartSeries[] = [{ name: t('tune.soak.bed'), color: BedSoakColors.bed, format: degrees, data: temps.filter((s) => s[1] != null).map((s) => ({ x: s[0] / 60, y: s[1] ?? 0 })) }]
  if (temps.some((s) => s[2] != null)) temperature.push({ name: t('tune.soak.chamber'), color: BedSoakColors.chamber, format: degrees, data: temps.filter((s) => s[2] != null).map((s) => ({ x: s[0] / 60, y: s[2] ?? 0 })) })

  const temperatureEvents: BedSoakChartEvent[] = []
  if (run.heatersOn != null || elapsed != null) temperatureEvents.push({ x: 0, label: t('tune.soak.event_heaters_on') })
  if (milestones.bed != null) temperatureEvents.push({ x: milestones.bed / 60, label: t('tune.soak.event_bed_at_temp') })
  if (milestones.chamber != null) temperatureEvents.push({ x: milestones.chamber / 60, label: t('tune.soak.event_chamber_at_temp') })

  const um = (v: number) => `${Math.round(v)} µm`
  const point = (time: number, value: number | null, index: number) => ({ x: time / 60, y: (value ?? 0) * 1000, name: bedSoakMeshName(index) })
  const movement: BedSoakChartSeries[] = [
    { name: t('tune.soak.height'), color: BedSoakColors.height, markers: true, format: um, data: used.filter((m) => m.height != null).map((m) => point(m.time, m.height, m.index)) },
    { name: t('tune.soak.shape'), color: BedSoakColors.shape, markers: true, format: um, data: used.filter((m) => m.shape != null).map((m) => point(m.time, m.shape, m.index)) },
  ]
  const movementEvents: BedSoakChartEvent[] = run.jumps.map((j) => ({ x: j.time / 60, label: t('tune.soak.event_jump', { mesh: j.mesh }) }))
  if (milestones.settled != null) movementEvents.push({ x: milestones.settled / 60, label: t('tune.soak.event_stopped_moving') })
  movementEvents.sort((a, b) => a.x - b.x)

  const speed: BedSoakChartSeries[] = [
    {
      name: t('tune.soak.change'),
      color: 'var(--ink-2)',
      markers: true,
      format: (v) => t('tune.soak.per_min', { value: v.toFixed(1) }),
      data: used.filter((m) => m.speed != null).map((m) => ({ x: m.time / 60, y: (m.speed ?? 0) * 1000, name: bedSoakMeshName(m.index) })),
    },
  ]

  const common = { xMin, xMax, timeLabel, hoverX, onHover: setHoverX }
  return (
    <div className="grid min-w-0 gap-3">
      <BedSoakChart title={t('tune.soak.chart_temperature')} unit="°C" series={temperature} events={temperatureEvents} references={[{ y: run.settings.bed }, ...(run.settings.chamber ? [{ y: run.settings.chamber }] : [])]} height={150} {...common} />
      <BedSoakChart title={t('tune.soak.chart_movement')} unit="µm" series={movement} events={movementEvents} height={150} {...common} />
      <BedSoakChart
        title={t('tune.soak.chart_still_moving')}
        note={t('tune.soak.chart_still_moving_note')}
        unit={t('tune.soak.unit_um_per_min')}
        series={speed}
        yMin={0}
        events={movementEvents}
        references={[{ y: speedLimit, label: t('tune.soak.speed_limit'), color: BedSoakColors.good }]}
        band={{ from: 0, to: speedLimit, color: 'rgba(12,163,12,0.12)' }}
        height={120}
        {...common}
      />
    </div>
  )
}

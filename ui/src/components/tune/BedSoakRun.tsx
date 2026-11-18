import { CircleCheck, Download, Info, TriangleAlert } from 'lucide-react'
import { Spinner } from '../ui'
import { client, usePrinter } from '../../state/printer'
import {
  BED_SOAK_ROOT,
  BED_SOAK_SETTLED_MESHES,
  BedSoakColors,
  bedSoakMeshesUnderLimit,
  bedSoakMilestones,
  bedSoakRepeatedProblemPoints,
  bedSoakStartTemps,
  type BedSoakRun,
  type BedSoakTemp,
  type TltgBedSoakMilestones,
  type TltgBedSoakState,
} from '../../qidi/tltg-bed-soak'
import { BedSoakRunCharts } from './BedSoakRunCharts'
import { BedSoakHeatmap } from './BedSoakHeatmap'
import { bedSoakClock, bedSoakDuration, bedSoakPointName, bedSoakStateLabel, bedSoakTargets, fmtDateTime } from './bed-soak-format'

type Stat = { id: string; label: string; value: string; detail?: string; state: 'done' | 'active' | 'warning' | 'none' }

/** Chamber temperature as the fork reads it: the chamber heater, else a chamber sensor. */
export function useChamberTemp() {
  return usePrinter((s) => (s.status['heater_generic chamber']?.temperature ?? s.status['temperature_sensor chamber']?.temperature) as number | undefined)
}

/** A run, live or saved: what it's doing or was, its milestones, charts, and change map. */
export function BedSoakRunView({ run, status, live, liveTemps = [], file }: { run: BedSoakRun | null; status?: TltgBedSoakState | null; live?: boolean; liveTemps?: BedSoakTemp[]; file?: string }) {
  const bedNow = usePrinter((s) => s.status.heater_bed?.temperature as number | undefined)
  const chamberNow = useChamberTemp()
  const isLive = !!live && !!status

  const s = run?.settings ?? status?.settings
  const settings = {
    started: run?.settings.started ?? status?.started ?? null,
    bed: s?.bed ?? 0,
    chamber: s?.chamber ?? null,
    probeCount: s?.probe_count ?? 5,
    timeLimit: s?.time_limit ?? null,
  }
  const speedLimit = status?.limits?.speed ?? run?.settings.limits.speed ?? 0.002
  const milestones: TltgBedSoakMilestones = isLive ? status!.milestones : run ? bedSoakMilestones(run) : { bed: null, chamber: null, chamber_max: null, settled: null, ready: null }
  const temps = (() => {
    const t = run?.temps ?? []
    const lastT = t.length ? t[t.length - 1][0] : -Infinity
    return live ? [...t, ...liveTemps.filter((x) => x[0] > lastT)] : t
  })()

  const stepText = (() => {
    if (!status) return ''
    if (status.stop_requested) return 'Stopping after this mesh'
    switch (status.step) {
      case 'nozzle':
        return `Heating the nozzle to ${status.settings?.nozzle ?? ''} °C`
      case 'mesh':
        return `Measuring mesh ${status.meshes}`
      case 'home':
        return 'Homing'
      case 'wipe':
        return 'Wiping the nozzle'
      case 'z_tilt':
        return 'Running Z tilt adjust'
      case 'home_z':
        return 'Homing Z'
      case 'cold_mesh':
        return 'Measuring the cold mesh'
      case 'finishing':
        return 'Finishing'
      default:
        return 'Running'
    }
  })()

  const targets = bedSoakTargets(settings.bed, settings.chamber)
  const meshSize = `${settings.probeCount} × ${settings.probeCount}`
  const runLine = isLive
    ? [stepText, ...(status!.elapsed != null ? [`${bedSoakClock(status!.elapsed)} since heaters on`] : []), targets, meshSize]
    : [...(settings.started ? [fmtDateTime(settings.started)] : []), targets, meshSize, bedSoakStateLabel[run?.result?.state ?? 'incomplete']]

  const stats: Stat[] = (() => {
    const heating = isLive ? status!.heaters_on != null : true
    const finished = run?.result?.state === 'finished'
    const start = !isLive && run ? bedSoakStartTemps(run) : null
    const pending = (value: number | undefined): Pick<Stat, 'value' | 'detail' | 'state'> =>
      isLive && heating ? { value: 'Heating', detail: value != null ? `${value.toFixed(1)} °C` : undefined, state: 'active' } : { value: '—', state: 'none' }
    const from = (v: number | null | undefined) => (v != null ? `from ${v.toFixed(1)} °C` : undefined)
    const out: Stat[] = [{ id: 'bed', label: `Bed at ${settings.bed} °C`, ...(milestones.bed != null ? { value: bedSoakDuration(milestones.bed), detail: from(start?.[1]), state: 'done' as const } : pending(bedNow)) }]
    if (!settings.chamber) out.push({ id: 'chamber', label: 'Chamber', value: 'Off', state: 'none' })
    else if (milestones.chamber != null) out.push({ id: 'chamber', label: `Chamber at ${settings.chamber} °C`, value: bedSoakDuration(milestones.chamber), detail: from(start?.[2]), state: 'done' })
    else if (milestones.chamber_max != null) out.push({ id: 'chamber', label: `Chamber at ${settings.chamber} °C`, value: `Stopped warming at ${milestones.chamber_max.toFixed(1)} °C`, state: 'none' })
    else out.push({ id: 'chamber', label: `Chamber at ${settings.chamber} °C`, ...pending(chamberNow) })
    if (milestones.settled != null) out.push({ id: 'settled', label: 'Stopped moving', value: bedSoakDuration(milestones.settled), state: 'done' })
    else if (isLive && milestones.bed != null) {
      const under = run ? bedSoakMeshesUnderLimit(run, milestones.bed, speedLimit) : 0
      out.push({ id: 'settled', label: 'Stopped moving', value: 'Settling', detail: `${under} of ${BED_SOAK_SETTLED_MESHES} meshes`, state: 'active' })
    } else if (finished) out.push({ id: 'settled', label: 'Stopped moving', value: `Still moving after ${settings.timeLimit ?? '—'} min`, state: 'warning' })
    else out.push({ id: 'settled', label: 'Stopped moving', value: '—', state: 'none' })
    out.push({ id: 'ready', label: 'Ready to print', value: milestones.ready != null ? bedSoakDuration(milestones.ready) : '—', state: milestones.ready != null ? 'done' : 'none' })
    return out
  })()

  const summary = (() => {
    if (!run) return null
    const used = run.meshes.filter((m) => m.used)
    const lastUsed = used[used.length - 1]
    const parts: string[] = []
    if (lastUsed && lastUsed.index > 0 && lastUsed.height != null && lastUsed.shape != null)
      parts.push(`Height ${lastUsed.height >= 0 ? '+' : '−'}${Math.abs(lastUsed.height).toFixed(3)} mm, shape ${lastUsed.shape.toFixed(3)} mm since the cold mesh`)
    const lastMesh = run.meshes[run.meshes.length - 1]
    const end = run.result?.ended != null && run.heatersOn != null ? run.result.ended - run.heatersOn : (lastMesh?.end ?? lastMesh?.time)
    if (end != null && end > 0) parts.push(`${run.meshes.length} mesh${run.meshes.length === 1 ? '' : 'es'} over ${bedSoakDuration(end)}`)
    if (run.result) parts.push(run.result.state === 'finished' && run.settings.keep_heating ? 'heaters kept on at the end' : 'heaters off at the end')
    return parts.length ? parts.join(' · ') : null
  })()

  const endNote = (() => {
    const r = run?.result
    if (!r) return 'This run has no result, as Klipper stopped before the test ended.'
    if (r.state === 'finished') return null
    return `${r.state === 'failed' ? 'The test failed.' : 'The test was stopped early.'} ${r.reason}`.trim()
  })()

  const warning = (() => {
    if (!run?.result?.warning) return null
    const points = bedSoakRepeatedProblemPoints(run)
    const list = points.slice(0, 3).map(bedSoakPointName).join(', ') + (points.length > 3 ? ` +${points.length - 3}` : '')
    const sentences: string[] = []
    if (points.length) sentences.push(points.length === 1 ? `${list} had a probe problem in more than one mesh.` : `${list} had probe problems in more than one mesh.`)
    if (run.jumps.length)
      sentences.push(run.jumps.length === 1 ? `Probe readings jumped in mesh ${run.jumps[0].mesh}, which isn't used.` : `Probe readings jumped in meshes ${run.jumps.map((j) => j.mesh).join(', ')}, which aren't used.`)
    sentences.push('Check the plate, then run the test again for a clean result.')
    return sentences.join(' ')
  })()

  const downloadHref = file ? client.fileUrl(BED_SOAK_ROOT, file) : null

  return (
    <div className="min-w-0">
      <div className="flex min-h-7 flex-wrap items-center gap-x-1">
        {runLine.map((part, i) => (
          <span key={i} className={i > 0 || !isLive ? 'text-ink-2' : 'font-semibold'}>
            {i > 0 ? ` · ${part}` : part}
          </span>
        ))}
        <span className="flex-1" />
        {downloadHref && (
          <a className="iconbtn" href={downloadHref} download={file!.split('/').pop()} title="Download" aria-label="Download">
            <Download size={15} />
          </a>
        )}
      </div>
      {isLive && status!.mesh_progress != null && (
        <div className="bar live mt-1.5 h-1!">
          <i style={{ width: `${status!.mesh_progress * 100}%` }} />
        </div>
      )}
      <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-4">
        {stats.map((st) => (
          <div key={st.id} className={`tile ${st.state === 'active' ? 'hot' : ''}`}>
            <span className="flex items-center gap-1.5 text-[12px] font-semibold text-ink-3">
              {st.state === 'done' && <CircleCheck size={13} style={{ color: BedSoakColors.good }} />}
              {st.state === 'warning' && <TriangleAlert size={13} style={{ color: BedSoakColors.warning }} />}
              {st.state === 'active' && <Spinner size={11} />}
              {st.label}
            </span>
            <span className="text-[15px] font-bold">
              {st.value}
              {st.detail && <span className="ml-1.5 text-[12px] font-semibold text-ink-3">{st.detail}</span>}
            </span>
          </div>
        ))}
      </div>
      {!isLive && run && (
        <>
          {summary && <div className="mt-2.5 text-[12.5px] text-ink-2">{summary}</div>}
          {warning && (
            <div className="tone-warn mt-3 flex items-start gap-2 rounded-lg px-3 py-2.5">
              <TriangleAlert size={15} className="mt-0.5 flex-none" />
              <span className="text-ink">{warning}</span>
            </div>
          )}
          {endNote && (
            <div className="tone-info mt-3 flex items-start gap-2 rounded-lg px-3 py-2.5">
              <Info size={15} className="mt-0.5 flex-none" />
              <span className="text-ink">{endNote}</span>
            </div>
          )}
        </>
      )}
      {run && run.meshes.length > 0 ? (
        <div className="mt-4 grid gap-6 xl:grid-cols-[minmax(0,1fr)_260px]">
          <BedSoakRunCharts run={run} temps={temps} milestones={milestones} elapsed={isLive && status!.elapsed != null ? status!.elapsed : undefined} />
          <BedSoakHeatmap run={run} />
        </div>
      ) : (
        <div className="mt-3 text-ink-3">The charts start once the cold mesh is measured.</div>
      )}
    </div>
  )
}

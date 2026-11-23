import { CircleCheck, Download, TriangleAlert } from 'lucide-react'
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
  type VoBedSoakMilestones,
  type VoBedSoakState,
} from '../../qidi/vo-bed-soak'
import { BedSoakRunCharts } from './BedSoakRunCharts'
import { BedSoakHeatmap } from './BedSoakHeatmap'
import { bedSoakClock, bedSoakDuration, bedSoakPointName, bedSoakStateLabel, bedSoakTargets, fmtDateTime } from './bed-soak-format'
import { Banner } from './common'
import { t, type MessageKey } from '../../i18n'

// What the live run is doing, by its step; nozzle and mesh steps name the temperature and the mesh.
const STEPS: Record<string, MessageKey> = {
  nozzle: 'tune.soak.step.nozzle',
  mesh: 'tune.soak.step.mesh',
  home: 'tune.soak.step.home',
  wipe: 'tune.soak.step.wipe',
  z_tilt: 'tune.soak.step.z_tilt',
  home_z: 'tune.soak.step.home_z',
  cold_mesh: 'tune.soak.step.cold_mesh',
  finishing: 'tune.soak.step.finishing',
}

type Stat = { id: string; label: string; value: string; detail?: string; state: 'done' | 'active' | 'warning' | 'none' }

/** Chamber temperature as the fork reads it: the chamber heater, else a chamber sensor. */
export function useChamberTemp() {
  return usePrinter((s) => (s.status['heater_generic chamber']?.temperature ?? s.status['temperature_sensor chamber']?.temperature) as number | undefined)
}

/** A run, live or saved: what it's doing or was, its milestones, charts, and change map. */
export function BedSoakRunView({ run, status, live, liveTemps = [], file }: { run: BedSoakRun | null; status?: VoBedSoakState | null; live?: boolean; liveTemps?: BedSoakTemp[]; file?: string }) {
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
  const milestones: VoBedSoakMilestones = isLive ? status!.milestones : run ? bedSoakMilestones(run) : { bed: null, chamber: null, chamber_max: null, settled: null, ready: null }
  const temps = (() => {
    const saved = run?.temps ?? []
    const lastT = saved.length ? saved[saved.length - 1][0] : -Infinity
    return live ? [...saved, ...liveTemps.filter((x) => x[0] > lastT)] : saved
  })()

  const stepText = !status ? '' : status.stop_requested ? t('tune.soak.step.stopping') : t(STEPS[status.step] ?? 'tune.soak.step.running', { temp: status.settings?.nozzle ?? '', n: status.meshes })

  const targets = bedSoakTargets(settings.bed, settings.chamber)
  const meshSize = `${settings.probeCount} × ${settings.probeCount}`
  const runLine = isLive
    ? [stepText, ...(status!.elapsed != null ? [t('tune.soak.since_heaters_on', { time: bedSoakClock(status!.elapsed) })] : []), targets, meshSize]
    : [...(settings.started ? [fmtDateTime(settings.started)] : []), targets, meshSize, bedSoakStateLabel(run?.result?.state ?? 'incomplete')]

  const stats: Stat[] = (() => {
    const heating = isLive ? status!.heaters_on != null : true
    const finished = run?.result?.state === 'finished'
    const start = !isLive && run ? bedSoakStartTemps(run) : null
    const pending = (value: number | undefined): Pick<Stat, 'value' | 'detail' | 'state'> =>
      isLive && heating ? { value: t('tune.soak.heating'), detail: value != null ? `${value.toFixed(1)} °C` : undefined, state: 'active' } : { value: '—', state: 'none' }
    const from = (v: number | null | undefined) => (v != null ? t('tune.soak.from_temp', { temp: v.toFixed(1) }) : undefined)
    const out: Stat[] = [{ id: 'bed', label: t('tune.soak.bed_at', { temp: settings.bed }), ...(milestones.bed != null ? { value: bedSoakDuration(milestones.bed), detail: from(start?.[1]), state: 'done' as const } : pending(bedNow)) }]
    if (!settings.chamber) out.push({ id: 'chamber', label: t('tune.soak.chamber'), value: t('common.off'), state: 'none' })
    else if (milestones.chamber != null) out.push({ id: 'chamber', label: t('tune.soak.chamber_at', { temp: settings.chamber }), value: bedSoakDuration(milestones.chamber), detail: from(start?.[2]), state: 'done' })
    else if (milestones.chamber_max != null) out.push({ id: 'chamber', label: t('tune.soak.chamber_at', { temp: settings.chamber }), value: t('tune.soak.chamber_stalled', { temp: milestones.chamber_max.toFixed(1) }), state: 'none' })
    else out.push({ id: 'chamber', label: t('tune.soak.chamber_at', { temp: settings.chamber }), ...pending(chamberNow) })
    if (milestones.settled != null) out.push({ id: 'settled', label: t('tune.soak.stopped_moving'), value: bedSoakDuration(milestones.settled), state: 'done' })
    else if (isLive && milestones.bed != null) {
      const under = run ? bedSoakMeshesUnderLimit(run, milestones.bed, speedLimit) : 0
      out.push({ id: 'settled', label: t('tune.soak.stopped_moving'), value: t('tune.soak.settling'), detail: t('tune.soak.meshes_of', { under, total: BED_SOAK_SETTLED_MESHES }), state: 'active' })
    } else if (finished) out.push({ id: 'settled', label: t('tune.soak.stopped_moving'), value: t('tune.soak.still_moving_after', { limit: settings.timeLimit ?? '—' }), state: 'warning' })
    else out.push({ id: 'settled', label: t('tune.soak.stopped_moving'), value: '—', state: 'none' })
    out.push({ id: 'ready', label: t('tune.soak.ready_to_print'), value: milestones.ready != null ? bedSoakDuration(milestones.ready) : '—', state: milestones.ready != null ? 'done' : 'none' })
    return out
  })()

  const summary = (() => {
    if (!run) return null
    const used = run.meshes.filter((m) => m.used)
    const lastUsed = used[used.length - 1]
    const parts: string[] = []
    if (lastUsed && lastUsed.index > 0 && lastUsed.height != null && lastUsed.shape != null)
      parts.push(t('tune.soak.summary_change', { height: `${lastUsed.height >= 0 ? '+' : '−'}${Math.abs(lastUsed.height).toFixed(3)}`, shape: lastUsed.shape.toFixed(3) }))
    const lastMesh = run.meshes[run.meshes.length - 1]
    const end = run.result?.ended != null && run.heatersOn != null ? run.result.ended - run.heatersOn : (lastMesh?.end ?? lastMesh?.time)
    if (end != null && end > 0) parts.push(t('tune.soak.summary_meshes', { count: run.meshes.length, duration: bedSoakDuration(end) }))
    if (run.result) parts.push(run.result.state === 'finished' && run.settings.keep_heating ? t('tune.soak.heaters_kept_on') : t('tune.soak.heaters_off'))
    return parts.length ? parts.join(' · ') : null
  })()

  const endNote = (() => {
    const r = run?.result
    if (!r) return t('tune.soak.no_result')
    if (r.state === 'finished') return null
    return `${r.state === 'failed' ? t('tune.soak.failed') : t('tune.soak.stopped_early')} ${r.reason}`.trim()
  })()

  const warning = (() => {
    if (!run?.result?.warning) return null
    const points = bedSoakRepeatedProblemPoints(run)
    const list = points.slice(0, 3).map(bedSoakPointName).join(', ') + (points.length > 3 ? ` +${points.length - 3}` : '')
    const sentences: string[] = []
    if (points.length) sentences.push(t('tune.soak.repeat_points', { count: points.length, list }))
    if (run.jumps.length)
      sentences.push(t('tune.soak.jumped', { count: run.jumps.length, list: run.jumps.map((j) => j.mesh).join(', ') }))
    sentences.push(t('tune.soak.check_plate'))
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
          <a className="iconbtn" href={downloadHref} download={file!.split('/').pop()} title={t('tune.shared.download')} aria-label={t('tune.shared.download')}>
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
          {warning && <Banner>{warning}</Banner>}
          {endNote && <Banner tone="info">{endNote}</Banner>}
        </>
      )}
      {run && run.meshes.length > 0 ? (
        <div className="mt-4 grid gap-6 xl:grid-cols-[minmax(0,1fr)_260px]">
          <BedSoakRunCharts run={run} temps={temps} milestones={milestones} elapsed={isLive && status!.elapsed != null ? status!.elapsed : undefined} />
          <BedSoakHeatmap run={run} />
        </div>
      ) : (
        <div className="mt-3 text-ink-3">{t('tune.soak.charts_wait')}</div>
      )}
    </div>
  )
}

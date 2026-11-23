import { useEffect, useState } from 'react'
import { ChevronDown, Flame, Play } from 'lucide-react'
import { Choices, MenuButton, Spinner, Stepper, Toggle } from '../ui'
import { attempt } from '../toast'
import { runGcode, usePrinter } from '../../state/printer'
import { usePresets } from '../../state/presets'
import { BED_SOAK_SETTLED_MESHES, bedSoakMilestones, bedSoakStartCommand, type BedSoakRun, type VoBedSoakState } from '../../qidi/vo-bed-soak'
import { bedSoakDuration, fmtDate } from './bed-soak-format'
import { useChamberTemp } from './BedSoakRun'
import { Banner, usePrinterFlags } from './common'
import { t } from '../../i18n'

/** Above this, the bed or chamber is still warm and the cold mesh won't be cold */
const WARM = 35

/** The fork's bed soak setup: targets, mesh size, time limit, and how the test ends. */
export function BedSoakSetup({ status, runs }: { status: VoBedSoakState; runs: { started: number; run: BedSoakRun }[] }) {
  const settings = usePrinter((s) => s.settings)
  const ready = usePrinter((s) => s.klippy === 'ready')
  const bedNow = usePrinter((s) => s.status.heater_bed?.temperature as number | undefined)
  const chamberNow = useChamberTemp()
  const { busy } = usePrinterFlags()
  const [presets] = usePresets()
  const chamberAvailable = !!status.chamber_available

  // Start from the last run's settings
  const last = status.settings
  const [bed, setBed] = useState(last?.bed ?? 60)
  const [chamber, setChamber] = useState(last?.chamber || 0)
  const [probeCount, setProbeCount] = useState(last && [5, 7, 9].includes(last.probe_count) ? last.probe_count : 5)
  const [stopWhenSettled, setStopWhenSettled] = useState(last?.stop_when_settled ?? true)
  const [timeLimit, setTimeLimit] = useState(last?.time_limit ?? 90)
  const [keepHeating, setKeepHeating] = useState(last?.keep_heating ?? false)
  const [starting, setStarting] = useState(false)
  useEffect(() => {
    if (!chamberAvailable) setChamber(0)
  }, [chamberAvailable])

  const bedMax = Number(settings.heater_bed?.max_temp ?? 120)
  const chamberMax = Number(settings['heater_generic chamber']?.max_temp ?? 70)
  const chamberTarget = chamberAvailable ? chamber : 0
  const speedLimit = `${+((status.limits?.speed ?? 0.002) * 1000).toFixed(1)}`

  const presetList = presets
    .filter((p) => p.values.heater_bed?.active && p.values.heater_bed.value > 0)
    .map((p) => {
      // Fluidd names heaters by their short name in presets
      const c = p.values.chamber ?? p.values['heater_generic chamber']
      return { id: p.id, name: p.name, bed: p.values.heater_bed.value, chamber: chamberAvailable && c?.active ? c.value : 0 }
    })

  const idle = ready && !busy && status.state !== 'running'
  const warning = (() => {
    if (!idle) return t('tune.soak.busy')
    const bedWarm = bedNow != null && bedNow > WARM
    const chamberWarm = chamberAvailable && chamberNow != null && chamberNow > WARM
    if (bedWarm && chamberWarm) return t('tune.soak.warm_both', { bed: Math.round(bedNow!), chamber: Math.round(chamberNow!) })
    if (bedWarm) return t('tune.soak.warm_bed', { bed: Math.round(bedNow!) })
    if (chamberWarm) return t('tune.soak.warm_chamber', { chamber: Math.round(chamberNow!) })
    return null
  })()

  // The latest finished run at the same targets, as a reminder of what to expect
  const lastRun = (() => {
    const match = runs.find(({ run }) => run.result?.state === 'finished' && run.settings.bed === bed && (run.settings.chamber ?? 0) === chamberTarget)
    if (!match) return null
    const m = bedSoakMilestones(match.run)
    if (m.bed == null) return null
    return m.settled != null
      ? t('tune.soak.last_settled', { date: fmtDate(match.started), bed: bedSoakDuration(m.bed), settled: bedSoakDuration(m.settled) })
      : t('tune.soak.last_moving', { date: fmtDate(match.started), bed: bedSoakDuration(m.bed), limit: match.run.settings.time_limit ?? '—' })
  })()

  const start = async () => {
    if (!idle || starting) return
    setStarting(true)
    await attempt(() => runGcode(bedSoakStartCommand({ bed, chamber: chamberTarget > 0 ? chamberTarget : null, probeCount, timeLimit, stopWhenSettled, keepHeating })))
    setStarting(false)
  }

  return (
    <div className="grid gap-3.5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <label className="grid gap-1.5">
          <span className="section-label">{t('tune.soak.bed')}</span>
          <Stepper value={bed} onChange={setBed} min={1} max={bedMax} step={5} unit="°C" label={t('tune.soak.bed')} />
        </label>
        {chamberAvailable && (
          <label className="grid gap-1.5">
            <span className="section-label">
              {t('tune.soak.chamber')} {chamber === 0 && <span className="font-semibold tracking-normal normal-case">· {t('tune.soak.off')}</span>}
            </span>
            <Stepper value={chamber} onChange={setChamber} min={0} max={chamberMax} step={5} unit={chamber > 0 ? '°C' : undefined} label={t('tune.soak.chamber')} />
          </label>
        )}
        <div className="grid gap-1.5">
          <span className="section-label">{t('tune.soak.mesh')}</span>
          <Choices
            value={probeCount}
            onChange={setProbeCount}
            options={[5, 7, 9].map((n) => ({ value: n, label: `${n} × ${n}` }))}
          />
        </div>
        <label className="grid gap-1.5">
          <span className="section-label">{t('tune.soak.time_limit')}</span>
          <Stepper value={timeLimit} onChange={setTimeLimit} min={1} max={240} step={15} unit={t('tune.soak.unit_min')} label={t('tune.soak.time_limit')} />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2.5">
        <label className="flex items-center gap-2.5" title={t('tune.soak.settled_tip', { speed: speedLimit, count: BED_SOAK_SETTLED_MESHES })}>
          <Toggle checked={stopWhenSettled} onChange={setStopWhenSettled} label={t('tune.soak.stop_settled')} />
          {t('tune.soak.stop_settled')}
        </label>
        <label className="flex items-center gap-2.5">
          <Toggle checked={keepHeating} onChange={setKeepHeating} label={t('tune.soak.keep_heating')} />
          {t('tune.soak.keep_heating')}
        </label>
        <span className="flex-1" />
        {presetList.length > 0 && (
          <MenuButton
            className="btn btn-sm"
            label={t('tune.soak.presets')}
            button={
              <>
                <Flame size={14} /> {t('tune.soak.presets')} <ChevronDown size={14} />
              </>
            }
            items={presetList.map((p) => ({
              label: (
                <span className="flex w-full items-center gap-4">
                  <span className="flex-1">{p.name}</span>
                  <span className="text-[12px] text-ink-3">{p.chamber ? `${p.bed} · ${p.chamber} °C` : `${p.bed} °C`}</span>
                </span>
              ),
              onSelect: () => (setBed(p.bed), setChamber(p.chamber || 0)),
            }))}
          />
        )}
        <button className="btn btn-primary" disabled={!idle || starting} onClick={start}>
          {starting ? <Spinner size={14} /> : <Play size={14} />} {t('tune.soak.start')}
        </button>
      </div>
      <p className="m-0 text-[12.5px] text-ink-3">
        {stopWhenSettled ? t('tune.soak.ends_settled', { speed: speedLimit, count: BED_SOAK_SETTLED_MESHES }) : t('tune.soak.runs_to_limit')}
      </p>
      {warning && <Banner className="">{warning}</Banner>}
      {lastRun && <div className="text-ink-2">{lastRun}</div>}
    </div>
  )
}

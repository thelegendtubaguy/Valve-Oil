import { useEffect, useState } from 'react'
import { ChevronDown, Flame, Play, TriangleAlert } from 'lucide-react'
import { Choices, MenuButton, Spinner, Stepper, Toggle } from '../ui'
import { attempt } from '../toast'
import { runGcode, usePrinter } from '../../state/printer'
import { usePresets } from '../../state/presets'
import { BED_SOAK_SETTLED_MESHES, bedSoakMilestones, bedSoakStartCommand, type BedSoakRun, type TltgBedSoakState } from '../../qidi/tltg-bed-soak'
import { bedSoakDuration, fmtDate } from './bed-soak-format'
import { useChamberTemp } from './BedSoakRun'
import { usePrinterFlags } from './hooks'

/** Above this, the bed or chamber is still warm and the cold mesh won't be cold */
const WARM = 35

/** The fork's bed soak setup: targets, mesh size, time limit, and how the test ends. */
export function BedSoakSetup({ status, runs }: { status: TltgBedSoakState; runs: { started: number; run: BedSoakRun }[] }) {
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
    if (!idle) return "The printer is busy. Start the test once it's idle."
    const bedWarm = bedNow != null && bedNow > WARM
    const chamberWarm = chamberAvailable && chamberNow != null && chamberNow > WARM
    if (bedWarm && chamberWarm) return `Bed is ${Math.round(bedNow!)} °C and chamber is ${Math.round(chamberNow!)} °C. Let them cool for a true cold start.`
    if (bedWarm) return `Bed is ${Math.round(bedNow!)} °C. Let it cool for a true cold start.`
    if (chamberWarm) return `Chamber is ${Math.round(chamberNow!)} °C. Let it cool for a true cold start.`
    return null
  })()

  // The latest finished run at the same targets, as a reminder of what to expect
  const lastRun = (() => {
    const match = runs.find(({ run }) => run.result?.state === 'finished' && run.settings.bed === bed && (run.settings.chamber ?? 0) === chamberTarget)
    if (!match) return null
    const m = bedSoakMilestones(match.run)
    if (m.bed == null) return null
    return m.settled != null
      ? `Last finished run at these temperatures (${fmtDate(match.started)}) reached temperature in ${bedSoakDuration(m.bed)} and stopped moving after ${bedSoakDuration(m.settled)}.`
      : `Last finished run at these temperatures (${fmtDate(match.started)}) reached temperature in ${bedSoakDuration(m.bed)} and was still moving after ${match.run.settings.time_limit ?? '—'} min.`
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
          <span className="section-label">Bed</span>
          <Stepper value={bed} onChange={setBed} min={1} max={bedMax} step={5} unit="°C" label="Bed" />
        </label>
        {chamberAvailable && (
          <label className="grid gap-1.5">
            <span className="section-label">Chamber {chamber === 0 && <span className="font-semibold tracking-normal normal-case">· off</span>}</span>
            <Stepper value={chamber} onChange={setChamber} min={0} max={chamberMax} step={5} unit={chamber > 0 ? '°C' : undefined} label="Chamber" />
          </label>
        )}
        <div className="grid gap-1.5">
          <span className="section-label">Mesh</span>
          <Choices
            value={probeCount}
            onChange={setProbeCount}
            options={[5, 7, 9].map((n) => ({ value: n, label: `${n} × ${n}` }))}
          />
        </div>
        <label className="grid gap-1.5">
          <span className="section-label">Time limit</span>
          <Stepper value={timeLimit} onChange={setTimeLimit} min={1} max={240} step={15} unit="min" label="Time limit" />
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2.5">
        <label className="flex items-center gap-2.5" title={`Ends the test once the bed moves less than ${speedLimit} µm per minute for ${BED_SOAK_SETTLED_MESHES} meshes in a row. Otherwise it runs to the time limit.`}>
          <Toggle checked={stopWhenSettled} onChange={setStopWhenSettled} label="Stop when the bed stops moving" />
          Stop when the bed stops moving
        </label>
        <label className="flex items-center gap-2.5">
          <Toggle checked={keepHeating} onChange={setKeepHeating} label="Keep heating afterwards" />
          Keep heating afterwards
        </label>
        <span className="flex-1" />
        {presetList.length > 0 && (
          <MenuButton
            className="btn btn-sm"
            label="Presets"
            button={
              <>
                <Flame size={14} /> Presets <ChevronDown size={14} />
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
          {starting ? <Spinner size={14} /> : <Play size={14} />} Start
        </button>
      </div>
      <p className="m-0 text-[12.5px] text-ink-3">
        {stopWhenSettled ? `Ends once the bed moves less than ${speedLimit} µm per minute for ${BED_SOAK_SETTLED_MESHES} meshes in a row, or at the time limit.` : 'Runs to the time limit.'}
      </p>
      {warning && (
        <div className="tone-warn flex items-start gap-2 rounded-lg px-3 py-2.5">
          <TriangleAlert size={15} className="mt-0.5 flex-none" />
          <span className="text-ink">{warning}</span>
        </div>
      )}
      {lastRun && <div className="text-ink-2">{lastRun}</div>}
    </div>
  )
}

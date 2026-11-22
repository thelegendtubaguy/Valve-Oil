// A Box's heater, which does two different things. Dry is QIDI's timed drying (BOX_START_DRYING): it heats the Box for
// a set number of hours and turns the spools every 5 minutes; it needs no filament in any slot and no print running. Heat
// holds a temperature until it's turned off, as QIDI does during a print; it can't go above the lowest Box limit among
// the filaments loaded in that Box, as on the touchscreen, and a limit of 0 forbids it.
//
// While the heater runs, the dialog is that run: what it's doing, how close to its target, and the one way to stop it.
// While it's off, Dry and Heat are two cards, and the chosen one's settings sit under them.
import { useState, type ReactNode } from 'react'
import { Droplet, Flame, Sun, Thermometer, TriangleAlert } from 'lucide-react'
import { Dialog, Stepper } from '../ui'
import { attempt } from '../toast'
import { boxCommands, boxHeatLimiters, boxHeaterMax, dryBlocker, type BoxState } from '../../state/box'
import { runGcode, usePrinter } from '../../state/printer'
import { startDryRun, stopDryRun, useDryLeft } from '../../state/box-dry'
import { fmtDuration, fmtEta } from '../../lib/format'
import { humidityLevel } from './BoxUnits'

const TEMPS = [40, 45, 50, 55, 60, 65]
const DRY_HOURS = [4, 8, 12, 24]
// The defaults: QIDI's filament list dries every filament in the Box at 45 °C for 12 hours.
const DRY_TEMP = 45
const DRY_TIME = 12

type Mode = 'dry' | 'heat'

const list = (xs: string[]) => (xs.length < 2 ? xs.join('') : `${xs.slice(0, -1).join(', ')}${xs.length > 2 ? ',' : ''} and ${xs[xs.length - 1]}`)

export function HeaterDialog({ box, boxNumber, onClose }: { box: BoxState; boxNumber: number; onClose: () => void }) {
  const c = box.climates.find((x) => x.box === boxNumber)
  const cfg = usePrinter((s) => (c?.heater ? s.heaterSettings[c.heater] : undefined))
  const cfgMax = cfg?.target_max_temp ?? cfg?.max_temp ?? 65
  const max = boxHeaterMax(box, boxNumber, cfgMax)
  const inPrint = box.printState === 'printing' || box.printState === 'paused'
  const [mode, setMode] = useState<Mode>(box.canDry && !inPrint ? 'dry' : 'heat')
  const [heatTo, setHeatTo] = useState<number>(c?.target && !c.drying ? c.target : Math.min(max, 45))
  const [dryTemp, setDryTemp] = useState(Math.min(DRY_TEMP, cfgMax))
  const [dryHours, setDryHours] = useState(DRY_TIME)
  const timer = useDryLeft(c)
  if (!c) return null
  const run = (g: string) => attempt(() => runGcode(g))
  const blocked = dryBlocker(box, boxNumber)
  const limiters = boxHeatLimiters(box, boxNumber)
  const types = list([...new Set(limiters.map((sl) => sl.type ?? sl.name ?? 'filament'))])
  const where = list(limiters.map((sl) => sl.label))
  const heater = c.heater
  const heating = !!c.target && !c.drying

  // ---- running: the run itself ----
  if (c.drying || heating) {
    return (
      <Dialog
        open
        onClose={onClose}
        title={`Box ${boxNumber} heater`}
        footer={
          c.drying ? (
            <button className="btn btn-danger" onClick={() => attempt(() => stopDryRun(boxNumber, () => runGcode(boxCommands.stopDry(boxNumber))))}>
              Stop drying
            </button>
          ) : (
            <>
              <button className="btn btn-danger mr-auto" onClick={() => heater && run(boxCommands.heater(heater, 0))}>
                Turn off
              </button>
              {heatTo !== c.target && max > 0 && (
                <button className="btn btn-primary" onClick={() => heater && (run(boxCommands.heater(heater, heatTo)), onClose())}>
                  Heat to {heatTo} °C
                </button>
              )}
            </>
          )
        }
      >
        <div className="grid gap-4">
          <Readings temp={c.temperature} humidity={c.humidity} />
          <div className="hd-status">
            <div className="flex items-center gap-2 text-[15px] font-semibold text-accent-ink">
              {c.drying ? <Sun size={16} /> : <Flame size={16} />}
              {c.drying ? `Drying at ${c.target} °C` : `Heating to ${c.target} °C`}
            </div>
            <div className="text-ink-2">{c.drying ? (box.dryRolls ? 'The spools turn every 5 minutes.' : 'The spools stay still.') : 'Holds until you turn it off.'}</div>
            {timer ? <TimeLeft left={timer.left} hours={timer.hours} temp={c.temperature} target={c.target!} /> : <Progress temp={c.temperature} target={c.target!} />}
          </div>
          {heating && max > 0 && <Temps label="Change to" value={heatTo} onChange={setHeatTo} max={max} />}
        </div>
      </Dialog>
    )
  }

  // ---- off: choose Dry or Heat ----
  const heatLocked = max === 0
  const dryOk = !blocked && dryTemp > 0
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Box ${boxNumber} heater`}
      footer={
        mode === 'dry' ? (
          <button className="btn btn-primary" disabled={!dryOk} onClick={() => (attempt(() => startDryRun(boxNumber, dryTemp, dryHours, () => runGcode(boxCommands.dry(boxNumber, dryTemp, dryHours)))), onClose())}>
            Dry {dryHours} h at {dryTemp} °C
          </button>
        ) : heatLocked ? undefined : (
          <button className="btn btn-primary" disabled={!heater || heatTo <= 0} onClick={() => heater && (run(boxCommands.heater(heater, heatTo)), onClose())}>
            Heat to {heatTo} °C
          </button>
        )
      }
    >
      <div className="grid gap-4">
        <Readings temp={c.temperature} humidity={c.humidity} />
        {box.canDry && (
          <div className="hd-modes" role="radiogroup" aria-label="Heater mode">
            <ModeCard on={mode === 'dry'} disabled={inPrint} onClick={() => setMode('dry')} icon={<Sun size={16} />} title="Dry" when="Between prints">
              {inPrint ? 'Not during a print' : box.dryRolls ? 'Set time, turning the spools' : 'Runs for a set time'}
            </ModeCard>
            <ModeCard on={mode === 'heat'} onClick={() => setMode('heat')} icon={<Flame size={16} />} title="Heat" when="During prints">
              Holds until you turn it off
            </ModeCard>
          </div>
        )}

        {mode === 'dry' ? (
          <>
            {blocked && <Warn>{blocked}</Warn>}
            <Temps label="Temperature" value={dryTemp} onChange={setDryTemp} max={cfgMax} />
            <div className="hd-field">
              <span className="k">Time</span>
              <div className="flex flex-wrap items-center gap-1.5">
                {DRY_HOURS.map((h) => (
                  <button key={h} type="button" className={`choice ${dryHours === h ? 'on' : ''}`} onClick={() => setDryHours(h)}>
                    {h} h
                  </button>
                ))}
                <div className="w-[156px]">
                  <Stepper value={dryHours} onChange={(v) => setDryHours(Math.max(1, Math.min(24, Math.round(v))))} step={1} min={1} max={24} unit="h" label="Drying time" />
                </div>
              </div>
            </div>
          </>
        ) : heatLocked ? (
          <Warn>
            QIDI's filament settings give {types} a Box heating limit of 0 °C, so Heat stays off while it's in {where}.{box.canDry ? (inPrint ? ' To dry it, use Dry after the print.' : ' To dry it, use Dry.') : ''}
          </Warn>
        ) : (
          <>
            <Temps label="Temperature" value={heatTo} onChange={setHeatTo} max={max} />
            {limiters.length > 0 && max < cfgMax && (
              <div className="text-ink-3">
                Up to {max} °C, QIDI's limit for {types} in {where}.
              </div>
            )}
          </>
        )}
      </div>
    </Dialog>
  )
}

/** The Box's temperature and humidity, in one strip. */
function Readings({ temp, humidity }: { temp: number | null; humidity: number | null }) {
  return (
    <div className="hd-read">
      <span>
        <Thermometer size={14} className="text-ink-3" />
        <b className="num">{temp != null ? `${Math.round(temp)} °C` : '—'}</b>
        <span className="text-ink-3">in the Box</span>
      </span>
      <span title={humidity != null ? humidityLevel(humidity).label : undefined}>
        <Droplet size={14} className={humidity != null ? humidityLevel(humidity).cls : 'text-ink-3'} />
        <b className="num">{humidity != null ? `${humidity}%` : '—'}</b>
        <span className="text-ink-3">humidity</span>
      </span>
    </div>
  )
}

/** How far the Box has come from room temperature to its target; full once it's within a degree. */
function Progress({ temp, target }: { temp: number | null; target: number }) {
  const t = temp ?? 0
  const there = target - t <= 1
  const p = there ? 1 : Math.max(0, Math.min(1, (t - 25) / Math.max(1, target - 25)))
  return (
    <div className="grid gap-1">
      <div className="hd-bar" aria-hidden>
        <span style={{ width: `${p * 100}%` }} />
      </div>
      <div className="num text-[12px] text-ink-3">{there ? `At ${target} °C` : `${temp != null ? Math.round(temp) : '—'} of ${target} °C`}</div>
    </div>
  )
}

/** A dry's time: the bar fills as it runs, with the time left, when it ends, and the temperature until it's reached. */
function TimeLeft({ left, hours, temp, target }: { left: number; hours: number; temp: number | null; target: number }) {
  const total = hours * 3600
  const warming = temp != null && target - temp > 1
  return (
    <div className="grid gap-1">
      <div className="hd-bar" aria-hidden>
        <span style={{ width: `${Math.max(0, Math.min(1, 1 - left / total)) * 100}%` }} />
      </div>
      <div className="num text-[12px] text-ink-3">
        <b className="font-semibold text-ink">{left > 0 ? `${fmtDuration(left)} left` : 'Ending'}</b>
        {[left > 0 && `ends ${fmtEta(left)}`, warming && `${Math.round(temp)} of ${target} °C`].filter(Boolean).map((x) => ` · ${x}`)}
      </div>
    </div>
  )
}

function ModeCard({ on, disabled, onClick, icon, title, when, children }: { on: boolean; disabled?: boolean; onClick: () => void; icon: ReactNode; title: string; when: string; children: ReactNode }) {
  return (
    <button type="button" role="radio" aria-checked={on} disabled={disabled} className={`hd-mode ${on ? 'on' : ''}`} onClick={onClick}>
      <span className="t">
        {icon} {title}
        <span className="w">{when}</span>
      </span>
      <span className="d">{children}</span>
    </button>
  )
}

function Temps({ label, value, onChange, max }: { label: string; value: number; onChange: (t: number) => void; max: number }) {
  return (
    <div className="hd-field">
      <span className="k">{label}</span>
      <div className="flex flex-wrap gap-1.5">
        {TEMPS.filter((t) => t <= max).map((t) => (
          <button key={t} type="button" className={`choice ${value === t ? 'on' : ''}`} onClick={() => onChange(t)}>
            {t} °C
          </button>
        ))}
      </div>
    </div>
  )
}

function Warn({ children }: { children: ReactNode }) {
  return (
    <div className="hd-warn">
      <TriangleAlert size={15} className="mt-px shrink-0" />
      <span>{children}</span>
    </div>
  )
}

// A Box's heater, which dries its spools. The target can't go above the lowest Box limit among the filaments loaded in
// that Box, as on the touchscreen; a limit of 0 forbids heating.
import { useState } from 'react'
import { Droplet, Thermometer } from 'lucide-react'
import { Dialog, SectionLabel, Stepper } from '../ui'
import { attempt } from '../toast'
import { boxCommands, boxHeaterMax, type BoxState } from '../../state/box'
import { runGcode, usePrinter } from '../../state/printer'
import { humidityLevel } from './BoxUnits'

const PRESETS = [45, 50, 55, 60, 65]

export function HeaterDialog({ box, boxNumber, onClose }: { box: BoxState; boxNumber: number; onClose: () => void }) {
  const c = box.climates.find((x) => x.box === boxNumber)
  const cfg = usePrinter((s) => (c?.heater ? s.heaterSettings[c.heater] : undefined))
  const max = boxHeaterMax(box, boxNumber, cfg?.target_max_temp ?? cfg?.max_temp)
  const [draft, setDraft] = useState<number>(c?.target || Math.min(max, 45) || 0)
  if (!c) return null
  const locked = max === 0 && !c.target
  const set = (t: number) => c.heater && attempt(() => runGcode(boxCommands.heater(c.heater!, t)))
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Box ${boxNumber} dryer`}
      footer={
        <>
          <button className="btn btn-danger mr-auto" disabled={!c.heater || !c.target} onClick={() => set(0)}>
            Turn off
          </button>
          <button className="btn btn-ghost" onClick={onClose}>
            Close
          </button>
          <button className="btn btn-primary" disabled={!c.heater || locked || draft <= 0} onClick={() => (set(draft), onClose())}>
            Dry at {draft} °C
          </button>
        </>
      }
    >
      <div className="grid gap-4">
        <div className="grid grid-cols-3 gap-2">
          <div className="tile">
            <span className="lbl inline-flex items-center gap-1">
              <Thermometer size={12} /> Now
            </span>
            <span className="v num">{c.temperature != null ? `${Math.round(c.temperature)}°` : '—'}</span>
          </div>
          <div className={`tile ${c.target ? 'hot' : ''}`}>
            <span className="lbl">Target</span>
            <span className={`v num ${c.target ? 'text-accent-ink' : 'text-ink-3'}`}>{c.target ? `${c.target}°` : 'Off'}</span>
          </div>
          <div className="tile">
            <span className="lbl inline-flex items-center gap-1">
              <Droplet size={12} /> Humidity
            </span>
            <span className={`v num ${c.humidity != null ? humidityLevel(c.humidity).cls : ''}`}>{c.humidity != null ? `${c.humidity}%` : '—'}</span>
          </div>
        </div>
        {locked ? (
          <div className="tone-warn rounded-lg px-3 py-2 text-ink">A filament loaded in this Box must not be heated in the Box.</div>
        ) : (
          <>
            <div className="grid gap-2">
              <SectionLabel>Temperature</SectionLabel>
              <div className="flex flex-wrap gap-1.5">
                {PRESETS.filter((t) => t <= max).map((t) => (
                  <button key={t} type="button" className={`choice ${draft === t ? 'on' : ''}`} onClick={() => setDraft(t)}>
                    {t} °C
                  </button>
                ))}
              </div>
              <div className="max-w-[220px]">
                <Stepper value={draft} onChange={setDraft} step={5} min={0} max={max} unit="°C" label="Box heater target" disabled={!c.heater} />
              </div>
            </div>
            <div className="text-ink-3">Up to {max} °C for the filaments loaded in Box {boxNumber}.</div>
          </>
        )}
      </div>
    </Dialog>
  )
}

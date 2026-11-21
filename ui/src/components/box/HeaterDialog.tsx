// A Box's heater, which dries its spools. The target can't go above the lowest Box limit among the filaments loaded in
// that Box, as on the touchscreen; a limit of 0 forbids heating.
import { useState } from 'react'
import { Droplet, Thermometer } from 'lucide-react'
import { Dialog, SectionLabel, Stepper } from '../ui'
import { attempt } from '../toast'
import { boxCommands, boxHeaterMax, type BoxState } from '../../state/box'
import { runGcode, usePrinter } from '../../state/printer'
import { humidityLevel } from './BoxUnits'
import { t } from '../../i18n'

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
      title={t('box.heater.title', { n: boxNumber })}
      footer={
        <>
          <button className="btn btn-danger mr-auto" disabled={!c.heater || !c.target} onClick={() => set(0)}>
            {t('box.heater.turn_off')}
          </button>
          <button className="btn btn-ghost" onClick={onClose}>
            {t('common.close')}
          </button>
          <button className="btn btn-primary" disabled={!c.heater || locked || draft <= 0} onClick={() => (set(draft), onClose())}>
            {t('box.heater.dry_at', { temp: draft })}
          </button>
        </>
      }
    >
      <div className="grid gap-4">
        <div className="grid grid-cols-3 gap-2">
          <div className="tile">
            <span className="lbl inline-flex items-center gap-1">
              <Thermometer size={12} /> {t('box.heater.now')}
            </span>
            <span className="v num">{c.temperature != null ? `${Math.round(c.temperature)}°` : '—'}</span>
          </div>
          <div className={`tile ${c.target ? 'hot' : ''}`}>
            <span className="lbl">{t('box.heater.target')}</span>
            <span className={`v num ${c.target ? 'text-accent-ink' : 'text-ink-3'}`}>{c.target ? `${c.target}°` : t('common.off')}</span>
          </div>
          <div className="tile">
            <span className="lbl inline-flex items-center gap-1">
              <Droplet size={12} /> {t('box.heater.humidity')}
            </span>
            <span className={`v num ${c.humidity != null ? humidityLevel(c.humidity).cls : ''}`}>{c.humidity != null ? `${c.humidity}%` : '—'}</span>
          </div>
        </div>
        {locked ? (
          <div className="tone-warn rounded-lg px-3 py-2 text-ink">{t('box.heater.locked')}</div>
        ) : (
          <>
            <div className="grid gap-2">
              <SectionLabel>{t('box.heater.temperature')}</SectionLabel>
              <div className="flex flex-wrap gap-1.5">
                {PRESETS.filter((t) => t <= max).map((t) => (
                  <button key={t} type="button" className={`choice ${draft === t ? 'on' : ''}`} onClick={() => setDraft(t)}>
                    {t} °C
                  </button>
                ))}
              </div>
              <div className="max-w-[220px]">
                <Stepper value={draft} onChange={setDraft} step={5} min={0} max={max} unit="°C" label={t('box.heater.target_label')} disabled={!c.heater} />
              </div>
            </div>
            <div className="text-ink-3">{t('box.heater.up_to', { max, n: boxNumber })}</div>
          </>
        )}
      </div>
    </Dialog>
  )
}

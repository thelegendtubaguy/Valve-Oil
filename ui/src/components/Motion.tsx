import { useState, type ReactNode } from 'react'
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, ChevronRight, House, Move } from 'lucide-react'
import { Choices } from './ui'
import { jog as jogAxis, send } from './control/common'
import { extrudeGcode } from './control/gcode'
import { usePrinter } from '../state/printer'
import { href } from '../lib/router'
import { t } from '../i18n'

const STEPS = [0.1, 1, 10, 50]
const EXTRUDE = [5, 10, 50]
// Fluidd's default speeds, in mm/s, as on the Control screen.
const XY_SPEED = 130
const Z_SPEED = 10
const E_SPEED = 5

/** Home's toolhead card: jogging, homing, motors off, and extruding. The Control screen has the rest. */
export function Motion({ className = '' }: { className?: string }) {
  const homed = usePrinter((s) => (s.status.toolhead?.homed_axes as string | undefined) ?? '')
  const pos = usePrinter((s) => (s.status.toolhead?.position as number[] | undefined) ?? null)
  const printing = usePrinter((s) => s.status.print_stats?.state === 'printing')
  const canExtrude = usePrinter((s) => s.status.extruder?.can_extrude === true)
  const nozzle = usePrinter((s) => (s.status.extruder?.temperature as number | undefined) ?? null)
  const clientMacro = usePrinter((s) => (s.objects.includes('gcode_macro _CLIENT_LINEAR_MOVE') ? '_CLIENT_LINEAR_MOVE' : null))
  const [step, setStep] = useState(10)
  const [eLen, setELen] = useState(10)
  const can = (axis: string) => !printing && homed.includes(axis)
  // A long Z jog with the bed under the nozzle is the easiest crash to make, so Z steps stop at 25 mm.
  const jog = (axis: 'X' | 'Y' | 'Z', dir: 1 | -1) => jogAxis(axis, dir * (axis === 'Z' ? Math.min(step, 25) : step), axis === 'Z' ? Z_SPEED : XY_SPEED, clientMacro)
  const missing = 'XYZ'.split('').filter((a) => !homed.includes(a.toLowerCase()))

  return (
    <section className={`card ${className}`}>
      <h3 className="card-title">
        <Move size={14} /> {t('app.motion.title')}
        <span className="aside num">{pos ? `X ${pos[0].toFixed(1)}  Y ${pos[1].toFixed(1)}  Z ${pos[2].toFixed(2)}` : ''}</span>
      </h3>
      <div className="flex flex-wrap items-start gap-x-5 gap-y-3">
        <div className="flex gap-2">
          <div className="grid grid-cols-3 gap-1" role="group" aria-label={t('app.motion.move_xy')}>
            <span />
            <Pad label="Y+" disabled={!can('y')} onClick={() => jog('Y', 1)}>
              <ArrowUp size={16} />
            </Pad>
            <span />
            <Pad label="X−" disabled={!can('x')} onClick={() => jog('X', -1)}>
              <ArrowLeft size={16} />
            </Pad>
            <Pad label={t('app.motion.home_xy')} home disabled={printing} onClick={() => send('G28 X Y')}>
              <House size={15} />
              {/* i18n-ignore: axis names */}
              <span className="ax">XY</span>
            </Pad>
            <Pad label="X+" disabled={!can('x')} onClick={() => jog('X', 1)}>
              <ArrowRight size={16} />
            </Pad>
            <span />
            <Pad label="Y−" disabled={!can('y')} onClick={() => jog('Y', -1)}>
              <ArrowDown size={16} />
            </Pad>
            <span />
          </div>
          <div className="grid grid-rows-3 gap-1" role="group" aria-label={t('app.motion.move_z')}>
            <Pad label={t('app.motion.z_up')} disabled={!can('z')} onClick={() => jog('Z', 1)}>
              <ArrowUp size={16} />
              <span className="ax">Z+</span>
            </Pad>
            <Pad label={t('app.motion.home_z')} home disabled={printing} onClick={() => send('G28 Z')}>
              <House size={15} />
              <span className="ax">Z</span>
            </Pad>
            <Pad label={t('app.motion.z_down')} disabled={!can('z')} onClick={() => jog('Z', -1)}>
              <ArrowDown size={16} />
              <span className="ax">Z−</span>
            </Pad>
          </div>
        </div>
        <div className="grid min-w-[190px] flex-1 content-start gap-2.5">
          <div>
            <div className="section-label mb-1">{t('app.motion.step')}</div>
            <Choices value={step} options={STEPS.map((v) => ({ value: v, label: String(v) }))} onChange={setStep} />
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            <button className="btn btn-sm" disabled={printing} onClick={() => send('G28')}>
              {t('app.motion.home_all')}
            </button>
            <button className="btn btn-sm" disabled={printing} onClick={() => send('M84')}>
              {t('app.motion.motors_off')}
            </button>
            <span className={`text-[12px] font-semibold ${missing.length ? 'text-warn' : 'text-good'}`}>{missing.length === 3 ? t('app.motion.not_homed') : missing.length ? t('app.motion.axes_not_homed', { axes: missing.join('') }) : t('app.motion.homed')}</span>
          </div>
          <div>
            <div className="section-label mb-1">{t('app.motion.extruder')}</div>
            <div className="flex flex-wrap items-center gap-1.5">
              <Choices value={eLen} options={EXTRUDE.map((v) => ({ value: v, label: String(v) }))} onChange={setELen} />
              <button className="btn btn-sm" disabled={printing || !canExtrude} onClick={() => send(extrudeGcode(-eLen, E_SPEED, clientMacro))}>
                <ArrowUp size={13} /> {t('app.motion.retract')}
              </button>
              <button className="btn btn-sm" disabled={printing || !canExtrude} onClick={() => send(extrudeGcode(eLen, E_SPEED, clientMacro))}>
                <ArrowDown size={13} /> {t('app.motion.extrude')}
              </button>
            </div>
          </div>
        </div>
      </div>
      <div className="mt-2.5 flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1 text-[12.5px] text-ink-3">
          {printing
            ? t('app.motion.locked')
            : missing.length
              ? t('app.motion.home_first')
              : !canExtrude
                ? nozzle != null
                  ? t('app.motion.needs_hot_now', { temp: Math.round(nozzle) })
                  : t('app.motion.needs_hot')
                : t('app.motion.z_hint')}
        </div>
        <a href={href('control', 'move')} className="inline-flex items-center gap-0.5 text-[12.5px] font-semibold text-accent-ink">
          {t('app.motion.more')} <ChevronRight size={13} />
        </a>
      </div>
    </section>
  )
}

function Pad({ label, disabled, onClick, children, home }: { label: string; disabled?: boolean; onClick: () => void; children: ReactNode; home?: boolean }) {
  return (
    <button type="button" title={label} aria-label={label} disabled={disabled} onClick={onClick} className={`btn jog h-11! w-11! ${home ? 'home' : ''}`}>
      <span className="grid justify-items-center">{children}</span>
    </button>
  )
}

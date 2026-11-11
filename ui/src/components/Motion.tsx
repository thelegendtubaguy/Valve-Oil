import { useState, type ReactNode } from 'react'
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, House, Move } from 'lucide-react'
import { attempt } from './toast'
import { runGcode, usePrinter } from '../state/printer'

const STEPS = [0.1, 1, 10, 50]
const EXTRUDE = [5, 10, 50]
const XY_FEED = 6000
const Z_FEED = 600
const E_FEED = 300

// Jogs run relative inside a saved G-code state, so they never leave the printer in G91 or M83 for macros
// that expect absolute coordinates.
const jog = (axis: 'X' | 'Y' | 'Z', mm: number) =>
  `SAVE_GCODE_STATE NAME=vo_jog\nG91\nG1 ${axis}${mm} F${axis === 'Z' ? Z_FEED : XY_FEED}\nRESTORE_GCODE_STATE NAME=vo_jog`
const extrude = (mm: number) => `SAVE_GCODE_STATE NAME=vo_jog\nM83\nG1 E${mm} F${E_FEED}\nRESTORE_GCODE_STATE NAME=vo_jog`

/** Toolhead jogging, homing, motors off, and extruding. Locked while a print is running. */
export function Motion({ className = '' }: { className?: string }) {
  const homed = usePrinter((s) => (s.status.toolhead?.homed_axes as string | undefined) ?? '')
  const pos = usePrinter((s) => (s.status.toolhead?.position as number[] | undefined) ?? null)
  const printing = usePrinter((s) => s.status.print_stats?.state === 'printing')
  const canExtrude = usePrinter((s) => s.status.extruder?.can_extrude === true)
  const nozzle = usePrinter((s) => (s.status.extruder?.temperature as number | undefined) ?? null)
  const [step, setStep] = useState(10)
  const [eLen, setELen] = useState(10)
  const [busy, setBusy] = useState(false)
  const locked = printing

  const run = async (g: string) => {
    setBusy(true)
    await attempt(() => runGcode(g))
    setBusy(false)
  }
  const can = (axis: string) => !locked && homed.includes(axis.toLowerCase())

  return (
    <section className={`card ${className}`}>
      <h3 className="card-title">
        <Move size={14} /> Toolhead
        <span className="aside num">{pos ? `X ${pos[0].toFixed(1)}  Y ${pos[1].toFixed(1)}  Z ${pos[2].toFixed(2)}` : ''}</span>
      </h3>
      <div className="flex flex-wrap items-start gap-x-5 gap-y-3">
        <div className="flex gap-2.5">
          <div className="grid grid-cols-3 gap-1.5" role="group" aria-label="Move X and Y">
            <span />
            <Pad label="Y+" disabled={!can('y')} onClick={() => run(jog('Y', step))}>
              <ArrowUp size={16} />
            </Pad>
            <span />
            <Pad label="X−" disabled={!can('x')} onClick={() => run(jog('X', -step))}>
              <ArrowLeft size={16} />
            </Pad>
            <Pad label="Home X and Y" disabled={locked} onClick={() => run('G28 X Y')} tone="home">
              <House size={15} />
              <span className="text-[9.5px] font-semibold">XY</span>
            </Pad>
            <Pad label="X+" disabled={!can('x')} onClick={() => run(jog('X', step))}>
              <ArrowRight size={16} />
            </Pad>
            <span />
            <Pad label="Y−" disabled={!can('y')} onClick={() => run(jog('Y', -step))}>
              <ArrowDown size={16} />
            </Pad>
            <span />
          </div>
          <div className="grid grid-rows-3 gap-1.5" role="group" aria-label="Move Z">
            <Pad label="Z+, bed down" disabled={!can('z')} onClick={() => run(jog('Z', Math.min(step, 25)))}>
              <ArrowUp size={16} />
              <span className="text-[9.5px] font-semibold">Z</span>
            </Pad>
            <Pad label="Home Z" disabled={locked} onClick={() => run('G28 Z')} tone="home">
              <House size={15} />
              <span className="text-[9.5px] font-semibold">Z</span>
            </Pad>
            <Pad label="Z−, bed up" disabled={!can('z')} onClick={() => run(jog('Z', -Math.min(step, 25)))}>
              <ArrowDown size={16} />
              <span className="text-[9.5px] font-semibold">Z</span>
            </Pad>
          </div>
        </div>
        <div className="grid min-w-[200px] flex-1 content-start gap-2.5">
          <div>
            <div className="lbl mb-1">Step</div>
            <Segmented values={STEPS} value={step} onChange={setStep} unit="mm" />
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button className="btn btn-sm" disabled={locked || busy} onClick={() => run('G28')}>
              Home all
            </button>
            <button className="btn btn-sm" disabled={locked || busy} onClick={() => run('M84')}>
              Motors off
            </button>
            <span className={`self-center text-[12px] font-medium ${homed === 'xyz' ? 'text-good' : 'text-warn'}`}>
              {homed === 'xyz' ? 'Homed' : homed ? `${'XYZ'.split('').filter((a) => !homed.includes(a.toLowerCase())).join('')} not homed` : 'Not homed'}
            </span>
          </div>
          <div>
            <div className="lbl mb-1">Extruder</div>
            <div className="grid gap-1.5">
              <Segmented values={EXTRUDE} value={eLen} onChange={setELen} unit="mm" />
              <div className="flex gap-1.5">
                <button className="btn btn-sm" disabled={locked || busy || !canExtrude} onClick={() => run(extrude(-eLen))}>
                  <ArrowUp size={13} /> Retract
                </button>
                <button className="btn btn-sm" disabled={locked || busy || !canExtrude} onClick={() => run(extrude(eLen))}>
                  <ArrowDown size={13} /> Extrude
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
      {locked ? (
        <div className="mt-2.5 text-[12.5px] text-ink-3">Moving the toolhead is locked while printing. Pause to move it.</div>
      ) : homed !== 'xyz' ? (
        <div className="mt-2.5 text-[12.5px] text-ink-3">Home an axis before moving it.</div>
      ) : !canExtrude ? (
        <div className="mt-2.5 text-[12.5px] text-ink-3">Extruding needs the nozzle hot{nozzle != null ? `; it's ${Math.round(nozzle)} °C` : ''}.</div>
      ) : null}
    </section>
  )
}

function Pad({ label, disabled, onClick, children, tone }: { label: string; disabled?: boolean; onClick: () => void; children: ReactNode; tone?: 'home' }) {
  return (
    <button
      type="button"
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className={`btn grid h-11 w-11 place-items-center gap-0 p-0! leading-none ${tone === 'home' ? 'text-accent-ink' : ''}`}
    >
      <span className="grid justify-items-center">{children}</span>
    </button>
  )
}

function Segmented({ values, value, onChange, unit }: { values: number[]; value: number; onChange: (v: number) => void; unit: string }) {
  return (
    <div className="inline-flex w-fit gap-0.5 rounded-lg bg-canvas p-0.5 shadow-[inset_0_0_0_1px_var(--line)]" role="radiogroup">
      {values.map((v) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={v === value}
          onClick={() => onChange(v)}
          className={`num rounded-md border-0 px-2 py-0.5 text-[12px] font-medium ${v === value ? 'bg-surface text-ink shadow-[var(--shadow-btn)]' : 'bg-transparent text-ink-3'}`}
        >
          {v}
          {v === values[values.length - 1] ? ` ${unit}` : ''}
        </button>
      ))}
    </div>
  )
}

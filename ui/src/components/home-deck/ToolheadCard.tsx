// Mockup B's toolhead card: one position line, then Move, Extrude, and Tune sections that fold to a one-line summary.
// Which sections start open depends on whether a print is running; a section the user opens or closes stays that way.
import { useState, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, ChevronRight, ChevronsDown, ChevronsUp, Ellipsis, House, Move3d, Lock, LockOpen, Pipette, Power, SlidersHorizontal, Snowflake } from 'lucide-react'
import { MenuButton, Stepper, confirm } from '../ui'
import { NumberInput, Slider, jog as jogAxis, send, useHasCommand, usePrinting } from '../control/common'
import { extrudeGcode, gcode, moveGcode } from '../control/gcode'
import { useClientMacro, useTools } from '../control/Move'
import { BoxTools } from '../box/BoxTools'
import { printer, usePrinter } from '../../state/printer'
import { useUiSetting } from '../settings/fluidd'
import { useBox } from '../../state/box'
import { toast } from '../toast'
import { JogWheel, MODES, useMode, type Mode } from './Deck'
import './deck.css'

const XY_SPEED = 130
const Z_SPEED = 10
const Z_STEP_MAX = 25
const UNHOMED_XY_SPEED = 50
const Z_STEPS = [0.005, 0.01, 0.025, 0.05]
const E_LENGTHS = [5, 10, 25, 50]

const sign = (v: number, d: number) => `${v >= 0 ? '+' : ''}${v.toFixed(d)}`

export function ToolheadCard({ className = '' }: { className?: string }) {
  const homed = usePrinter((s) => (s.status.toolhead?.homed_axes as string | undefined) ?? '')
  const printing = usePrinting()
  const ctx = printing ? 'printing' : 'idle'
  const missing = 'XYZ'.split('').filter((a) => !homed.includes(a.toLowerCase()))
  const [free, setFree] = useMoveUnhomed(printing, missing.length === 0)
  return (
    <section className={`card th ${className}`}>
      <h3 className="card-title">
        Toolhead
        <span className="aside flex items-center gap-1">
          <MoreMenu />
        </span>
      </h3>
      <Readout homed={homed} printing={printing} />
      <Fold id="move" ctx={ctx} title="Move" icon={<Move3d size={13} />} def locked={printing && 'Locked while printing. Pause to move.'} summary={free ? <span className="text-[var(--red)]">Moving without homing</span> : <MoveSummary />}>
        <MoveBody homed={homed} free={free} onFree={setFree} />
      </Fold>
      <Fold id="extrude" ctx={ctx} title="Extrude" icon={<Pipette size={13} />} def={false} locked={printing && 'Locked while printing'} summary={<ExtrudeSummary />}>
        <ExtrudeBody />
      </Fold>
      <Fold id="tune" ctx={ctx} title="Tune" icon={<SlidersHorizontal size={13} />} def={false} summary={<TuneSummary />}>
        <TuneBody />
      </Fold>
    </section>
  )
}

/** The toolhead tools (clean, park, calibrations) behind one button; homing has its own buttons on the card. */
function MoreMenu() {
  const tools = useTools()
  const printing = usePrinting()
  const allHomed = usePrinter((s) => s.status.toolhead?.homed_axes === 'xyz')
  if (!tools.some(Boolean)) return null
  return (
    <MenuButton
      label="More toolhead actions"
      button={<Ellipsis size={16} />}
      items={tools.map((t) => (t ? { label: <span className="code">{t.label ?? t.name}</span>, onSelect: () => send(t.homeFirst && !allHomed ? `G28\n${t.name}` : t.name), disabled: printing || t.disabled } : null))}
    />
  )
}

/** One number per axis: the live position, which you can click and type over to move there. An axis that isn't homed shows in amber. */
function Readout({ homed, printing }: { homed: string; printing: boolean }) {
  const live = usePrinter((s) => (s.status.motion_report?.live_position as number[] | undefined) ?? null)
  const toolhead = usePrinter((s) => (s.status.toolhead?.position as number[] | undefined) ?? null)
  const clientMacro = useClientMacro()
  return (
    <div className="th-readout" role="group" aria-label="Position">
      {(['X', 'Y', 'Z'] as const).map((a, i) => {
        const ok = homed.includes(a.toLowerCase())
        return (
          <label key={a} title={ok ? `Type a position and press Enter to move ${a} there` : `${a} isn't homed. Home it to move by position.`}>
            <span className={ok ? 'ax' : 'ax warn'}>{a}</span>
            <NumberInput
              label={`Move ${a} to`}
              value={live?.[i] ?? toolhead?.[i] ?? 0}
              digits={i === 2 ? 3 : 2}
              disabled={!ok}
              readOnly={printing}
              onCommit={(v) => send(moveGcode({ [a]: v }, a === 'Z' ? Z_SPEED : XY_SPEED, true, clientMacro))}
            />
          </label>
        )
      })}
    </div>
  )
}

/** A section heading that folds the section. Closed, it shows a one-line summary instead. */
function Fold({ id, ctx, title, icon, def, locked, summary, children }: { id: string; ctx: string; title: string; icon: ReactNode; def: boolean; locked?: string | false; summary: ReactNode; children: ReactNode }) {
  const key = `vo-th-${id}-${ctx}`
  const [, bump] = useState(0)
  const saved = localStorage.getItem(key)
  const open = !locked && (saved == null ? def : saved === '1')
  const toggle = () => {
    try {
      localStorage.setItem(key, open ? '0' : '1')
    } catch {}
    bump((n) => n + 1)
  }
  return (
    <div className="th-fold">
      <button type="button" className="th-head" aria-expanded={open} disabled={!!locked} onClick={toggle}>
        <ChevronRight size={14} className="chev" />
        <span className="section-label inline-flex items-center gap-1.5">
          {icon} {title}
        </span>
        <span className="sum">{locked || (!open && summary)}</span>
      </button>
      {open && <div className="th-body">{children}</div>}
    </div>
  )
}

// ---- Move ----
function MoveSummary() {
  const [mode] = useMode()
  return (
    <>
      {MODES[mode].label} steps · {MODES[mode].small} and {MODES[mode].big} mm
    </>
  )
}

/** Whether any stepper is powered. Printers without stepper_enable status count as on. */
function useSteppersOn() {
  return usePrinter((s) => {
    const st = s.status.stepper_enable?.steppers as Record<string, boolean> | undefined
    return st ? Object.values(st).some(Boolean) : true
  })
}

function MoveBody({ homed, free, onFree }: { homed: string; free: boolean; onFree: (on: boolean) => void }) {
  const printing = usePrinting()
  const clientMacro = useClientMacro()
  const [mode, setMode] = useMode()
  const { small, big } = MODES[mode]
  const canFree = useHasCommand('SET_KINEMATIC_POSITION')
  const steppersOn = useSteppersOn()
  const can = (a: string) => !printing && (homed.includes(a) || free)
  const zBig = Math.min(big, Z_STEP_MAX)
  // A homed axis jogs as usual, inside its limits; an unhomed one moves only with "Move without homing" on.
  const move = (axis: 'X' | 'Y' | 'Z', d: number) => (homed.includes(axis.toLowerCase()) ? jogAxis(axis, d, axis === 'Z' ? Z_SPEED : XY_SPEED, clientMacro) : unhomedMove(axis, d))
  const jogZ = (d: number) => move('Z', d)
  const zHot = free && !homed.includes('z') ? 'hot' : ''
  return (
    <div className="grid gap-3">
      <div className={`th-move ${free ? 'free' : ''}`}>
        <div className="steprock" role="radiogroup" aria-label="Step size">
          <span className="cap">Step</span>
          {(Object.keys(MODES) as Mode[]).map((m) => (
            <button key={m} type="button" role="radio" aria-checked={mode === m} className={mode === m ? 'on' : ''} onClick={() => setMode(m)} title={`${MODES[m].label}: ${MODES[m].small} mm and ${MODES[m].big} mm steps`}>
              <b>{MODES[m].label}</b>
              <span>
                {MODES[m].small}/{MODES[m].big}
              </span>
            </button>
          ))}
          <span className="flex-1" />
          <button type="button" className={`homeall ${homed === 'xyz' ? '' : 'need'}`} disabled={printing} onClick={() => tracked('G28')} title={homed === 'xyz' ? 'Home every axis again' : `${'XYZ'.split('').filter((a) => !homed.includes(a.toLowerCase())).join(', ')} not homed`}>
            <House size={15} />
            <span>Home all</span>
          </button>
          {canFree && (homed !== 'xyz' || free) && (
            <button
              type="button"
              className={`nohome ${free ? 'on' : ''}`}
              aria-pressed={free}
              disabled={printing}
              onClick={() => onFree(!free)}
              title={free ? 'Moving without homing: unhomed axes have no limits and the motors switch off after each move. Click to stop.' : 'Move unhomed axes anyway, with no limits'}
            >
              {free ? <LockOpen size={15} /> : <Lock size={15} />}
              <span>{free ? 'Unhomed' : 'No homing'}</span>
            </button>
          )}
          {steppersOn && !free && (
            <button type="button" className="motoroff" disabled={printing} onClick={() => send('M84')} title="Switch the motors off so the toolhead and bed move by hand. Every axis needs homing again afterwards.">
              <Power size={15} />
              <span>Motors off</span>
            </button>
          )}
        </div>
        <JogWheel small={small} big={big} canX={can('x')} canY={can('y')} canHome={!printing} onJog={move} onHome={() => tracked('G28 X Y')} hotX={free && !homed.includes('x')} hotY={free && !homed.includes('y')} />
        <div className="zrock" role="group" aria-label="Move Z">
          <span className="cap">Bed down</span>
          <button className={zHot} disabled={!can('z')} onClick={() => jogZ(zBig)} title={`Z+ ${zBig} mm: the bed moves down${big > Z_STEP_MAX ? `. Z steps stop at ${Z_STEP_MAX} mm.` : ''}`}>
            <ChevronsUp size={15} /> {zBig}
          </button>
          <button className={zHot} disabled={!can('z')} onClick={() => jogZ(small)} title={`Z+ ${small} mm: the bed moves down`}>
            <ArrowUp size={14} /> {small}
          </button>
          <button className="home" disabled={printing} onClick={() => tracked('G28 Z')} title="Home Z">
            <House size={14} /> Z
          </button>
          <button className={zHot} disabled={!can('z')} onClick={() => jogZ(-small)} title={`Z− ${small} mm: the bed moves up`}>
            <ArrowDown size={14} /> {small}
          </button>
          <button className={zHot} disabled={!can('z')} onClick={() => jogZ(-zBig)} title={`Z− ${zBig} mm: the bed moves up`}>
            <ChevronsDown size={15} /> {zBig}
          </button>
          <span className="cap">Bed up</span>
        </div>
      </div>
    </div>
  )
}

// ---- Move without homing ----
/**
 * A relative move on an unhomed axis. Klipper refuses moves before homing, so this tells it the unhomed axes sit mid-travel
 * (homed axes keep their real position), makes the move, then runs M84. On QIDI's Klipper, SET_KINEMATIC_POSITION marks
 * every axis homed and only a motors-off clears that again; without it the printer would trust the made-up position.
 */
let inflight = 0
/** Sends G-code and counts it until Klipper finishes it, so an unhomed move can't queue behind a home it would undo. */
function tracked(script: string) {
  inflight++
  return send(script).finally(() => inflight--)
}

function unhomedMove(axis: 'X' | 'Y' | 'Z', d: number) {
  const st = printer.get().status
  if (inflight > 0 || st.idle_timeout?.state === 'Printing') return toast('Wait for the printer to finish the last command.', 'error')
  const th = st.toolhead ?? {}
  const homed = (th.homed_axes as string | undefined) ?? ''
  // Homed since the last render: jog it the normal way.
  if (homed.includes(axis.toLowerCase())) return jogAxis(axis, d, axis === 'Z' ? Z_SPEED : XY_SPEED, null)
  const lo = th.axis_minimum as number[] | undefined
  const hi = th.axis_maximum as number[] | undefined
  if (!lo || !hi) return
  const set = (['X', 'Y', 'Z'] as const)
    .map((a, i) => (homed.includes(a.toLowerCase()) ? '' : `${a}=${((lo[i] + hi[i]) / 2).toFixed(1)}`))
    .filter(Boolean)
    .join(' ')
  const rate = axis === 'Z' ? Z_SPEED : UNHOMED_XY_SPEED
  return tracked(['SET_KINEMATIC_POSITION ' + set, 'G91', `G1 ${axis}${d} F${rate * 60}`, 'G90', 'M84'].join('\n'))
}

/** On after a one-time confirm (none when that's turned off in Settings), and off again when a print starts or every axis is homed. */
function useMoveUnhomed(printing: boolean, allHomed: boolean) {
  const [on, setOn] = useState(false)
  const [warn] = useUiSetting('general.forceMoveToggleWarning')
  const set = async (next: boolean) => {
    // The warning shows once per browser: after someone has read and accepted it, the toggle just switches.
    const seen = localStorage.getItem('vo-unhomed-ack') === '1'
    if (next && warn && !seen) {
      if (!(await confirm({ title: 'Move without homing?', body: `Unhomed axes will move with no limits, so the toolhead can hit the frame and the bed can hit the nozzle. After each move the motors switch off, so every axis needs homing again before printing.`, confirm: 'Move without homing', danger: true }))) return
      try {
        localStorage.setItem('vo-unhomed-ack', '1')
      } catch {}
    }
    setOn(next)
  }
  return [on && !printing && !allHomed, set] as const
}

// ---- Extrude ----
function useExtruder() {
  const ext = usePrinter((s) => s.status.extruder ?? {})
  const min = usePrinter((s) => (s.settings.extruder?.min_extrude_temp as number | undefined) ?? 170)
  return { temp: ext.temperature as number | undefined, ready: (ext.can_extrude as boolean | undefined) ?? false, min }
}

function ExtrudeSummary() {
  const box = useBox()
  const { temp, ready, min } = useExtruder()
  const active = box.managesTools ? box.toolButtons.find((b) => b.active) : undefined
  return (
    <>
      {active && `T${active.tool} loaded · `}
      {ready ? `Nozzle ${Math.round(temp ?? 0)}°` : `Nozzle below ${min}°`}
    </>
  )
}

function ExtrudeBody() {
  const printing = usePrinting()
  const clientMacro = useClientMacro()
  const { ready, min } = useExtruder()
  const [len, setLen] = useState(() => Number(localStorage.getItem('vo-th-elen')) || 10)
  const [speed, setSpeed] = useState(5)
  const pickLen = (v: number) => {
    setLen(v)
    try {
      localStorage.setItem('vo-th-elen', String(v))
    } catch {}
  }
  const off = printing || !ready || speed <= 0
  return (
    <div className="grid gap-2.5">
      <BoxTools />
      <div className="th-extrude">
        <button className="btn" disabled={off} onClick={() => send(extrudeGcode(-len, speed, clientMacro))} title={`Pull ${len} mm back`}>
          <ArrowUp size={14} /> Retract
        </button>
        <div className="seg" role="radiogroup" aria-label="Length">
          {E_LENGTHS.map((v) => (
            <button key={v} type="button" role="radio" aria-checked={len === v} className={len === v ? 'on' : ''} onClick={() => pickLen(v)}>
              {v}
            </button>
          ))}
        </div>
        <button className="btn" disabled={off} onClick={() => send(extrudeGcode(len, speed, clientMacro))} title={`Push ${len} mm through the nozzle`}>
          Extrude <ArrowDown size={14} />
        </button>
      </div>
      <div className="flex items-center gap-1.5 text-[12px] text-ink-3">
        {ready ? (
          <span>{len} mm per press</span>
        ) : (
          <span className="inline-flex items-center gap-1 font-semibold text-[var(--blue)]">
            <Snowflake size={12} /> Heat the nozzle to {min} °C to extrude
          </span>
        )}
        <span className="flex-1" />
        at
        <input className="field num h-[28px] w-12 text-right" inputMode="decimal" aria-label="Extrude speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value) || 0)} />
        mm/s
      </div>
    </div>
  )
}

// ---- Tune: the values you change during a print ----
function useTune() {
  return usePrinter((s) => {
    const gm = s.status.gcode_move ?? {}
    const ext = s.status.extruder ?? {}
    return {
      z: (gm.homing_origin?.[2] as number | undefined) ?? 0,
      speed: Math.round(((gm.speed_factor as number | undefined) ?? 1) * 100),
      flow: Math.round(((gm.extrude_factor as number | undefined) ?? 1) * 100),
      pa: ext.pressure_advance as number | undefined,
      smooth: (ext.smooth_time as number | undefined) ?? 0,
    }
  })
}

function TuneSummary() {
  const t = useTune()
  const hi = (changed: boolean, text: string) => <span className={changed ? 'text-accent-ink' : ''}>{text}</span>
  return (
    <>
      {hi(t.z !== 0, `Z ${sign(t.z, 3)}`)} · {hi(t.speed !== 100, `Speed ${t.speed}%`)} · {hi(t.flow !== 100, `Flow ${t.flow}%`)}
      {typeof t.pa === 'number' && ` · PA ${t.pa.toFixed(3)}`}
    </>
  )
}

function TuneBody() {
  const t = useTune()
  const zHomed = usePrinter((s) => ((s.status.toolhead?.homed_axes as string | undefined) ?? '').includes('z'))
  const printing = usePrinting()
  const probe = useHasCommand('Z_OFFSET_APPLY_PROBE')
  const endstop = useHasCommand('Z_OFFSET_APPLY_ENDSTOP')
  const [step, setStep] = useState(Z_STEPS[1])
  const adjust = (d: number) => send(gcode`SET_GCODE_OFFSET Z_ADJUST=${d > 0 ? '+' : ''}${d} MOVE=${+zHomed}`)
  const saves = [endstop && 'Z_OFFSET_APPLY_ENDSTOP', probe && 'Z_OFFSET_APPLY_PROBE'].filter(Boolean) as string[]
  const reset = (changed: boolean, cmd: string) =>
    changed && (
      <button type="button" className="th-reset" onClick={() => send(cmd)}>
        Reset
      </button>
    )
  return (
    <div className="th-tune">
      <span className="k">Z offset {reset(t.z !== 0, `SET_GCODE_OFFSET Z=0 MOVE=${+zHomed}`)}</span>
      <div className="grid gap-1.5">
        <div className="th-zoff">
          <button className="btn" onClick={() => adjust(-step)} title={`Lower the nozzle ${step} mm toward the bed`}>
            Closer
          </button>
          <span className="num v">
            {sign(t.z, 3)}
            <small>mm</small>
          </span>
          <button className="btn" onClick={() => adjust(step)} title={`Raise the nozzle ${step} mm off the bed`}>
            Farther
          </button>
        </div>
        <div className="flex items-center gap-1">
          <div className="th-chips" role="radiogroup" aria-label="Z offset step">
            {Z_STEPS.map((v) => (
              <button key={v} type="button" role="radio" aria-checked={step === v} className={step === v ? 'on' : ''} onClick={() => setStep(v)}>
                {v}
              </button>
            ))}
          </div>
          <span className="flex-1" />
          {saves.length > 0 && (
            <MenuButton
              className="th-reset"
              label="Save Z offset"
              button={<>Save…</>}
              items={saves.map((c) => ({
                label: <span className="code">{c}</span>,
                onSelect: () => send(c),
                disabled: printing || t.z === 0,
              }))}
            />
          )}
        </div>
      </div>

      <span className="k">Speed {reset(t.speed !== 100, 'M220 S100')}</span>
      <Slider label="Speed" value={t.speed} min={10} max={300} step={5} onCommit={(v) => send(gcode`M220 S${Math.round(v)}`)} format={(v) => `${v}%`} />

      <span className="k">Flow {reset(t.flow !== 100, 'M221 S100')}</span>
      <Slider label="Flow" value={t.flow} min={50} max={150} step={1} onCommit={(v) => send(gcode`M221 S${Math.round(v)}`)} format={(v) => `${v}%`} />

      {typeof t.pa === 'number' && (
        <>
          <span className="k">Pressure advance</span>
          <Stepper label="Pressure advance" value={t.pa} step={0.001} min={0} max={2} digits={3} onChange={(v) => send(gcode`SET_PRESSURE_ADVANCE ADVANCE=${v}`)} />
          <span className="k">Smooth time</span>
          <Stepper label="Smooth time" value={t.smooth} step={0.005} min={0} max={0.2} digits={3} unit="s" onChange={(v) => send(gcode`SET_PRESSURE_ADVANCE SMOOTH_TIME=${v}`)} />
        </>
      )}
    </div>
  )
}

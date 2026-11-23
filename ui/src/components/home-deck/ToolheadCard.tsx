// Mockup B's toolhead card: one position line, then Move, Extrude, and Tune sections that fold to a one-line summary.
// Which sections start open depends on whether a print is running; a section the user opens or closes stays that way.
import { useState, type ReactNode } from 'react'
import { ArrowDown, ArrowUp, ChevronRight, ChevronsDown, ChevronsUp, Ellipsis, House, Move3d, Lock, LockOpen, Pipette, Power, SlidersHorizontal, Snowflake } from 'lucide-react'
import { MenuButton, Stepper, confirm } from '../ui'
import { NumberInput, Slider, jog as jogAxis, send, useHomedAxes, usePercent, useZOffset } from '../control/common'
import { usePrinting } from '../../state/job'
import { extrudeGcode, gcode, moveGcode } from '../control/gcode'
import { Z_SPEED, Z_STEPS, Z_STEP_MAX, axisSpeed, useClientMacro, useSteppersOn, useToolItems } from '../control/Move'
import { BoxTools } from '../box/BoxTools'
import { printer, useHasCommand, usePrinter } from '../../state/printer'
import { useUiSetting } from '../settings/fluidd'
import { useBox } from '../../state/box'
import { toast } from '../toast'
import { readLocal, saveLocal, useStored } from '../../lib/browser'
import { t } from '../../i18n'
import { tr } from '../../i18n/rich'
import { JogWheel, MODES, useMode, type Mode } from './Deck'
import './deck.css'

const UNHOMED_XY_SPEED = 50
const E_LENGTHS = [5, 10, 25, 50]

const sign = (v: number, d: number) => `${v >= 0 ? '+' : ''}${v.toFixed(d)}`

export function ToolheadCard({ className = '' }: { className?: string }) {
  const homed = useHomedAxes()
  const printing = usePrinting()
  const ctx = printing ? 'printing' : 'idle'
  const missing = 'XYZ'.split('').filter((a) => !homed.includes(a.toLowerCase()))
  const [free, setFree] = useMoveUnhomed(printing, missing.length === 0)
  return (
    <section className={`card th ${className}`}>
      <h3 className="card-title">
        {t('control.toolhead.title')}
        <span className="aside flex items-center gap-1">
          <MoreMenu />
        </span>
      </h3>
      <Readout homed={homed} printing={printing} />
      <Fold id="move" ctx={ctx} title={t('control.screen.tab_move')} icon={<Move3d size={13} />} def locked={printing && t('home.toolhead.locked_pause')} summary={free ? <span className="text-[var(--red)]">{t('home.toolhead.moving_unhomed')}</span> : <MoveSummary />}>
        <MoveBody homed={homed} free={free} onFree={setFree} />
      </Fold>
      <Fold id="extrude" ctx={ctx} title={t('control.extruder.extrude')} icon={<Pipette size={13} />} def={false} locked={printing && t('home.toolhead.locked')} summary={<ExtrudeSummary />}>
        <ExtrudeBody />
      </Fold>
      <Fold id="tune" ctx={ctx} title={t('home.tune.title')} icon={<SlidersHorizontal size={13} />} def={false} summary={<TuneSummary />}>
        <TuneBody />
      </Fold>
    </section>
  )
}

/** The toolhead tools (clean, park, calibrations) behind one button; homing has its own buttons on the card. */
function MoreMenu() {
  const items = useToolItems()
  if (!items.some(Boolean)) return null
  return <MenuButton label={t('home.toolhead.more')} button={<Ellipsis size={16} />} items={items} />
}

/** One number per axis: the live position, which you can click and type over to move there. An axis that isn't homed shows in amber. */
function Readout({ homed, printing }: { homed: string; printing: boolean }) {
  const live = usePrinter((s) => (s.status.motion_report?.live_position as number[] | undefined) ?? null)
  const toolhead = usePrinter((s) => (s.status.toolhead?.position as number[] | undefined) ?? null)
  const clientMacro = useClientMacro()
  return (
    <div className="th-readout" role="group" aria-label={t('control.toolhead.position')}>
      {(['X', 'Y', 'Z'] as const).map((a, i) => {
        const ok = homed.includes(a.toLowerCase())
        return (
          <label key={a} title={ok ? t('home.toolhead.position_hint', { axis: a }) : t('home.toolhead.position_unhomed', { axis: a })}>
            <span className={ok ? 'ax' : 'ax warn'}>{a}</span>
            <NumberInput
              label={t('control.toolhead.move_to', { axis: a })}
              value={live?.[i] ?? toolhead?.[i] ?? 0}
              digits={i === 2 ? 3 : 2}
              disabled={!ok}
              readOnly={printing}
              onCommit={(v) => send(moveGcode({ [a]: v }, axisSpeed(a), true, clientMacro))}
            />
          </label>
        )
      })}
    </div>
  )
}

/** A section heading that folds the section. Closed, it shows a one-line summary instead. */
function Fold({ id, ctx, title, icon, def, locked, summary, children }: { id: string; ctx: string; title: string; icon: ReactNode; def: boolean; locked?: string | false; summary: ReactNode; children: ReactNode }) {
  const [saved, save] = useStored(`vo-th-${id}-${ctx}`, def ? '1' : '0')
  const open = !locked && saved === '1'
  const toggle = () => save(open ? '0' : '1')
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
      {t('home.toolhead.move_summary', { mode: t(MODES[mode].labelKey), small: MODES[mode].small, big: MODES[mode].big })}
    </>
  )
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
  const move = (axis: 'X' | 'Y' | 'Z', d: number) => (homed.includes(axis.toLowerCase()) ? jogAxis(axis, d, axisSpeed(axis), clientMacro) : unhomedMove(axis, d))
  const zHot = free && !homed.includes('z') ? 'hot' : ''
  const jogZ = (d: number, icon: ReactNode, title: string) => (
    <button className={zHot} disabled={!can('z')} onClick={() => move('Z', d)} title={title}>
      {icon} {Math.abs(d)}
    </button>
  )
  return (
    <div className="grid gap-3">
      <div className={`th-move ${free ? 'free' : ''}`}>
        <div className="steprock" role="group" aria-label={t('home.toolhead.step_size')}>
          <span className="cap">{t('control.z_offset.step')}</span>
          {(Object.keys(MODES) as Mode[]).map((m) => (
            <button key={m} type="button" aria-pressed={mode === m} className={mode === m ? 'on' : ''} onClick={() => setMode(m)} title={t('home.toolhead.mode_title', { mode: t(MODES[m].labelKey), small: MODES[m].small, big: MODES[m].big })}>
              <b>{t(MODES[m].labelKey)}</b>
              <span>
                {MODES[m].small}/{MODES[m].big}
              </span>
            </button>
          ))}
          <span className="flex-1" />
          <button type="button" className={`homeall ${homed === 'xyz' ? '' : 'need'}`} disabled={printing} onClick={() => tracked('G28')} title={homed === 'xyz' ? t('home.toolhead.home_again') : t('control.toolhead.axes_not_homed', { axes: 'XYZ'.split('').filter((a) => !homed.includes(a.toLowerCase())).join(', ') })}>
            <House size={15} />
            <span>{t('tune.shared.home_all')}</span>
          </button>
          {canFree && (homed !== 'xyz' || free) && (
            <button
              type="button"
              className={`nohome ${free ? 'on' : ''}`}
              aria-pressed={free}
              disabled={printing}
              onClick={() => onFree(!free)}
              title={free ? t('home.toolhead.unhomed_on_title') : t('home.toolhead.unhomed_off_title')}
            >
              {free ? <LockOpen size={15} /> : <Lock size={15} />}
              <span>{free ? t('home.toolhead.unhomed') : t('home.toolhead.no_homing')}</span>
            </button>
          )}
          {steppersOn && !free && (
            <button type="button" className="motoroff" disabled={printing} onClick={() => send('M84')} title={t('home.toolhead.motors_off_title')}>
              <Power size={15} />
              <span>{t('control.toolhead.motors_off')}</span>
            </button>
          )}
        </div>
        <JogWheel small={small} big={big} canX={can('x')} canY={can('y')} canHome={!printing} onJog={move} onHome={() => tracked('G28 X Y')} hotX={free && !homed.includes('x')} hotY={free && !homed.includes('y')} />
        <div className="zrock" role="group" aria-label={t('control.toolhead.move_z')}>
          <span className="cap">{t('home.toolhead.bed_down')}</span>
          {jogZ(zBig, <ChevronsUp size={15} />, big > Z_STEP_MAX ? t('home.toolhead.z_plus_capped', { step: zBig, max: Z_STEP_MAX }) : t('home.toolhead.z_plus', { step: zBig }))}
          {jogZ(small, <ArrowUp size={14} />, t('home.toolhead.z_plus', { step: small }))}
          <button className="home" disabled={printing} onClick={() => tracked('G28 Z')} title={t('control.toolhead.home_z')}>
            <House size={14} /> Z
          </button>
          {jogZ(-small, <ArrowDown size={14} />, t('home.toolhead.z_minus', { step: small }))}
          {jogZ(-zBig, <ChevronsDown size={15} />, t('home.toolhead.z_minus', { step: zBig }))}
          <span className="cap">{t('home.toolhead.bed_up')}</span>
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
  if (inflight > 0 || st.idle_timeout?.state === 'Printing') return toast(t('home.toolhead.wait_last'), 'error')
  const th = st.toolhead ?? {}
  const homed = (th.homed_axes as string | undefined) ?? ''
  // Homed since the last render: jog it the normal way.
  if (homed.includes(axis.toLowerCase())) return jogAxis(axis, d, axisSpeed(axis), null)
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
    if (next && warn && readLocal('vo-unhomed-ack') !== '1') {
      if (!(await confirm({ title: t('home.toolhead.unhomed_confirm_title'), body: t('home.toolhead.unhomed_confirm_body'), confirm: t('home.toolhead.unhomed_confirm'), danger: true }))) return
      saveLocal('vo-unhomed-ack', '1')
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
      {active && `${t('home.toolhead.tool_loaded', { tool: active.tool })} · `}
      {ready ? t('home.toolhead.nozzle_temp', { temp: Math.round(temp ?? 0) }) : t('home.toolhead.nozzle_below', { min })}
    </>
  )
}

function ExtrudeBody() {
  const printing = usePrinting()
  const clientMacro = useClientMacro()
  const { ready, min } = useExtruder()
  const [savedLen, saveLen] = useStored<string>('vo-th-elen', '10')
  const len = Number(savedLen) || 10
  const [speed, setSpeed] = useState(5)
  const off = printing || !ready || speed <= 0
  return (
    <div className="th-extrude-fold grid min-w-0 grid-cols-1 gap-2.5">
      <BoxTools />
      <div className="th-extrude">
        <button className="btn" disabled={off} onClick={() => send(extrudeGcode(-len, speed, clientMacro))} title={t('home.toolhead.retract_title', { length: len })}>
          <ArrowUp size={14} /> {t('control.extruder.retract')}
        </button>
        <div className="seg" role="group" aria-label={t('control.extruder.length')}>
          {E_LENGTHS.map((v) => (
            <button key={v} type="button" aria-pressed={len === v} className={len === v ? 'on' : ''} onClick={() => saveLen(String(v))}>
              {v}
            </button>
          ))}
        </div>
        <button className="btn" disabled={off} onClick={() => send(extrudeGcode(len, speed, clientMacro))} title={t('home.toolhead.extrude_title', { length: len })}>
          {t('control.extruder.extrude')} <ArrowDown size={14} />
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 text-[12px] text-ink-3">
        {ready ? (
          <span>{t('home.toolhead.per_press', { length: len })}</span>
        ) : (
          <span className="inline-flex items-center gap-1 font-semibold text-[var(--blue)]">
            <Snowflake size={12} /> {t('home.toolhead.heat_to_extrude', { min })}
          </span>
        )}
        <span className="flex-1" />
        {tr('home.toolhead.at_speed', {
          speed: <input className="field num h-[28px] w-12 text-right" inputMode="decimal" aria-label={t('control.extruder.extrude_speed')} value={speed} onChange={(e) => setSpeed(Number(e.target.value) || 0)} />,
        })}
      </div>
    </div>
  )
}

// ---- Tune: the values you change during a print ----
function useTune() {
  const ext = usePrinter((s) => ({ pa: s.status.extruder?.pressure_advance as number | undefined, smooth: (s.status.extruder?.smooth_time as number | undefined) ?? 0 }))
  return { z: useZOffset(), speed: usePercent('speed_factor'), flow: usePercent('extrude_factor'), ...ext }
}

function TuneSummary() {
  const v = useTune()
  const hi = (changed: boolean, text: string) => <span className={changed ? 'text-accent-ink' : ''}>{text}</span>
  return (
    <>
      {hi(v.z !== 0, `Z ${sign(v.z, 3)}`)} · {hi(v.speed !== 100, t('home.job.speed', { pct: v.speed }))} · {hi(v.flow !== 100, t('home.job.flow', { pct: v.flow }))}
      {/* i18n-ignore: PA is pressure advance, which stays in English */}
      {typeof v.pa === 'number' && ` · PA ${v.pa.toFixed(3)}`}
    </>
  )
}

function TuneBody() {
  const v = useTune()
  const zHomed = useHomedAxes().includes('z')
  const printing = usePrinting()
  const probe = useHasCommand('Z_OFFSET_APPLY_PROBE')
  const endstop = useHasCommand('Z_OFFSET_APPLY_ENDSTOP')
  const [step, setStep] = useState(Z_STEPS[1])
  const adjust = (d: number) => send(gcode`SET_GCODE_OFFSET Z_ADJUST=${d > 0 ? '+' : ''}${d} MOVE=${+zHomed}`)
  const saves = [endstop && 'Z_OFFSET_APPLY_ENDSTOP', probe && 'Z_OFFSET_APPLY_PROBE'].filter(Boolean) as string[]
  const reset = (changed: boolean, cmd: string) =>
    changed && (
      <button type="button" className="th-reset" onClick={() => send(cmd)}>
        {t('control.speed_flow.reset')}
      </button>
    )
  return (
    <div className="th-tune">
      <span className="k th-tune-z-label">{t('home.tune.z_offset')} {reset(v.z !== 0, `SET_GCODE_OFFSET Z=0 MOVE=${+zHomed}`)}</span>
      <div className="grid gap-1.5 th-tune-z-controls">
        <div className="th-zoff">
          <button className="btn" onClick={() => adjust(-step)} title={t('home.toolhead.closer_title', { step })}>
            {t('home.toolhead.closer')}
          </button>
          <span className="num v">
            {sign(v.z, 3)}
            <small>mm</small>
          </span>
          <button className="btn" onClick={() => adjust(step)} title={t('home.toolhead.farther_title', { step })}>
            {t('home.toolhead.farther')}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <div className="th-chips" role="group" aria-label={t('home.tune.z_step')}>
            {Z_STEPS.map((v) => (
              <button key={v} type="button" aria-pressed={step === v} className={step === v ? 'on' : ''} onClick={() => setStep(v)}>
                {v}
              </button>
            ))}
          </div>
          <span className="flex-1" />
          {saves.length > 0 && (
            <MenuButton
              className="th-reset"
              label={t('control.z_offset.save_label')}
              button={<>{t('home.toolhead.save_menu')}</>}
              items={saves.map((c) => ({
                label: <span className="code">{c}</span>,
                onSelect: () => send(c),
                disabled: printing || v.z === 0,
              }))}
            />
          )}
        </div>
      </div>

      <span className="k">{t('home.tune.speed')} {reset(v.speed !== 100, 'M220 S100')}</span>
      <Slider label={t('home.tune.speed')} value={v.speed} min={10} max={300} step={5} onCommit={(v) => send(gcode`M220 S${Math.round(v)}`)} format={(v) => `${v}%`} />

      <span className="k">{t('home.tune.flow')} {reset(v.flow !== 100, 'M221 S100')}</span>
      <Slider label={t('home.tune.flow')} value={v.flow} min={50} max={150} step={1} onCommit={(v) => send(gcode`M221 S${Math.round(v)}`)} format={(v) => `${v}%`} />

      {typeof v.pa === 'number' && (
        <>
          <span className="k">{t('control.extruder.pressure_advance')}</span>
          <Stepper label={t('control.extruder.pressure_advance')} value={v.pa} step={0.001} min={0} max={2} digits={3} onChange={(v) => send(gcode`SET_PRESSURE_ADVANCE ADVANCE=${v}`)} />
          <span className="k">{t('control.extruder.smooth_time')}</span>
          <Stepper label={t('control.extruder.smooth_time')} value={v.smooth} step={0.005} min={0} max={0.2} digits={3} unit="s" onChange={(v) => send(gcode`SET_PRESSURE_ADVANCE SMOOTH_TIME=${v}`)} />
        </>
      )}
    </div>
  )
}

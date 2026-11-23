import { useState, type ReactNode } from 'react'
import { BoxToolsCard } from '../box/BoxTools'
import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, ChevronDown, Gauge, House, Move as MoveIcon, Pipette, Snowflake, Wrench } from 'lucide-react'
import { Card, Choices, MenuButton, Segmented, Stepper, confirm } from '../ui'
import { NumberInput, Note, jog as jogAxis, send, usePrinting, useHasCommand } from './common'
import { extrudeGcode, gcode, moveGcode } from './gcode'
import { registerObjects, usePrinter } from '../../state/printer'
import { createStore, useStore } from '../../lib/store'
import { t } from '../../i18n'
import { tr } from '../../i18n/rich'

registerObjects((n) => n === 'bed_screws' || n === 'gcode_macro _CLIENT_LINEAR_MOVE')

// Fluidd's defaults (uiSettings.general): 130 mm/s for X and Y, 10 mm/s for Z, and 10 mm at 5 mm/s for the extruder.
const XY_SPEED = 130
const Z_SPEED = 10
const STEPS = [0.1, 1, 10, 25, 50, 100]
// A long Z jog with the bed under the nozzle is the easiest crash to make, so Z steps stop at 25 mm.
const Z_STEP_MAX = 25

// Kept for the session, so switching tabs doesn't reset them.
const prefs = createStore({ step: 10, eLength: 10, eSpeed: 5, gcodeCoords: false })
const usePrefs = () => useStore(prefs, (s) => s)
const setPref = (p: Partial<ReturnType<typeof prefs.get>>) => prefs.set((s) => ({ ...s, ...p }))

export function useClientMacro() {
  return usePrinter((s) => (s.objects.includes('gcode_macro _CLIENT_LINEAR_MOVE') ? '_CLIENT_LINEAR_MOVE' : null))
}

/** The Move tab: the toolhead, the extruder, Z offset, and speed and flow. */
export function MoveTab() {
  return (
    <div className="grid grid-cols-1 items-start gap-3.5 lg:grid-cols-2">
      <div className="grid min-w-0 grid-cols-1 gap-3.5">
        <ToolheadCard />
      </div>
      <div className="grid min-w-0 grid-cols-1 gap-3.5">
        <BoxToolsCard />
        <ExtruderCard />
        <ZOffsetCard />
        <SpeedFlowCard />
      </div>
    </div>
  )
}

// ---- toolhead: jog, home, position ----
export function ToolheadCard() {
  const homed = usePrinter((s) => (s.status.toolhead?.homed_axes as string | undefined) ?? '')
  const printing = usePrinting()
  const steppersOn = usePrinter((s) => {
    const st = s.status.stepper_enable?.steppers as Record<string, boolean> | undefined
    return st ? Object.values(st).some(Boolean) : true
  })
  const clientMacro = useClientMacro()
  const { step } = usePrefs()
  const can = (axis: string) => !printing && homed.includes(axis)
  const jog = (axis: 'X' | 'Y' | 'Z', dir: 1 | -1) => jogAxis(axis, dir * (axis === 'Z' ? Math.min(step, Z_STEP_MAX) : step), axis === 'Z' ? Z_SPEED : XY_SPEED, clientMacro)
  const missing = 'XYZ'.split('').filter((a) => !homed.includes(a.toLowerCase()))
  return (
    <Card
      title={t('control.toolhead.title')}
      icon={<MoveIcon size={14} />}
      aside={<span className={`pill ${missing.length ? 'tone-warn' : 'tone-good'}`}>{missing.length === 3 ? t('control.toolhead.not_homed') : missing.length ? t('control.toolhead.axes_not_homed', { axes: missing.join('') }) : t('control.toolhead.homed')}</span>}
    >
      <div className="flex flex-wrap items-start gap-x-6 gap-y-4">
        <div className="flex gap-3">
          <div className="grid grid-cols-3 gap-1.5" role="group" aria-label={t('control.toolhead.move_xy')}>
            <span />
            <Jog label="Y+" disabled={!can('y')} onClick={() => jog('Y', 1)}>
              <ArrowUp size={18} />
              <span className="ax">Y+</span>
            </Jog>
            <span />
            <Jog label="X−" disabled={!can('x')} onClick={() => jog('X', -1)}>
              <ArrowLeft size={18} />
              <span className="ax">X−</span>
            </Jog>
            <Jog label={t('control.toolhead.home_xy')} home disabled={printing} onClick={() => send('G28 X Y')}>
              <House size={17} />
              <span className="ax">XY</span>{/* i18n-ignore: axis names */}
            </Jog>
            <Jog label="X+" disabled={!can('x')} onClick={() => jog('X', 1)}>
              <ArrowRight size={18} />
              <span className="ax">X+</span>
            </Jog>
            <span />
            <Jog label="Y−" disabled={!can('y')} onClick={() => jog('Y', -1)}>
              <ArrowDown size={18} />
              <span className="ax">Y−</span>
            </Jog>
            <span />
          </div>
          <div className="grid grid-rows-3 gap-1.5" role="group" aria-label={t('control.toolhead.move_z')}>
            <Jog label={t('control.toolhead.z_up')} disabled={!can('z')} onClick={() => jog('Z', 1)}>
              <ArrowUp size={18} />
              <span className="ax">Z+</span>
            </Jog>
            <Jog label={t('control.toolhead.home_z')} home disabled={printing} onClick={() => send('G28 Z')}>
              <House size={17} />
              <span className="ax">Z</span>
            </Jog>
            <Jog label={t('control.toolhead.z_down')} disabled={!can('z')} onClick={() => jog('Z', -1)}>
              <ArrowDown size={18} />
              <span className="ax">Z−</span>
            </Jog>
          </div>
        </div>
        <div className="grid min-w-[220px] flex-1 content-start gap-3">
          <div>
            <div className="section-label mb-1.5">{t('control.toolhead.step')}</div>
            <Choices value={step} options={STEPS.map((v) => ({ value: v, label: `${v}` }))} onChange={(v) => setPref({ step: v })} />
            <div className="mt-1.5">
              <Note>{t('control.toolhead.z_note', { max: Z_STEP_MAX })}</Note>
            </div>
          </div>
          <div>
            <div className="section-label mb-1.5">{t('control.toolhead.home')}</div>
            <div className="flex flex-wrap gap-1.5">
              <button className="btn btn-sm" disabled={printing} onClick={() => send('G28 X')}>
                X
              </button>
              <button className="btn btn-sm" disabled={printing} onClick={() => send('G28 Y')}>
                Y
              </button>
              <button className="btn btn-sm" disabled={printing} onClick={() => send('G28 Z')}>
                Z
              </button>
              <button className="btn btn-sm btn-primary" disabled={printing} onClick={() => send('G28')}>
                <House size={13} /> {t('control.toolhead.home_all')}
              </button>
              <button className="btn btn-sm" disabled={printing || !steppersOn} onClick={() => send('M84')}>
                {t('control.toolhead.motors_off')}
              </button>
            </div>
          </div>
        </div>
      </div>
      <Position />
      {printing ? <Note>{t('control.toolhead.locked_printing')}</Note> : missing.length ? <Note>{t('control.toolhead.home_first')}</Note> : null}
    </Card>
  )
}

function Jog({ label, disabled, onClick, children, home }: { label: string; disabled?: boolean; onClick: () => void; children: ReactNode; home?: boolean }) {
  return (
    <button type="button" title={label} aria-label={label} disabled={disabled} onClick={onClick} className={`btn jog ${home ? 'home' : ''}`}>
      <span className="grid justify-items-center gap-0.5">{children}</span>
    </button>
  )
}

/** Live, toolhead, and G-code positions, with a move to an entered position. */
function Position() {
  const live = usePrinter((s) => (s.status.motion_report?.live_position as number[] | undefined) ?? null)
  const toolhead = usePrinter((s) => (s.status.toolhead?.position as number[] | undefined) ?? null)
  const gcodePos = usePrinter((s) => (s.status.gcode_move?.gcode_position as number[] | undefined) ?? null)
  const absolute = usePrinter((s) => s.status.gcode_move?.absolute_coordinates !== false)
  const homed = usePrinter((s) => (s.status.toolhead?.homed_axes as string | undefined) ?? '')
  const printing = usePrinting()
  const clientMacro = useClientMacro()
  const { gcodeCoords } = usePrefs()
  const shown = gcodeCoords ? gcodePos : toolhead
  const moveTo = (i: number, v: number) => {
    const axis = (['X', 'Y', 'Z'] as const)[i]
    send(moveGcode({ [axis]: v }, axis === 'Z' ? Z_SPEED : XY_SPEED, true, clientMacro))
  }
  return (
    <div className="mt-4 grid gap-2 border-t border-line pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="section-label mr-auto">{t('control.toolhead.position')}</span>
        <Segmented
          label={t('control.toolhead.coordinates')}
          value={gcodeCoords ? 'gcode' : 'toolhead'}
          options={[
            { value: 'toolhead', label: t('control.toolhead.coords_toolhead') },
            { value: 'gcode', label: 'G-code' },
          ]}
          onChange={(v) => setPref({ gcodeCoords: v === 'gcode' })}
        />
        <Segmented
          label={t('control.toolhead.positioning')}
          value={absolute ? 'abs' : 'rel'}
          options={[
            { value: 'abs', label: t('control.toolhead.absolute'), disabled: printing },
            { value: 'rel', label: t('control.toolhead.relative'), disabled: printing },
          ]}
          onChange={(v) => send(v === 'abs' ? 'G90' : 'G91')}
        />
      </div>
      <div className="grid grid-cols-3 gap-2">
        {['X', 'Y', 'Z'].map((a, i) => (
          <label key={a} className="grid gap-1">
            <span className="lbl flex justify-between gap-1">
              <span>{a}</span>
              <span className="num" title={t('control.toolhead.live_position')}>
                {live ? live[i].toFixed(i === 2 ? 3 : 2) : ''}
              </span>
            </span>
            <NumberInput
              label={t('control.toolhead.move_to', { axis: a })}
              value={shown?.[i] ?? 0}
              digits={i === 2 ? 3 : 2}
              disabled={!homed.includes(a.toLowerCase())}
              readOnly={printing}
              onCommit={(v) => moveTo(i, v)}
            />
          </label>
        ))}
      </div>
      <Note>{t('control.toolhead.position_note')}</Note>
    </div>
  )
}

// ---- calibration tools, manual probe, bed screws ----
type Tool = { name: string; label?: string; disabled?: boolean; homeFirst?: boolean } | null

export function useTools(): Tool[] {
  const settings = usePrinter((s) => s.settings)
  const objects = usePrinter((s) => s.objects)
  const probing = usePrinter((s) => s.status.manual_probe?.is_active === true)
  const macro = (...names: string[]) => names.map((n) => objects.find((o) => o.toLowerCase() === `gcode_macro ${n.toLowerCase()}`)).find(Boolean)?.slice(12)
  const description = (name: string) => {
    const d = settings[`gcode_macro ${name.toLowerCase()}`]?.description
    return d && d !== 'G-Code macro' ? d : null
  }
  const tools: Tool[] = []
  const park = macro('PARK_TOOLHEAD', 'TOOLHEAD_PARK', 'G27')
  const clean = macro('CLEAN_NOZZLE', 'NOZZLE_CLEAN', 'WIPE_NOZZLE', 'NOZZLE_WIPE', 'G12')
  if (clean) tools.push({ name: clean.toUpperCase(), label: clean.toLowerCase() === 'g12' ? `G12 (${description(clean) ?? t('control.tools.clean_nozzle')})` : undefined })
  if (park) tools.push({ name: park.toUpperCase(), label: park.toLowerCase() === 'g27' ? `G27 (${description(park) ?? t('control.tools.park_toolhead')})` : undefined, homeFirst: true })
  if (tools.length) tools.push(null)
  const calib = (name: string) => tools.push({ name, homeFirst: true, disabled: probing })
  if (settings.bed_screws) calib('BED_SCREWS_ADJUST')
  if (settings.bed_tilt) calib('BED_TILT_CALIBRATE')
  if (settings.delta_calibrate) calib('DELTA_CALIBRATE')
  calib('MANUAL_PROBE')
  if (settings.probe || settings.bltouch || settings.smart_effector) {
    calib('PROBE_ACCURACY')
    calib('PROBE_CALIBRATE')
  }
  if (settings.quad_gantry_level) calib('QUAD_GANTRY_LEVEL')
  if (settings.screws_tilt_adjust) calib('SCREWS_TILT_CALCULATE')
  if (settings.stepper_z?.position_endstop != null) calib('Z_ENDSTOP_CALIBRATE')
  if (settings.z_tilt) calib('Z_TILT_ADJUST')
  return tools
}

/** The Tools menu in the page header: the printer's calibration commands, as Fluidd's toolhead card offers them. Tools that need the printer homed home it first. */
export function ToolsMenu() {
  const tools = useTools()
  const homed = usePrinter((s) => s.status.toolhead?.homed_axes === 'xyz')
  const printing = usePrinting()
  const items = tools.map((tool) => (tool ? { label: <span className="code">{tool.label ?? tool.name}</span>, onSelect: () => send(tool.homeFirst && !homed ? `G28\n${tool.name}` : tool.name), disabled: tool.disabled } : null))
  return (
    <MenuButton
      className="btn btn-sm"
      label={t('control.tools.title')}
      button={
        <>
          <Wrench size={13} /> {t('control.tools.title')} <ChevronDown size={13} />
        </>
      }
      items={printing ? items.map((i) => i && { ...i, disabled: true }) : items}
    />
  )
}

// ---- extruder ----
export function ExtruderCard() {
  const ext = usePrinter((s) => s.status.extruder ?? {})
  const cfg = usePrinter((s) => s.settings.extruder ?? {})
  const factor = usePrinter((s) => (s.status.gcode_move?.extrude_factor as number | undefined) || 1)
  const direction = usePrinter((s) => Math.sign((s.status.motion_report?.live_extruder_velocity as number | undefined) ?? 0))
  const printing = usePrinting()
  const clientMacro = useClientMacro()
  const { eLength, eSpeed } = usePrefs()
  const [details, setDetails] = useState(false)
  const ready = ext.can_extrude ?? (typeof ext.temperature === 'number' && ext.temperature >= (cfg.min_extrude_temp ?? 170))
  const maxLength = (cfg.max_extrude_only_distance as number | undefined) || 50
  const maxSpeed = (cfg.max_extrude_only_velocity as number | undefined) || 500
  const fil = (cfg.filament_diameter as number | undefined) || 1.75
  const noz = (cfg.nozzle_diameter as number | undefined) || 0.4
  const layer = 0.2
  const length = Math.round(eLength * factor * (fil ** 2 / noz ** 2) * 10) / 10
  const flow = Math.round((Math.PI / 4) * fil ** 2 * eSpeed * 10) / 10
  const maxPrintSpeed = Math.round((flow / (layer * (noz + layer * (Math.PI / 4 - 1)))) * 10) / 10
  const valid = eLength > 0 && eLength <= maxLength && eSpeed > 0 && eSpeed <= maxSpeed
  const hasPa = typeof ext.pressure_advance === 'number'
  return (
    <Card
      title={t('control.extruder.title')}
      icon={<Pipette size={14} />}
      aside={
        !ready ? (
          <span className="pill tone-info">
            <Snowflake size={12} /> {t('control.extruder.cold')}
          </span>
        ) : direction ? (
          <span className={`pill ${direction < 0 ? 'tone-bad' : 'tone-good'}`}>{direction < 0 ? t('control.extruder.retracting') : t('control.extruder.extruding')}</span>
        ) : undefined
      }
    >
      {!printing && (
        <>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1">
              <span className="section-label">{t('control.extruder.length')}</span>
              <Stepper label={t('control.extruder.extrude_length')} value={eLength} onChange={(v) => setPref({ eLength: v })} step={5} min={0.1} max={maxLength} digits={1} unit="mm" />
            </div>
            <div className="grid gap-1">
              <span className="section-label">{t('control.extruder.speed')}</span>
              <Stepper label={t('control.extruder.extrude_speed')} value={eSpeed} onChange={(v) => setPref({ eSpeed: v })} step={1} min={0.1} max={maxSpeed} digits={1} unit="mm/s" />
            </div>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2">
            <button className="btn" disabled={!ready || !valid} onClick={() => send(extrudeGcode(-eLength, eSpeed, clientMacro))}>
              <ArrowUp size={14} /> {t('control.extruder.retract')}
            </button>
            <button className="btn btn-primary" disabled={!ready || !valid} onClick={() => send(extrudeGcode(eLength, eSpeed, clientMacro))}>
              <ArrowDown size={14} /> {t('control.extruder.extrude')}
            </button>
          </div>
          {!ready && (
            <div className="mt-2">
              <Note tone="info">
                {t('control.extruder.too_cold', { min: cfg.min_extrude_temp ?? '—', temp: typeof ext.temperature === 'number' ? Math.round(ext.temperature) : '—' })}
              </Note>
            </div>
          )}
          <button type="button" className="mt-3 flex w-full items-center gap-2 rounded-lg border-0 bg-inset px-3 py-2 text-left text-[12.5px] text-ink-2" onClick={() => setDetails((d) => !d)} aria-expanded={details}>
            <span className="num min-w-0 flex-1">
              ~ {length} mm @ {flow} mm³/s, {maxPrintSpeed} mm/s
            </span>
            <ChevronDown size={14} className={details ? 'rotate-180' : ''} />
          </button>
          {details && (
            <div className="mt-2 grid gap-1.5 text-[12.5px] text-ink-2">
              <p className="m-0">{tr('control.extruder.detail_setup', { fil, noz }, { b: bold })}</p>
              <p className="m-0">{tr('control.extruder.detail_flow', { speed: eSpeed, flow }, { b: bold })}</p>
              <p className="m-0">{tr('control.extruder.detail_length', { length: eLength, factor: Math.round(factor * 100), extruded: length }, { b: bold })}</p>
              <p className="m-0">{tr('control.extruder.detail_speed', { layer, speed: maxPrintSpeed }, { b: bold })}</p>
            </div>
          )}
        </>
      )}
      {hasPa && (
        <div className={`grid gap-3 sm:grid-cols-2 ${printing ? '' : 'mt-4 border-t border-line pt-3'}`}>
          <div className="grid gap-1">
            <span className="section-label">{t('control.extruder.pressure_advance')}</span>
            <Stepper label={t('control.extruder.pressure_advance')} value={ext.pressure_advance as number} step={0.001} min={0} max={2} digits={3} onChange={(v) => send(gcode`SET_PRESSURE_ADVANCE ADVANCE=${v}`)} />
          </div>
          <div className="grid gap-1">
            <span className="section-label">{t('control.extruder.smooth_time')}</span>
            <Stepper label={t('control.extruder.smooth_time_label')} value={(ext.smooth_time as number | undefined) ?? 0} step={0.005} min={0} max={0.2} digits={3} unit="s" onChange={(v) => send(gcode`SET_PRESSURE_ADVANCE SMOOTH_TIME=${v}`)} />
          </div>
        </div>
      )}
    </Card>
  )
}

const bold = (c: ReactNode) => <b className="text-ink">{c}</b>

// ---- Z offset ----
const Z_STEPS = [0.005, 0.01, 0.025, 0.05]

export function ZOffsetCard() {
  const z = usePrinter((s) => (s.status.gcode_move?.homing_origin?.[2] as number | undefined) ?? 0)
  const zHomed = usePrinter((s) => ((s.status.toolhead?.homed_axes as string | undefined) ?? '').includes('z'))
  const pending = usePrinter((s) => s.status.configfile?.save_config_pending === true)
  const printing = usePrinting()
  const probe = useHasCommand('Z_OFFSET_APPLY_PROBE')
  const endstop = useHasCommand('Z_OFFSET_APPLY_ENDSTOP')
  const [step, setStep] = useState(Z_STEPS[1])
  const adjust = (d: number) => send(gcode`SET_GCODE_OFFSET Z_ADJUST=${d > 0 ? '+' : ''}${d} MOVE=${+zHomed}`)
  const saveDisabled = printing || z === 0
  return (
    <Card title={t('control.z_offset.title')} icon={<ArrowDown size={14} />} aside={<span className="num text-[15px] font-bold text-ink">{`${z >= 0 ? '+' : ''}${z.toFixed(3)} mm`}</span>}>
      <div className="grid gap-3">
        <div>
          <div className="section-label mb-1.5">{t('control.z_offset.step')}</div>
          <Choices value={step} options={Z_STEPS.map((v) => ({ value: v, label: `${v}` }))} onChange={setStep} />
        </div>
        <div className="grid grid-cols-1 gap-2 min-[340px]:grid-cols-2">
          <button className="btn" onClick={() => adjust(-step)} title="SET_GCODE_OFFSET Z_ADJUST" /* i18n-ignore: a command name */>
            {t('control.z_offset.closer')}
          </button>
          <button className="btn" onClick={() => adjust(step)} title="SET_GCODE_OFFSET Z_ADJUST" /* i18n-ignore: a command name */>
            {t('control.z_offset.farther')}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <button className="btn btn-sm" disabled={z === 0} onClick={() => send(`SET_GCODE_OFFSET Z=0 MOVE=${+zHomed}`)}>
            {t('control.z_offset.clear')}
          </button>
          {probe && endstop ? (
            <MenuButton
              className="btn btn-sm"
              label={t('control.z_offset.save_label')}
              button={
                <>
                  {t('common.save')} <ChevronDown size={13} />
                </>
              }
              items={['Z_OFFSET_APPLY_ENDSTOP', 'Z_OFFSET_APPLY_PROBE'].map((c) => ({ label: <span className="code">{c}</span>, onSelect: () => send(c), disabled: saveDisabled }))}
            />
          ) : probe || endstop ? (
            <button className="btn btn-sm" disabled={saveDisabled} onClick={() => send(probe ? 'Z_OFFSET_APPLY_PROBE' : 'Z_OFFSET_APPLY_ENDSTOP')} title={probe ? 'Z_OFFSET_APPLY_PROBE' : 'Z_OFFSET_APPLY_ENDSTOP'} /* i18n-ignore: command names */>
              {t('common.save')}
            </button>
          ) : null}
          {pending && (
            <button
              className="btn btn-sm btn-primary"
              disabled={printing}
              onClick={async () => {
                if (await confirm({ title: t('control.z_offset.save_config_title'), body: t('control.z_offset.save_config_body'), confirm: t('control.z_offset.save_config_confirm') })) send('SAVE_CONFIG')
              }}
            >
              {t('control.z_offset.save_config')}
            </button>
          )}
        </div>
        <Note>{probe ? t('control.z_offset.note_probe') : t('control.z_offset.note_endstop')}</Note>
      </div>
    </Card>
  )
}

// ---- speed and flow ----
export function SpeedFlowCard() {
  const speed = usePrinter((s) => Math.round(((s.status.gcode_move?.speed_factor as number | undefined) ?? 1) * 100))
  const flow = usePrinter((s) => Math.round(((s.status.gcode_move?.extrude_factor as number | undefined) ?? 1) * 100))
  return (
    <Card title={t('control.speed_flow.title')} icon={<Gauge size={14} />}>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-1">
          <span className="section-label flex justify-between">
            {t('control.speed_flow.speed')}
            {speed !== 100 && (
              <button type="button" className="border-0 bg-transparent p-0 text-[11px] font-semibold text-accent-ink normal-case" onClick={() => send('M220 S100')}>
                {t('control.speed_flow.reset')}
              </button>
            )}
          </span>
          <Stepper label={t('control.speed_flow.speed')} value={speed} step={5} min={1} max={500} unit="%" onChange={(v) => send(gcode`M220 S${Math.round(v)}`)} />
        </div>
        <div className="grid gap-1">
          <span className="section-label flex justify-between">
            {t('control.speed_flow.flow')}
            {flow !== 100 && (
              <button type="button" className="border-0 bg-transparent p-0 text-[11px] font-semibold text-accent-ink normal-case" onClick={() => send('M221 S100')}>
                {t('control.speed_flow.reset')}
              </button>
            )}
          </span>
          <Stepper label={t('control.speed_flow.flow')} value={flow} step={1} min={1} max={200} unit="%" onChange={(v) => send(gcode`M221 S${Math.round(v)}`)} />
        </div>
      </div>
    </Card>
  )
}


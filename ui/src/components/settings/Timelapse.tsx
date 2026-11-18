import { useEffect, useState, type ReactNode } from 'react'
import { Clapperboard, Film, ParkingSquare } from 'lucide-react'
import { Choices, Spinner, Stepper, Toggle } from '../ui'
import { attempt } from '../toast'
import { client, usePrinter } from '../../state/printer'
import { Row, SettingsCard } from './common'
import { useWebcams } from './Cameras'

// moonraker-timelapse's settings (machine.timelapse.*), as Fluidd's Timelapse settings and render settings show them,
// with Fluidd's labels. Settings the printer's moonraker.conf sets are locked there and shown read-only.
type TimelapseSettings = Record<string, unknown> & { blockedsettings?: string[] }

const PARK_POSITIONS = [
  { value: 'front_left', label: 'Front Left' },
  { value: 'front_right', label: 'Front Right' },
  { value: 'center', label: 'Center' },
  { value: 'back_left', label: 'Back Left' },
  { value: 'back_right', label: 'Back Right' },
  { value: 'x_only', label: 'Move X only' },
  { value: 'y_only', label: 'Move Y only' },
  { value: 'custom', label: 'Custom' },
]

export function TimelapseSettingsSection() {
  const open = usePrinter((s) => s.conn === 'open')
  const [s, setS] = useState<TimelapseSettings | null>(null)
  const [failed, setFailed] = useState(false)
  const cams = useWebcams()

  useEffect(() => {
    if (!open) return
    client
      .call<TimelapseSettings>('machine.timelapse.get_settings')
      .then((r) => (setS(r), setFailed(false)))
      .catch(() => setFailed(true))
    const off = client.on('notify_timelapse_event', (p) => {
      const ev = p[0] as { action?: string } & TimelapseSettings
      if (ev?.action === 'newsetting') setS((cur) => (cur ? { ...cur, ...ev, action: undefined } : cur))
    })
    return () => {
      off()
    }
  }, [open])

  if (failed) return <SettingsCard title="Timelapse" icon={<Film size={14} />}>{<div className="text-ink-2">The timelapse settings couldn't be read.</div>}</SettingsCard>
  if (!s)
    return (
      <SettingsCard title="Timelapse" icon={<Film size={14} />}>
        <div className="flex justify-center py-4">
          <Spinner />
        </div>
      </SettingsCard>
    )

  const blocked = (k: string) => s.blockedsettings?.includes(k) === true
  const save = (k: string, v: unknown) =>
    attempt(async () => {
      setS((cur) => cur && { ...cur, [k]: v })
      const r = await client.call<TimelapseSettings>('machine.timelapse.post_settings', { [k]: v })
      if (r && typeof r === 'object') setS(r)
    })
  const bool = (k: string, title: string, sub?: string) => (
    <Row title={title} sub={blocked(k) ? 'Managed by your Moonraker configuration' : sub}>
      <Toggle checked={s[k] === true} disabled={blocked(k)} onChange={(v) => save(k, v)} label={title} />
    </Row>
  )
  const num = (k: string, title: string, opts: { step?: number; min?: number; max?: number; digits?: number; unit?: string; sub?: ReactNode }) => (
    <Row title={title} sub={blocked(k) ? 'Managed by your Moonraker configuration' : opts.sub} wide>
      <Stepper label={title} value={Number(s[k]) || 0} onChange={(v) => save(k, v)} disabled={blocked(k)} step={opts.step ?? 1} min={opts.min ?? 0} max={opts.max} digits={opts.digits ?? 0} unit={opts.unit} />
    </Row>
  )

  return (
    <>
      <SettingsCard title="Timelapse" icon={<Film size={14} />} footer="Shared with Fluidd. Timelapses are made by moonraker-timelapse on the printer.">
        {bool('enabled', 'Enabled')}
        <Row title="Camera" sub={blocked('camera') ? 'Managed by your Moonraker configuration' : undefined} wide>
          <select className="field" value={String(s.camera ?? '')} disabled={blocked('camera')} onChange={(e) => save('camera', e.target.value)} aria-label="Camera">
            {!(cams ?? []).some((c) => c.uid === s.camera) && <option value={String(s.camera ?? '')}>{s.camera ? String(s.camera) : 'Default camera'}</option>}
            {(cams ?? []).map((c) => (
              <option key={c.uid ?? c.name} value={c.uid ?? c.name} disabled={!c.enabled}>
                {c.name}
              </option>
            ))}
          </select>
        </Row>
        <Row title="Mode" sub={blocked('mode') ? 'Managed by your Moonraker configuration' : 'Layer Macro takes a frame each layer; Hyperlapse takes one every few seconds.'}>
          <Choices
            value={String(s.mode)}
            disabled={blocked('mode')}
            onChange={(v) => save('mode', v)}
            options={[
              { value: 'layermacro', label: 'Layer Macro' },
              { value: 'hyperlapse', label: 'Hyperlapse' },
            ]}
          />
        </Row>
        {s.mode === 'hyperlapse' && num('hyperlapse_cycle', 'Hyperlapse Cycle', { min: 1, unit: 's' })}
        {num('stream_delay_compensation', 'Delay Compensation', { step: 0.01, digits: 2, unit: 's', sub: 'How long the camera stream lags behind the printer.' })}
        {bool('gcode_verbose', 'Verbose Gcode')}
      </SettingsCard>

      <SettingsCard title="Toolhead parking" icon={<ParkingSquare size={14} />}>
        {bool('parkhead', 'Park Head', 'Moves the toolhead out of the way for each frame.')}
        {s.parkhead === true && (
          <>
            <Row title="Park Position" sub={blocked('parkpos') ? 'Managed by your Moonraker configuration' : undefined}>
              <Choices value={String(s.parkpos)} disabled={blocked('parkpos')} onChange={(v) => save('parkpos', v)} options={PARK_POSITIONS} />
            </Row>
            {s.parkpos === 'custom' && (
              <>
                {num('park_custom_pos_x', 'Park Position X', { step: 5, unit: 'mm' })}
                {num('park_custom_pos_y', 'Park Position Y', { step: 5, unit: 'mm' })}
              </>
            )}
            {num('park_custom_pos_dz', 'Park Position Z-Hop', { step: 0.1, digits: 1, unit: 'mm' })}
            {num('park_travel_speed', 'Park Travel Speed', { step: 10, min: 1, unit: 'mm/s' })}
            {num('park_time', 'Park Time', { step: 0.1, digits: 1, unit: 's' })}
            {bool('fw_retract', 'Use Firmware Retraction')}
            {s.fw_retract !== true && (
              <>
                {num('park_retract_distance', 'Park Retraction Distance', { step: 0.1, digits: 1, unit: 'mm' })}
                {num('park_retract_speed', 'Park Retraction Speed', { step: 1, min: 1, unit: 'mm/s' })}
                {num('park_extrude_distance', 'Park Extrude Distance', { step: 0.1, digits: 1, unit: 'mm' })}
                {num('park_extrude_speed', 'Park Extrude Speed', { step: 1, min: 1, unit: 'mm/s' })}
              </>
            )}
          </>
        )}
      </SettingsCard>

      <SettingsCard title="Render settings" icon={<Clapperboard size={14} />}>
        {bool('autorender', 'Auto Render', 'Makes the video when the print finishes.')}
        {bool('variable_fps', 'Variable FPS', 'Picks the framerate to make the video about the target length.')}
        {s.variable_fps === true ? (
          <>
            {num('targetlength', 'Target Length', { min: 1, unit: 's' })}
            {num('variable_fps_min', 'Minimum Framerate', { min: 1, unit: 'fps' })}
            {num('variable_fps_max', 'Maximum Framerate', { min: 1, unit: 'fps' })}
          </>
        ) : (
          num('output_framerate', 'Output Framerate', { min: 1, unit: 'fps' })
        )}
        {num('constant_rate_factor', 'Constant Rate Factor', { min: 0, max: 51, sub: 'Lower is better quality and a bigger file.' })}
        {num('duplicatelastframe', 'Duplicate last frames', { min: 0 })}
        {bool('previewimage', 'Generate Thumbnail')}
        {bool('saveframes', 'Save Frames', 'Keeps the frames in a zip beside the video.')}
      </SettingsCard>
    </>
  )
}

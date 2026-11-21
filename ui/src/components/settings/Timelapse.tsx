import { useEffect, useState, type ReactNode } from 'react'
import { Clapperboard, Film, ParkingSquare } from 'lucide-react'
import { Choices, Spinner, Stepper, Toggle } from '../ui'
import { attempt } from '../toast'
import { client, usePrinter } from '../../state/printer'
import { Row, SettingsCard } from './common'
import { useWebcams } from './Cameras'
import { t, type MessageKey } from '../../i18n'

// moonraker-timelapse's settings (machine.timelapse.*), as Fluidd's Timelapse settings and render settings show them,
// with Fluidd's labels. Settings the printer's moonraker.conf sets are locked there and shown read-only.
type TimelapseSettings = Record<string, unknown> & { blockedsettings?: string[] }

const PARK_POSITIONS = ['front_left', 'front_right', 'center', 'back_left', 'back_right', 'x_only', 'y_only', 'custom']
const parkPositions = () => PARK_POSITIONS.map((value) => ({ value, label: t(`settings.timelapse.park.${value}` as MessageKey) }))

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

  if (failed) return <SettingsCard title={t('settings.timelapse.title')} icon={<Film size={14} />}>{<div className="text-ink-2">{t('settings.timelapse.read_failed')}</div>}</SettingsCard>
  if (!s)
    return (
      <SettingsCard title={t('settings.timelapse.title')} icon={<Film size={14} />}>
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
    <Row title={title} sub={blocked(k) ? t('settings.common.managed_by_config') : sub}>
      <Toggle checked={s[k] === true} disabled={blocked(k)} onChange={(v) => save(k, v)} label={title} />
    </Row>
  )
  const num = (k: string, title: string, opts: { step?: number; min?: number; max?: number; digits?: number; unit?: string; sub?: ReactNode }) => (
    <Row title={title} sub={blocked(k) ? t('settings.common.managed_by_config') : opts.sub} wide>
      <Stepper label={title} value={Number(s[k]) || 0} onChange={(v) => save(k, v)} disabled={blocked(k)} step={opts.step ?? 1} min={opts.min ?? 0} max={opts.max} digits={opts.digits ?? 0} unit={opts.unit} />
    </Row>
  )

  return (
    <>
      <SettingsCard title={t('settings.timelapse.title')} icon={<Film size={14} />} footer={t('settings.timelapse.footer')}>
        {bool('enabled', t('settings.timelapse.enabled'))}
        <Row title={t('settings.timelapse.camera')} sub={blocked('camera') ? t('settings.common.managed_by_config') : undefined} wide>
          <select className="field" value={String(s.camera ?? '')} disabled={blocked('camera')} onChange={(e) => save('camera', e.target.value)} aria-label={t('settings.timelapse.camera')}>
            {!(cams ?? []).some((c) => c.uid === s.camera) && <option value={String(s.camera ?? '')}>{s.camera ? String(s.camera) : t('settings.timelapse.default_camera')}</option>}
            {(cams ?? []).map((c) => (
              <option key={c.uid ?? c.name} value={c.uid ?? c.name} disabled={!c.enabled}>
                {c.name}
              </option>
            ))}
          </select>
        </Row>
        <Row title={t('settings.timelapse.mode')} sub={blocked('mode') ? t('settings.common.managed_by_config') : t('settings.timelapse.mode_sub')}>
          <Choices
            value={String(s.mode)}
            disabled={blocked('mode')}
            onChange={(v) => save('mode', v)}
            options={[
              { value: 'layermacro', label: t('settings.timelapse.mode_layermacro') },
              { value: 'hyperlapse', label: t('settings.timelapse.mode_hyperlapse') },
            ]}
          />
        </Row>
        {s.mode === 'hyperlapse' && num('hyperlapse_cycle', t('settings.timelapse.hyperlapse_cycle'), { min: 1, unit: 's' })}
        {num('stream_delay_compensation', t('settings.timelapse.delay_compensation'), { step: 0.01, digits: 2, unit: 's', sub: t('settings.timelapse.delay_compensation_sub') })}
        {bool('gcode_verbose', t('settings.timelapse.verbose_gcode'))}
      </SettingsCard>

      <SettingsCard title={t('settings.timelapse.parking')} icon={<ParkingSquare size={14} />}>
        {bool('parkhead', t('settings.timelapse.park_head'), t('settings.timelapse.park_head_sub'))}
        {s.parkhead === true && (
          <>
            <Row title={t('settings.timelapse.park_position')} sub={blocked('parkpos') ? t('settings.common.managed_by_config') : undefined}>
              <Choices value={String(s.parkpos)} disabled={blocked('parkpos')} onChange={(v) => save('parkpos', v)} options={parkPositions()} />
            </Row>
            {s.parkpos === 'custom' && (
              <>
                {num('park_custom_pos_x', t('settings.timelapse.park_x'), { step: 5, unit: 'mm' })}
                {num('park_custom_pos_y', t('settings.timelapse.park_y'), { step: 5, unit: 'mm' })}
              </>
            )}
            {num('park_custom_pos_dz', t('settings.timelapse.park_z_hop'), { step: 0.1, digits: 1, unit: 'mm' })}
            {num('park_travel_speed', t('settings.timelapse.park_speed'), { step: 10, min: 1, unit: 'mm/s' })}
            {num('park_time', t('settings.timelapse.park_time'), { step: 0.1, digits: 1, unit: 's' })}
            {bool('fw_retract', t('settings.timelapse.fw_retract'))}
            {s.fw_retract !== true && (
              <>
                {num('park_retract_distance', t('settings.timelapse.retract_distance'), { step: 0.1, digits: 1, unit: 'mm' })}
                {num('park_retract_speed', t('settings.timelapse.retract_speed'), { step: 1, min: 1, unit: 'mm/s' })}
                {num('park_extrude_distance', t('settings.timelapse.extrude_distance'), { step: 0.1, digits: 1, unit: 'mm' })}
                {num('park_extrude_speed', t('settings.timelapse.extrude_speed'), { step: 1, min: 1, unit: 'mm/s' })}
              </>
            )}
          </>
        )}
      </SettingsCard>

      <SettingsCard title={t('settings.timelapse.render')} icon={<Clapperboard size={14} />}>
        {bool('autorender', t('settings.timelapse.autorender'), t('settings.timelapse.autorender_sub'))}
        {bool('variable_fps', t('settings.timelapse.variable_fps'), t('settings.timelapse.variable_fps_sub'))}
        {s.variable_fps === true ? (
          <>
            {num('targetlength', t('settings.timelapse.target_length'), { min: 1, unit: 's' })}
            {num('variable_fps_min', t('settings.timelapse.min_fps'), { min: 1, unit: 'fps' })}
            {num('variable_fps_max', t('settings.timelapse.max_fps'), { min: 1, unit: 'fps' })}
          </>
        ) : (
          num('output_framerate', t('settings.timelapse.output_fps'), { min: 1, unit: 'fps' })
        )}
        {num('constant_rate_factor', t('settings.timelapse.crf'), { min: 0, max: 51, sub: t('settings.timelapse.crf_sub') })}
        {num('duplicatelastframe', t('settings.timelapse.duplicate_frames'), { min: 0 })}
        {bool('previewimage', t('settings.timelapse.thumbnail'))}
        {bool('saveframes', t('settings.timelapse.save_frames'), t('settings.timelapse.save_frames_sub'))}
      </SettingsCard>
    </>
  )
}

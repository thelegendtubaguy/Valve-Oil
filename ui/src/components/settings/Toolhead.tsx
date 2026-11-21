import { CircleCheck, Move, Pipette, ArrowUpDown } from 'lucide-react'
import { Choices, Stepper } from '../ui'
import { usePrinter } from '../../state/printer'
import { NumberList, Row, SettingsCard, ToggleRow } from './common'
import { useUiSetting } from './fluidd'
import { t } from '../../i18n'

export function ToolheadSettings() {
  const [distances, setDistances] = useUiSetting('general.toolheadMoveDistances')
  const [moveLength, setMoveLength] = useUiSetting('general.defaultToolheadMoveLength')
  const [xySpeed, setXySpeed] = useUiSetting('general.defaultToolheadXYSpeed')
  const [zSpeed, setZSpeed] = useUiSetting('general.defaultToolheadZSpeed')
  const [gcodeCoords, setGcodeCoords] = useUiSetting('general.useGcodeCoords')
  const [invX, setInvX] = useUiSetting('general.axis.x.inverted')
  const [invY, setInvY] = useUiSetting('general.axis.y.inverted')
  const [invZ, setInvZ] = useUiSetting('general.axis.z.inverted')
  const [extrudeLength, setExtrudeLength] = useUiSetting('general.defaultExtrudeLength')
  const [extrudeSpeed, setExtrudeSpeed] = useUiSetting('general.defaultExtrudeSpeed')
  const [zAdjust, setZAdjust] = useUiSetting('general.zAdjustDistances')
  const [forceMove, setForceMove] = useUiSetting('general.forceMoveToggleWarning')
  const [manualProbe, setManualProbe] = useUiSetting('general.showManualProbeDialogAutomatically')
  const [bedScrews, setBedScrews] = useUiSetting('general.showBedScrewsAdjustDialogAutomatically')
  const [screwsTilt, setScrewsTilt] = useUiSetting('general.showScrewsTiltAdjustDialogAutomatically')
  const has = usePrinter((s) => ({ bedScrews: !!s.settings.bed_screws, screwsTilt: !!s.settings.screws_tilt_adjust, forceMove: !!s.settings.force_move?.enable_force_move }))
  const maxVelocity = usePrinter((s) => Number(s.settings.printer?.max_velocity) || 1000)
  const maxZVelocity = usePrinter((s) => Number(s.settings.printer?.max_z_velocity) || maxVelocity)

  return (
    <>
      <p className="m-0 text-[12.5px] text-ink-3">{t('settings.common.shared_with_fluidd')}</p>
      <SettingsCard title={t('settings.toolhead.moves')} icon={<Move size={14} />}>
        <Row title={t('settings.toolhead.distances')} sub={t('settings.toolhead.distances_sub')} wide>
          <NumberList label={t('settings.toolhead.distances_label')} value={distances} onChange={setDistances} min={1} max={6} />
        </Row>
        <Row title={t('settings.toolhead.move_length')}>
          <Choices value={moveLength} options={distances.map((d) => ({ value: d, label: `${d}` }))} onChange={setMoveLength} />
        </Row>
        <Row title={t('settings.toolhead.xy_speed')} wide>
          <Stepper label={t('settings.toolhead.xy_speed')} value={xySpeed} onChange={setXySpeed} step={10} min={1} max={maxVelocity} unit="mm/s" />
        </Row>
        <Row title={t('settings.toolhead.z_speed')} wide>
          <Stepper label={t('settings.toolhead.z_speed')} value={zSpeed} onChange={setZSpeed} step={1} min={1} max={maxZVelocity} unit="mm/s" />
        </Row>
        <ToggleRow title={t('settings.toolhead.gcode_coords')} sub={t('settings.toolhead.gcode_coords_sub')} checked={gcodeCoords} onChange={setGcodeCoords} />
        <ToggleRow title={t('settings.toolhead.invert_x')} checked={invX} onChange={setInvX} />
        <ToggleRow title={t('settings.toolhead.invert_y')} checked={invY} onChange={setInvY} />
        <ToggleRow title={t('settings.toolhead.invert_z')} checked={invZ} onChange={setInvZ} />
      </SettingsCard>

      <SettingsCard title={t('settings.toolhead.extruder')} icon={<Pipette size={14} />}>
        <Row title={t('settings.toolhead.extrude_length')} wide>
          <Stepper label={t('settings.toolhead.extrude_length')} value={extrudeLength} onChange={setExtrudeLength} step={5} min={0.1} max={1000} digits={1} unit="mm" />
        </Row>
        <Row title={t('settings.toolhead.extrude_speed')} wide>
          <Stepper label={t('settings.toolhead.extrude_speed')} value={extrudeSpeed} onChange={setExtrudeSpeed} step={1} min={0.1} max={100} digits={1} unit="mm/s" />
        </Row>
      </SettingsCard>

      <SettingsCard title={t('settings.toolhead.z_offset')} icon={<ArrowUpDown size={14} />}>
        <Row title={t('settings.toolhead.z_adjust')} sub={t('settings.toolhead.z_adjust_sub')} wide>
          <NumberList label={t('settings.toolhead.z_adjust')} value={zAdjust} onChange={setZAdjust} min={1} max={6} />
        </Row>
      </SettingsCard>

      <SettingsCard title={t('settings.toolhead.confirmations')} icon={<CircleCheck size={14} />}>
        {has.forceMove && <ToggleRow title={t('settings.toolhead.force_move')} checked={forceMove} onChange={setForceMove} />}
        <ToggleRow title={t('settings.toolhead.manual_probe')} sub={t('settings.toolhead.manual_probe_sub')} checked={manualProbe} onChange={setManualProbe} />
        {has.bedScrews && <ToggleRow title={t('settings.toolhead.bed_screws')} sub={t('settings.toolhead.bed_screws_sub')} checked={bedScrews} onChange={setBedScrews} />}
        {has.screwsTilt && <ToggleRow title={t('settings.toolhead.screws_tilt')} sub={t('settings.toolhead.screws_tilt_sub')} checked={screwsTilt} onChange={setScrewsTilt} />}
      </SettingsCard>
    </>
  )
}

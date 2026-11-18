import { CircleCheck, Move, Pipette, ArrowUpDown } from 'lucide-react'
import { Choices, Stepper } from '../ui'
import { usePrinter } from '../../state/printer'
import { NumberList, Row, SettingsCard, ToggleRow } from './common'
import { useUiSetting } from './fluidd'

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
      <p className="m-0 text-[12.5px] text-ink-3">Shared with Fluidd: a change here shows there too.</p>
      <SettingsCard title="Moves" icon={<Move size={14} />}>
        <Row title="Toolhead distance values" sub="The jog steps to pick from, in mm. Up to six." wide>
          <NumberList label="toolhead distance values" value={distances} onChange={setDistances} min={1} max={6} />
        </Row>
        <Row title="Default toolhead move length">
          <Choices value={moveLength} options={distances.map((d) => ({ value: d, label: `${d}` }))} onChange={setMoveLength} />
        </Row>
        <Row title="Default toolhead XY speed" wide>
          <Stepper label="Default toolhead XY speed" value={xySpeed} onChange={setXySpeed} step={10} min={1} max={maxVelocity} unit="mm/s" />
        </Row>
        <Row title="Default toolhead Z speed" wide>
          <Stepper label="Default toolhead Z speed" value={zSpeed} onChange={setZSpeed} step={1} min={1} max={maxZVelocity} unit="mm/s" />
        </Row>
        <ToggleRow title="Use G-code coordinates" sub="Show the G-code position instead of the toolhead's position." checked={gcodeCoords} onChange={setGcodeCoords} />
        <ToggleRow title="Invert X control" checked={invX} onChange={setInvX} />
        <ToggleRow title="Invert Y control" checked={invY} onChange={setInvY} />
        <ToggleRow title="Invert Z control" checked={invZ} onChange={setInvZ} />
      </SettingsCard>

      <SettingsCard title="Extruder" icon={<Pipette size={14} />}>
        <Row title="Default extrude length" wide>
          <Stepper label="Default extrude length" value={extrudeLength} onChange={setExtrudeLength} step={5} min={0.1} max={1000} digits={1} unit="mm" />
        </Row>
        <Row title="Default extrude speed" wide>
          <Stepper label="Default extrude speed" value={extrudeSpeed} onChange={setExtrudeSpeed} step={1} min={0.1} max={100} digits={1} unit="mm/s" />
        </Row>
      </SettingsCard>

      <SettingsCard title="Z offset" icon={<ArrowUpDown size={14} />}>
        <Row title="Z adjust values" sub="The steps for adjusting the Z offset, in mm. Up to six." wide>
          <NumberList label="Z adjust values" value={zAdjust} onChange={setZAdjust} min={1} max={6} />
        </Row>
      </SettingsCard>

      <SettingsCard title="Confirmations and helpers" icon={<CircleCheck size={14} />}>
        {has.forceMove && <ToggleRow title="Require confirm when activating FORCE_MOVE" checked={forceMove} onChange={setForceMove} />}
        <ToggleRow title="Show Manual Probe dialog automatically" sub="Shows the helper while a manual probe runs." checked={manualProbe} onChange={setManualProbe} />
        {has.bedScrews && <ToggleRow title="Show Bed Screws Adjust dialog automatically" sub="Shows the helper while BED_SCREWS_ADJUST runs." checked={bedScrews} onChange={setBedScrews} />}
        {has.screwsTilt && <ToggleRow title="Show Screws Tilt Adjust dialog automatically" sub="Shows the helper after SCREWS_TILT_CALCULATE." checked={screwsTilt} onChange={setScrewsTilt} />}
      </SettingsCard>
    </>
  )
}

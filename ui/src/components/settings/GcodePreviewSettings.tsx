import { Layers } from 'lucide-react'
import { Stepper } from '../ui'
import { usePreviewSettings } from '../gcode-preview/settings'
import { Row, SettingsCard, ToggleRow } from './common'

// Fluidd's G-code preview options. The preview keeps them as one object in Fluidd's database (gcode-preview/settings.ts),
// so these read and write it the same way.
export function GcodePreviewSettings() {
  const [s, set] = usePreviewSettings()
  return (
    <SettingsCard title="G-code preview" icon={<Layers size={14} />} footer="Shared with Fluidd: a change here shows there too.">
      <Row title="Extrusion line width" wide>
        <Stepper label="Extrusion line width" value={s.extrusionLineWidth} onChange={(v) => set('extrusionLineWidth', v)} step={0.05} min={0.05} max={5} digits={2} unit="mm" />
      </Row>
      <Row title="Move line width" wide>
        <Stepper label="Move line width" value={s.moveLineWidth} onChange={(v) => set('moveLineWidth', v)} step={0.05} min={0.05} max={5} digits={2} unit="mm" />
      </Row>
      <Row title="Retraction icon size" wide>
        <Stepper label="Retraction icon size" value={s.retractionIconSize} onChange={(v) => set('retractionIconSize', v)} step={0.1} min={0.1} max={5} digits={1} unit="mm" />
      </Row>
      <Row title="Default minimum layer height" wide>
        <Stepper label="Default minimum layer height" value={s.minLayerHeight} onChange={(v) => set('minLayerHeight', v)} step={0.05} min={0.01} max={5} digits={2} unit="mm" />
      </Row>
      <ToggleRow title="Horizontal flip" checked={s.flip.horizontal} onChange={(v) => set('flip', { ...s.flip, horizontal: v })} />
      <ToggleRow title="Vertical flip" checked={s.flip.vertical} onChange={(v) => set('flip', { ...s.flip, vertical: v })} />
      <ToggleRow title="Draw origin" checked={s.drawOrigin} onChange={(v) => set('drawOrigin', v)} />
      <ToggleRow title="Draw background" checked={s.drawBackground} onChange={(v) => set('drawBackground', v)} />
      <ToggleRow title="Show animations" checked={s.showAnimations} onChange={(v) => set('showAnimations', v)} />
      <ToggleRow title="Automatically load file on print start" checked={s.autoLoadOnPrintStart} onChange={(v) => set('autoLoadOnPrintStart', v)} />
      <ToggleRow title="Automatically load file on mobile devices" checked={s.autoLoadMobileOnPrintStart} onChange={(v) => set('autoLoadMobileOnPrintStart', v)} />
      <ToggleRow title="Automatically follow progress on file load" checked={s.autoFollowOnFileLoad} onChange={(v) => set('autoFollowOnFileLoad', v)} />
      <ToggleRow title="Hide part bounding box when printing a single part" checked={s.hideSinglePartBoundingBox} onChange={(v) => set('hideSinglePartBoundingBox', v)} />
    </SettingsCard>
  )
}

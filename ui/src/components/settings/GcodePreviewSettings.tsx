import { Layers } from 'lucide-react'
import { Stepper } from '../ui'
import { usePreviewSettings } from '../gcode-preview/settings'
import { Row, SettingsCard, ToggleRow } from './common'
import { t } from '../../i18n'

// Fluidd's G-code preview options. The preview keeps them as one object in Fluidd's database (gcode-preview/settings.ts),
// so these read and write it the same way.
export function GcodePreviewSettings() {
  const [s, set] = usePreviewSettings()
  return (
    <SettingsCard title={t('settings.sections.gcode_preview.label')} icon={<Layers size={14} />} footer={t('settings.common.shared_with_fluidd')}>
      <Row title={t('settings.gcode_preview.extrusion_width')} wide>
        <Stepper label={t('settings.gcode_preview.extrusion_width')} value={s.extrusionLineWidth} onChange={(v) => set('extrusionLineWidth', v)} step={0.05} min={0.05} max={5} digits={2} unit="mm" />
      </Row>
      <Row title={t('settings.gcode_preview.move_width')} wide>
        <Stepper label={t('settings.gcode_preview.move_width')} value={s.moveLineWidth} onChange={(v) => set('moveLineWidth', v)} step={0.05} min={0.05} max={5} digits={2} unit="mm" />
      </Row>
      <Row title={t('settings.gcode_preview.retraction_icon')} wide>
        <Stepper label={t('settings.gcode_preview.retraction_icon')} value={s.retractionIconSize} onChange={(v) => set('retractionIconSize', v)} step={0.1} min={0.1} max={5} digits={1} unit="mm" />
      </Row>
      <Row title={t('settings.gcode_preview.min_layer_height')} wide>
        <Stepper label={t('settings.gcode_preview.min_layer_height')} value={s.minLayerHeight} onChange={(v) => set('minLayerHeight', v)} step={0.05} min={0.01} max={5} digits={2} unit="mm" />
      </Row>
      <ToggleRow title={t('settings.gcode_preview.flip_h')} checked={s.flip.horizontal} onChange={(v) => set('flip', { ...s.flip, horizontal: v })} />
      <ToggleRow title={t('settings.gcode_preview.flip_v')} checked={s.flip.vertical} onChange={(v) => set('flip', { ...s.flip, vertical: v })} />
      <ToggleRow title={t('settings.gcode_preview.draw_origin')} checked={s.drawOrigin} onChange={(v) => set('drawOrigin', v)} />
      <ToggleRow title={t('settings.gcode_preview.draw_background')} checked={s.drawBackground} onChange={(v) => set('drawBackground', v)} />
      <ToggleRow title={t('settings.gcode_preview.show_animations')} checked={s.showAnimations} onChange={(v) => set('showAnimations', v)} />
      <ToggleRow title={t('settings.gcode_preview.auto_load')} checked={s.autoLoadOnPrintStart} onChange={(v) => set('autoLoadOnPrintStart', v)} />
      <ToggleRow title={t('settings.gcode_preview.auto_load_mobile')} checked={s.autoLoadMobileOnPrintStart} onChange={(v) => set('autoLoadMobileOnPrintStart', v)} />
      <ToggleRow title={t('settings.gcode_preview.auto_follow')} checked={s.autoFollowOnFileLoad} onChange={(v) => set('autoFollowOnFileLoad', v)} />
      <ToggleRow title={t('settings.gcode_preview.hide_bounding_box')} checked={s.hideSinglePartBoundingBox} onChange={(v) => set('hideSinglePartBoundingBox', v)} />
    </SettingsCard>
  )
}

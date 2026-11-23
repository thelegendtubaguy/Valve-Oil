import { Save } from 'lucide-react'
import { confirm } from '../ui'
import { useObject } from '../../state/printer'
import { useJobActive } from '../../state/job'
import { send } from '../control/common'
import { prettyName } from '../../lib/format'
import { t } from '../../i18n'

/** What a pending SAVE_CONFIG section holds, in plain words. */
export function pendingLabel(section: string) {
  if (section.startsWith('bed_mesh ')) return t('tune.save_config.pending_mesh', { name: section.slice(9) })
  if (section === 'input_shaper') return t('tune.save_config.pending_shaper')
  if (section === 'probe' || section === 'probe_air' || section === 'bltouch') return t('tune.save_config.pending_probe')
  if (section === 'stepper_z') return t('tune.save_config.pending_endstop')
  if (section.startsWith('heater_') || section === 'extruder') return t('tune.save_config.pending_pid', { name: prettyName(section) })
  return prettyName(section)
}

/**
 * Calibration Klipper holds until SAVE_CONFIG writes it to printer.cfg, which restarts Klipper. `only` keeps the
 * notice to the sections a tab is about, but a removed profile has no section, so pending without any is shown too.
 */
export function SaveConfigNotice({ only }: { only?: (section: string) => boolean }) {
  const cf = useObject('configfile')
  const printing = useJobActive()
  const items = Object.keys((cf.save_config_pending_items as Record<string, unknown> | undefined) ?? {})
  const shown = only ? items.filter(only) : items
  if (!cf.save_config_pending || (only && items.length > 0 && shown.length === 0)) return null
  const save = async () => {
    if (
      await confirm({
        title: t('tune.save_config.button'),
        body: t('tune.save_config.confirm_body'),
        confirm: t('tune.save_config.confirm_button'),
      })
    )
      send('SAVE_CONFIG')
  }
  return (
    <div className="tone-warn flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl px-3.5 py-2.5">
      <Save size={16} className="flex-none" />
      <div className="min-w-0 flex-1 text-ink">
        <div className="font-semibold">{t('tune.save_config.waiting')}</div>
        <div className="text-[12.5px] text-ink-2">
          {shown.length
            ? only && items.length > shown.length
              ? t('tune.save_config.changed_more', { list: shown.map(pendingLabel).join(', '), more: items.length - shown.length })
              : t('tune.save_config.changed', { list: shown.map(pendingLabel).join(', ') })
            : t('tune.save_config.other_change')}
        </div>
      </div>
      <button className="btn btn-sm btn-primary" disabled={printing} onClick={save} title={printing ? t('tune.save_config.not_printing') : t('tune.save_config.runs_save_config')}>
        {t('tune.save_config.button')}
      </button>
    </div>
  )
}

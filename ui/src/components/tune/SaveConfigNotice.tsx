import { Save } from 'lucide-react'
import { confirm } from '../ui'
import { attempt } from '../toast'
import { runGcode, useObject } from '../../state/printer'
import { prettyName } from '../../lib/format'

/** What a pending SAVE_CONFIG section holds, in plain words. */
export function pendingLabel(section: string) {
  if (section.startsWith('bed_mesh ')) return `Bed mesh profile ${section.slice(9)}`
  if (section === 'input_shaper') return 'Input shaper'
  if (section === 'probe' || section === 'probe_air' || section === 'bltouch') return 'Probe Z offset'
  if (section === 'stepper_z') return 'Z endstop position'
  if (section.startsWith('heater_') || section === 'extruder') return `${prettyName(section)} PID`
  return prettyName(section)
}

/**
 * Calibration Klipper holds until SAVE_CONFIG writes it to printer.cfg, which restarts Klipper. `only` keeps the
 * notice to the sections a tab is about, but a removed profile has no section, so pending without any is shown too.
 */
export function SaveConfigNotice({ only }: { only?: (section: string) => boolean }) {
  const cf = useObject('configfile')
  const ps = useObject('print_stats')
  const items = Object.keys((cf.save_config_pending_items as Record<string, unknown> | undefined) ?? {})
  const shown = only ? items.filter(only) : items
  if (!cf.save_config_pending || (only && items.length > 0 && shown.length === 0)) return null
  const printing = ps.state === 'printing' || ps.state === 'paused'
  const save = async () => {
    if (
      await confirm({
        title: 'Save config',
        body: 'Klipper writes the pending changes to printer.cfg and restarts. Heaters turn off and the printer has to home again.',
        confirm: 'Save and restart',
      })
    )
      attempt(() => runGcode('SAVE_CONFIG'))
  }
  return (
    <div className="tone-warn flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl px-3.5 py-2.5">
      <Save size={16} className="flex-none" />
      <div className="min-w-0 flex-1 text-ink">
        <div className="font-semibold">Waiting for Save config</div>
        <div className="text-[12.5px] text-ink-2">
          {shown.length
            ? `${shown.map(pendingLabel).join(', ')}${only && items.length > shown.length ? `, and ${items.length - shown.length} more,` : ''} changed since Klipper started.`
            : 'A removed profile or another change is waiting to be written to printer.cfg.'}
        </div>
      </div>
      <button className="btn btn-sm btn-primary" disabled={printing} onClick={save} title={printing ? 'Not during a print' : 'Runs SAVE_CONFIG, which restarts Klipper'}>
        Save config
      </button>
    </div>
  )
}

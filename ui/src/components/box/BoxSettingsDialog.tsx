// Whether prints use the Box, and QIDI's Box settings, saved variables its Box modules read, set to 1 or 0 as QIDI's Fluidd did; Valve Oil's turn
// spools while heating when its module is installed; and the printer's own load and unload motion. Detection on insertion
// goes through QIDI's own command where the printer has it, so RS-485 Boxes get it too (boxCommands.setting).
import { Dialog, Toggle } from '../ui'
import { attempt } from '../toast'
import { boxCommands, type BoxSetting, type BoxState } from '../../state/box'
import { runGcode, usePrinter } from '../../state/printer'
import { BoxMotion } from './BoxMotion'
import { t, type MessageKey } from '../../i18n'

// Each setting's title and help are box.settings.<key> and box.settings.<key>_help.
const SETTINGS: BoxSetting[] = [
  // The printer-wide choice the print dialog's switch also sets. A new install turns it on when it finds a Box; later
  // installs and updates leave it as set here.
  'enable_box',
  'auto_read_rfid',
  'auto_init_detect',
  'auto_reload_detect',
]
const SPOOL_ROLL: BoxSetting = 'vo_box_spool_roll'
const row = (key: BoxSetting, value: boolean) => ({ key, title: t(`box.settings.${key}` as MessageKey), help: t(`box.settings.${key}_help` as MessageKey), value })

export function BoxSettingsDialog({ box, open, onClose }: { box: BoxState; open: boolean; onClose: () => void }) {
  const variables = usePrinter((s) => s.status.save_variables?.variables ?? {})
  const ready = usePrinter((s) => s.klippy === 'ready')
  const hasMotion = usePrinter((s) => s.status.vo_box_feed_motion != null)
  const rows = [
    ...SETTINGS.map((key) => row(key, Number(variables[key] ?? 0) === 1)),
    // Valve Oil's module reports whether it's on.
    ...(box.spoolRoll ? [row(SPOOL_ROLL, box.spoolRoll.enabled)] : []),
  ]
  return (
    <Dialog open={open} onClose={onClose} title={t('box.screen.settings')} size="wide">
      <div className="grid">
        {rows.map((r, i) => (
          <label key={r.key} className={`flex items-center gap-4 py-3 ${i > 0 ? 'border-t border-line' : 'pt-0'}`}>
            <div className="min-w-0 flex-1">
              <div className="font-semibold">{r.title}</div>
              <div className="text-[12.5px] text-ink-3">{r.help}</div>
            </div>
            <Toggle checked={r.value} label={r.title} disabled={!ready} onChange={(v) => attempt(() => runGcode(boxCommands.setting(r.key, v, box)))} />
          </label>
        ))}
        {box.autoReload && Object.keys(box.reloadGroups).length === 0 && <div className="-mt-1 pb-3 text-[12.5px] text-ink-3">{t('box.settings.no_reload_groups')}</div>}
      </div>
      {hasMotion && (
        <div className="mt-1 border-t border-line pt-4">
          <BoxMotion />
        </div>
      )}
    </Dialog>
  )
}

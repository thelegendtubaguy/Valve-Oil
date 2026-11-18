// QIDI's Box settings, saved variables its Box modules read, set to 1 or 0 as QIDI's Fluidd did; Valve Oil's turn
// spools while heating when its module is installed; and the printer's own load and unload motion.
import { Dialog, Toggle } from '../ui'
import { attempt } from '../toast'
import { boxCommands, type BoxSetting, type BoxState } from '../../state/box'
import { runGcode, usePrinter } from '../../state/printer'
import { BoxMotion } from './BoxMotion'

const SETTINGS: { key: BoxSetting; title: string; help: string }[] = [
  { key: 'auto_read_rfid', title: 'Detection on insertion', help: 'When filament is inserted, the Box reads its filament information, which takes about 20 seconds.' },
  { key: 'auto_init_detect', title: 'Detection on startup', help: 'On every startup, the Box reads the filament information of every slot, which takes about 1 minute.' },
  { key: 'auto_reload_detect', title: 'Automatic reload', help: 'When filament runs out, the Box switches to a slot with the same filament, vendor, and color.' },
]
const SPOOL_ROLL = {
  key: 'vo_box_spool_roll' as const,
  title: 'Turn spools while heating',
  help: 'While the Box heater is on, including during a print, each empty slot turns its spool every 5 minutes. Slots with filament fed never turn.',
}

export function BoxSettingsDialog({ box, open, onClose }: { box: BoxState; open: boolean; onClose: () => void }) {
  const variables = usePrinter((s) => s.status.save_variables?.variables ?? {})
  const ready = usePrinter((s) => s.klippy === 'ready')
  const hasMotion = usePrinter((s) => s.status.tltg_box_feed_motion != null)
  const rows = [
    ...SETTINGS.map((r) => ({ ...r, value: Number(variables[r.key] ?? 0) === 1 })),
    // Valve Oil's module reports whether it's on.
    ...(box.spoolRoll ? [{ ...SPOOL_ROLL, value: box.spoolRoll.enabled }] : []),
  ]
  return (
    <Dialog open={open} onClose={onClose} title="Box settings" size="wide">
      <div className="grid">
        {rows.map((r, i) => (
          <label key={r.key} className={`flex items-center gap-4 py-3 ${i > 0 ? 'border-t border-line' : 'pt-0'}`}>
            <div className="min-w-0 flex-1">
              <div className="font-semibold">{r.title}</div>
              <div className="text-[12.5px] text-ink-3">{r.help}</div>
            </div>
            <Toggle checked={r.value} label={r.title} disabled={!ready} onChange={(v) => attempt(() => runGcode(boxCommands.setting(r.key, v)))} />
          </label>
        ))}
        {box.autoReload && Object.keys(box.reloadGroups).length === 0 && <div className="-mt-1 pb-3 text-[12.5px] text-ink-3">No loaded slots share the same filament, vendor, and color, so none can stand in for another.</div>}
      </div>
      {hasMotion && (
        <div className="mt-1 border-t border-line pt-4">
          <BoxMotion />
        </div>
      )}
    </Dialog>
  )
}

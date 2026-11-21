import { useEffect } from 'react'
import { Minus, Plus } from 'lucide-react'
import { Dialog } from '../ui'
import { attempt } from '../toast'
import { runGcode, useObject, usePrinter } from '../../state/printer'
import { FLUIDD, useDbItem } from '../../state/database'
import { prettyName } from '../../lib/format'
import { createStore, useStore } from '../../lib/store'
import { usePrinterFlags } from './hooks'
import { useUiSetting } from '../settings/fluidd'
import { gcode } from '../control/gcode'
import { t } from '../../i18n'
import { tr } from '../../i18n/rich'

type Helper = 'manual' | 'screws'
const helperOpen = createStore<Record<Helper, boolean>>({ manual: false, screws: false })

/** Opens a helper's dialog again after it was closed while the helper still runs. */
export const reopenHelper = (h: Helper) => helperOpen.set((s) => ({ ...s, [h]: true }))

/** Opens itself while a helper is active, as Fluidd's dialogs do, and closes when it ends. */
function useAutoOpen(h: Helper, active: boolean) {
  const { printing } = usePrinterFlags()
  // Fluidd's "show automatically" settings, shared with it. Off, the dialog opens only from reopenHelper().
  const [autoManual] = useUiSetting('general.showManualProbeDialogAutomatically')
  const [autoScrews] = useUiSetting('general.showBedScrewsAdjustDialogAutomatically')
  const auto = h === 'manual' ? autoManual : autoScrews
  const open = useStore(helperOpen, (s) => s[h])
  useEffect(() => helperOpen.set((s) => ({ ...s, [h]: active && !printing && auto })), [h, active, printing, auto])
  return [open && active, (v: boolean) => helperOpen.set((s) => ({ ...s, [h]: v }))] as const
}

/** Klipper's manual probe helper (PROBE_CALIBRATE, Z_ENDSTOP_CALIBRATE, MANUAL_PROBE): TESTZ steps, then ACCEPT. */
export function ManualProbeDialog() {
  const mp = useObject('manual_probe')
  const active = !!mp.is_active
  const [open, setOpen] = useAutoOpen('manual', active)
  const { printing } = usePrinterFlags()
  const ready = usePrinter((s) => s.klippy === 'ready')
  const [distances] = useDbItem<number[]>(FLUIDD, 'uiSettings.general.zAdjustDistances', [0.005, 0.01, 0.025, 0.05])
  const offsets = [...new Set([1, 0.1, ...distances])].sort((a, b) => b - a)
  const run = (g: string) => attempt(() => runGcode(g))
  const fmt = (v: unknown) => (typeof v === 'number' ? v.toFixed(3) : '—')
  const disabled = !ready || printing
  return (
    <Dialog
      open={open}
      onClose={() => setOpen(false)}
      title={t('tune.shared.manual_probe')}
      footer={
        <>
          <button className="btn btn-danger" onClick={() => (run('ABORT'), setOpen(false))}>
            {t('tune.shared.abort')}
          </button>
          <button className="btn btn-primary" onClick={() => run('ACCEPT')}>
            {t('tune.shared.accept')}
          </button>
        </>
      }
    >
      <p className="mt-0 text-ink-2">{t('tune.helpers.manual_hint')}</p>
      <div className="grid grid-cols-3 gap-2">
        {[
          { k: 'min', label: t('tune.helpers.z_min'), v: mp.z_position_lower },
          { k: 'z', label: 'Z', v: mp.z_position },
          { k: 'max', label: t('tune.helpers.z_max'), v: mp.z_position_upper },
        ].map(({ k, label, v }) => (
          <div key={k} className={`tile text-center ${k === 'z' ? 'hot' : ''}`}>
            <span className="section-label">{label}</span>
            <span className="v num">{fmt(v)}</span>
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-center justify-between gap-2">
        <div className="seg">
          <button disabled={disabled} onClick={() => run('TESTZ Z=--')} title={t('tune.helpers.halfway_min')}>
            − −
          </button>
          <button disabled={disabled} onClick={() => run('TESTZ Z=-')} title={t('tune.helpers.partway_min')}>
            −
          </button>
        </div>
        <span className="text-[12px] text-ink-3">{t('tune.helpers.bisect')}</span>
        <div className="seg">
          <button disabled={disabled} onClick={() => run('TESTZ Z=+')} title={t('tune.helpers.partway_max')}>
            +
          </button>
          <button disabled={disabled} onClick={() => run('TESTZ Z=++')} title={t('tune.helpers.halfway_max')}>
            + +
          </button>
        </div>
      </div>
      <div className="mt-3 grid gap-1.5">
        {offsets.map((o) => (
          <div key={o} className="flex items-center gap-2">
            <button className="pm" disabled={disabled} onClick={() => run(gcode`TESTZ Z=-${o}`)} aria-label={t('common.lower_named', { name: `${o} mm` })}>
              <Minus size={15} />
            </button>
            <div className="num flex-1 text-center font-semibold">{o} mm</div>
            <button className="pm" disabled={disabled} onClick={() => run(gcode`TESTZ Z=+${o}`)} aria-label={t('common.raise_named', { name: `${o} mm` })}>
              <Plus size={15} />
            </button>
          </div>
        ))}
      </div>
    </Dialog>
  )
}

/** Klipper's BED_SCREWS_ADJUST helper: the paper test at each screw. */
export function BedScrewsDialog() {
  const bs = useObject('bed_screws')
  const cfg = usePrinter((s) => s.settings.bed_screws) as Record<string, unknown> | undefined
  const active = !!bs.is_active
  const [open, setOpen] = useAutoOpen('screws', active)
  const run = (g: string) => attempt(() => runGcode(g))
  const screws: string[] = []
  for (let i = 1; cfg?.[`screw${i}`]; i++) screws.push((cfg[`screw${i}_name`] as string | undefined) || t('tune.helpers.screw_n', { n: i }))
  const current = typeof bs.current_screw === 'number' ? bs.current_screw : 0
  const accepted = typeof bs.accepted_screws === 'number' ? bs.accepted_screws : 0
  const total = screws.length || 1
  return (
    <Dialog
      open={open}
      onClose={() => setOpen(false)}
      title={t('tune.shared.bed_screws_adjust')}
      footer={
        <>
          <button className="btn btn-danger" onClick={() => (run('ABORT'), setOpen(false))}>
            {t('tune.shared.abort')}
          </button>
          <button className="btn" onClick={() => run('ADJUSTED')}>
            {t('tune.helpers.adjusted')}
          </button>
          <button className="btn btn-primary" onClick={() => run('ACCEPT')}>
            {t('tune.shared.accept')}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-2">
        <div className="tile col-span-2">
          <span className="section-label">{t('tune.helpers.screw')}</span>
          <span className="v">{screws[current] ? prettyName(screws[current]) : '—'}</span>
          <span className="text-ink-3">
            {bs.state === 'fine' ? t('tune.helpers.screw_of_fine', { current: current + 1, total }) : t('tune.helpers.screw_of', { current: current + 1, total })}
          </span>
        </div>
        <div className="tile col-span-2">
          <span className="section-label">{t('tune.helpers.accepted_screws')}</span>
          <span className="v num">{tr('tune.helpers.accepted_of', { accepted, total }, { small: (c) => <small>{c}</small> })}</span>
          <div className="bar mt-1">
            <i style={{ width: `${(accepted / total) * 100}%` }} />
          </div>
        </div>
      </div>
      <p className="mb-0 text-ink-2">
        {tr('tune.helpers.screws_hint', {}, { b: (c) => <b>{c}</b> })}
      </p>
    </Dialog>
  )
}

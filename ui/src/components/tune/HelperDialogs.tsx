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
      title="Manual probe"
      footer={
        <>
          <button className="btn btn-danger" onClick={() => (run('ABORT'), setOpen(false))}>
            Abort
          </button>
          <button className="btn btn-primary" onClick={() => run('ACCEPT')}>
            Accept
          </button>
        </>
      }
    >
      <p className="mt-0 text-ink-2">Lower the nozzle until it just grips a sheet of paper, then Accept. The steps below move Z by that many millimetres.</p>
      <div className="grid grid-cols-3 gap-2">
        {[
          ['Z min', mp.z_position_lower],
          ['Z', mp.z_position],
          ['Z max', mp.z_position_upper],
        ].map(([k, v]) => (
          <div key={k as string} className={`tile text-center ${k === 'Z' ? 'hot' : ''}`}>
            <span className="section-label">{k as string}</span>
            <span className="v num">{fmt(v)}</span>
          </div>
        ))}
      </div>
      <div className="mt-3 flex items-center justify-between gap-2">
        <div className="seg">
          <button disabled={disabled} onClick={() => run('TESTZ Z=--')} title="Halfway to Z min">
            − −
          </button>
          <button disabled={disabled} onClick={() => run('TESTZ Z=-')} title="Partway to Z min">
            −
          </button>
        </div>
        <span className="text-[12px] text-ink-3">Bisect</span>
        <div className="seg">
          <button disabled={disabled} onClick={() => run('TESTZ Z=+')} title="Partway to Z max">
            +
          </button>
          <button disabled={disabled} onClick={() => run('TESTZ Z=++')} title="Halfway to Z max">
            + +
          </button>
        </div>
      </div>
      <div className="mt-3 grid gap-1.5">
        {offsets.map((o) => (
          <div key={o} className="flex items-center gap-2">
            <button className="pm" disabled={disabled} onClick={() => run(`TESTZ Z=-${o}`)} aria-label={`Lower ${o} mm`}>
              <Minus size={15} />
            </button>
            <div className="num flex-1 text-center font-semibold">{o} mm</div>
            <button className="pm" disabled={disabled} onClick={() => run(`TESTZ Z=+${o}`)} aria-label={`Raise ${o} mm`}>
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
  for (let i = 1; cfg?.[`screw${i}`]; i++) screws.push((cfg[`screw${i}_name`] as string | undefined) || `Screw ${i}`)
  const current = typeof bs.current_screw === 'number' ? bs.current_screw : 0
  const accepted = typeof bs.accepted_screws === 'number' ? bs.accepted_screws : 0
  const total = screws.length || 1
  return (
    <Dialog
      open={open}
      onClose={() => setOpen(false)}
      title="Bed screws adjust"
      footer={
        <>
          <button className="btn btn-danger" onClick={() => (run('ABORT'), setOpen(false))}>
            Abort
          </button>
          <button className="btn" onClick={() => run('ADJUSTED')}>
            Adjusted
          </button>
          <button className="btn btn-primary" onClick={() => run('ACCEPT')}>
            Accept
          </button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-2">
        <div className="tile col-span-2">
          <span className="section-label">Screw</span>
          <span className="v">{screws[current] ? prettyName(screws[current]) : '—'}</span>
          <span className="text-ink-3">
            {current + 1} of {total}
            {bs.state === 'fine' ? ' · fine adjustment' : ''}
          </span>
        </div>
        <div className="tile col-span-2">
          <span className="section-label">Accepted screws</span>
          <span className="v num">
            {accepted} <small>of {total}</small>
          </span>
          <div className="bar mt-1">
            <i style={{ width: `${(accepted / total) * 100}%` }} />
          </div>
        </div>
      </div>
      <p className="mb-0 text-ink-2">
        Press <b>Adjusted</b> if a significant adjustment is necessary on the current screw; otherwise, press <b>Accept</b> to continue.
      </p>
    </Dialog>
  )
}

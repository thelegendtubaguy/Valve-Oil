import { useState, type ReactNode } from 'react'
import { Layers } from 'lucide-react'
import { CancelButton, Dialog, Field, Toggle } from '../ui'
import { send } from '../control/common'
import { encodeGcodeParamValue, gcode } from '../control/gcode'
import { registerObjects, usePrinter } from '../../state/printer'
import { t } from '../../i18n'

// Pause at layer, as Fluidd's PauseAtLayerDialog: it needs the SET_PAUSE_NEXT_LAYER or SET_PAUSE_AT_LAYER macros, and
// reads their state from the SET_PRINT_STATS_INFO macro's variables. QIDI's stock macros don't have them, so this shows
// only on printers that add them.
registerObjects((n) => n === 'gcode_macro SET_PRINT_STATS_INFO')

type PauseNextLayer = { enable: boolean; call: string }
type PauseAtLayerState = { enable: boolean; call: string; layer: number }

function usePauseAtLayer() {
  return usePrinter((s) => {
    const has = (n: string) => s.objects.some((o) => o.toLowerCase() === `gcode_macro ${n.toLowerCase()}`)
    const vars = s.status['gcode_macro SET_PRINT_STATS_INFO'] ?? {}
    const info = s.status.print_stats?.info ?? {}
    return {
      next: has('SET_PAUSE_NEXT_LAYER'),
      at: has('SET_PAUSE_AT_LAYER'),
      // As Fluidd's PauseResumeBtn: offered when the variables exist and the slicer reports layers.
      available: (has('SET_PAUSE_NEXT_LAYER') || has('SET_PAUSE_AT_LAYER')) && 'pause_next_layer' in vars && 'pause_at_layer' in vars && typeof info.total_layer === 'number' && typeof info.current_layer === 'number',
      pauseNext: vars.pause_next_layer as PauseNextLayer | undefined,
      pauseAt: vars.pause_at_layer as PauseAtLayerState | undefined,
      current: (info.current_layer as number | undefined) ?? 0,
      total: (info.total_layer as number | undefined) ?? 0,
    }
  })
}

/** A button that opens the dialog; amber while a pause is set. */
export function PauseAtLayerButton({ className = 'btn btn-sm' }: { className?: string }) {
  const p = usePauseAtLayer()
  const [open, setOpen] = useState(false)
  if (!p.available) return null
  const armed = p.pauseNext?.enable ? t('home.pause_layer.armed_next') : p.pauseAt?.enable ? t('home.pause_layer.armed_layer', { layer: p.pauseAt.layer }) : null
  return (
    <>
      <button className={`${className} ${armed ? 'text-accent-ink!' : ''}`} onClick={() => setOpen(true)}>
        <Layers size={13} /> {armed ?? t('home.pause_layer.button')}
      </button>
      {open && <PauseAtLayerDialog onClose={() => setOpen(false)} />}
    </>
  )
}

const CALLS = ['PAUSE', 'M600']

function PauseAtLayerDialog({ onClose }: { onClose: () => void }) {
  const p = usePauseAtLayer()
  const [next, setNext] = useState<PauseNextLayer>({ enable: false, call: 'PAUSE', ...p.pauseNext })
  const [at, setAt] = useState<PauseAtLayerState>({ enable: false, call: 'PAUSE', layer: 0, ...p.pauseAt })
  const [layer, setLayer] = useState(String(at.layer || p.current + 1))
  const n = Number(layer)
  const layerError = at.enable && !(Number.isInteger(n) && n > p.current && n <= p.total) ? t('home.pause_layer.layer_error', { current: p.current, total: p.total }) : null
  const accept = () => {
    if (layerError) return
    const lines: string[] = []
    if (p.next) lines.push(next.enable ? `SET_PAUSE_NEXT_LAYER ENABLE=1 MACRO=${encodeGcodeParamValue(next.call)}` : 'SET_PAUSE_NEXT_LAYER ENABLE=0')
    if (p.at) lines.push(at.enable ? gcode`SET_PAUSE_AT_LAYER ENABLE=1 LAYER=${n} MACRO=${encodeGcodeParamValue(at.call)}` : 'SET_PAUSE_AT_LAYER ENABLE=0')
    send(lines.join('\n'))
    onClose()
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('home.pause_layer.title')}
      footer={
        <>
          <CancelButton onClick={onClose} />
          <button className="btn btn-primary" disabled={!!layerError} onClick={accept}>
            {t('home.pause_layer.accept')}
          </button>
        </>
      }
    >
      <div className="grid gap-4">
        <div className="text-ink-2">{t('home.pause_layer.now', { current: p.current, total: p.total })}</div>
        {p.next && (
          <Pause label={t('home.pause_layer.next')} on={next.enable} onToggle={(enable) => setNext((x) => ({ ...x, enable }))}>
            <CallField value={next.call} onChange={(call) => setNext((x) => ({ ...x, call }))} />
          </Pause>
        )}
        {p.at && (
          <Pause label={t('home.pause_layer.at')} on={at.enable} onToggle={(enable) => setAt((x) => ({ ...x, enable }))}>
            <Field label={t('home.pause_layer.layer')} className="gap-1">
              <input className="field" inputMode="numeric" value={layer} onChange={(e) => setLayer(e.target.value)} />
              {layerError && <span className="text-[12px] text-bad">{layerError}</span>}
            </Field>
            <CallField value={at.call} onChange={(call) => setAt((x) => ({ ...x, call }))} />
          </Pause>
        )}
      </div>
    </Dialog>
  )
}

/** A pause's switch, and its settings while it's on. */
function Pause({ label, on, onToggle, children }: { label: string; on: boolean; onToggle: (on: boolean) => void; children: ReactNode }) {
  return (
    <div className="grid gap-2">
      <label className="flex items-center gap-3">
        <span className="flex-1 font-semibold">{label}</span>
        <Toggle label={label} checked={on} onChange={onToggle} />
      </label>
      {on && children}
    </div>
  )
}

function CallField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Field label={t('home.pause_layer.command')} className="gap-1">
      <input className="field code" list="vo-pause-calls" value={value} onChange={(e) => onChange(e.target.value)} />
      <datalist id="vo-pause-calls">
        {CALLS.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
    </Field>
  )
}

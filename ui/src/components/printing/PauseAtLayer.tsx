import { useState } from 'react'
import { Layers } from 'lucide-react'
import { Dialog, Toggle } from '../ui'
import { send } from '../control/common'
import { encodeGcodeParamValue } from '../control/gcode'
import { registerObjects, usePrinter } from '../../state/printer'

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
  const armed = p.pauseNext?.enable ? 'next layer' : p.pauseAt?.enable ? `layer ${p.pauseAt.layer}` : null
  return (
    <>
      <button className={`${className} ${armed ? 'text-accent-ink!' : ''}`} onClick={() => setOpen(true)}>
        <Layers size={13} /> {armed ? `Pause at ${armed}` : 'Pause at layer'}
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
  const layerError = at.enable && !(Number.isInteger(n) && n > p.current && n <= p.total) ? `Enter a layer after ${p.current}, up to ${p.total}.` : null
  const accept = () => {
    if (layerError) return
    const lines: string[] = []
    if (p.next) lines.push(next.enable ? `SET_PAUSE_NEXT_LAYER ENABLE=1 MACRO=${encodeGcodeParamValue(next.call)}` : 'SET_PAUSE_NEXT_LAYER ENABLE=0')
    if (p.at) lines.push(at.enable ? `SET_PAUSE_AT_LAYER ENABLE=1 LAYER=${n} MACRO=${encodeGcodeParamValue(at.call)}` : 'SET_PAUSE_AT_LAYER ENABLE=0')
    send(lines.join('\n'))
    onClose()
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title="Pause at layer"
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={!!layerError} onClick={accept}>
            Accept
          </button>
        </>
      }
    >
      <div className="grid gap-4">
        <div className="text-ink-2">
          Now on layer {p.current} of {p.total}.
        </div>
        {p.next && (
          <div className="grid gap-2">
            <label className="flex items-center gap-3">
              <span className="flex-1 font-semibold">Pause at next layer</span>
              <Toggle label="Pause at next layer" checked={next.enable} onChange={(enable) => setNext((x) => ({ ...x, enable }))} />
            </label>
            {next.enable && <CallField value={next.call} onChange={(call) => setNext((x) => ({ ...x, call }))} />}
          </div>
        )}
        {p.at && (
          <div className="grid gap-2">
            <label className="flex items-center gap-3">
              <span className="flex-1 font-semibold">Pause at layer number</span>
              <Toggle label="Pause at layer number" checked={at.enable} onChange={(enable) => setAt((x) => ({ ...x, enable }))} />
            </label>
            {at.enable && (
              <>
                <label className="grid gap-1">
                  <span className="lbl">Layer</span>
                  <input className="field" inputMode="numeric" value={layer} onChange={(e) => setLayer(e.target.value)} />
                  {layerError && <span className="text-[12px] text-bad">{layerError}</span>}
                </label>
                <CallField value={at.call} onChange={(call) => setAt((x) => ({ ...x, call }))} />
              </>
            )}
          </div>
        )}
      </div>
    </Dialog>
  )
}

function CallField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <label className="grid gap-1">
      <span className="lbl">Command</span>
      <input className="field code" list="vo-pause-calls" value={value} onChange={(e) => onChange(e.target.value)} />
      <datalist id="vo-pause-calls">
        {CALLS.map((c) => (
          <option key={c} value={c} />
        ))}
      </datalist>
    </label>
  )
}

import { useState } from 'react'
import { Ban, Shapes } from 'lucide-react'
import { Dialog, confirm } from '../ui'
import { send } from '../control/common'
import { excludeObjectGcode } from '../control/gcode'
import { usePrinter } from '../../state/printer'

type Part = { name: string; center?: [number, number]; polygon?: [number, number][] }
export type ExcludeObjectState = { objects: Part[]; excluded_objects: string[]; current_object: string | null }

export function useExcludeObject(): ExcludeObjectState {
  return usePrinter((s) => {
    const eo = s.status.exclude_object
    return { objects: (eo?.objects as Part[] | undefined) ?? [], excluded_objects: (eo?.excluded_objects as string[] | undefined) ?? [], current_object: (eo?.current_object as string | null | undefined) ?? null }
  })
}

/** Klipper's object names as people read them: "planter_body_id_0_copy_1" stays as is, but without shouting. */
export const partLabel = (name: string) => name.replace(/_/g, ' ')

async function exclude(name: string) {
  if (!(await confirm({ title: `Exclude ${partLabel(name)}?`, body: 'Are you sure you want to exclude this object from printing? The printer skips it for the rest of this print, and it can’t be added back.', confirm: 'Exclude', danger: true }))) return
  send(excludeObjectGcode(name))
}

/** A button for the print card that opens the object list, when the file defines objects. */
export function ExcludeObjectsButton({ className = 'btn btn-sm' }: { className?: string }) {
  const eo = useExcludeObject()
  const [open, setOpen] = useState(false)
  if (!eo.objects.length) return null
  const excluded = eo.excluded_objects.length
  return (
    <>
      <button className={className} onClick={() => setOpen(true)} title="Exclude objects">
        <Shapes size={13} /> Objects <span className="text-ink-3">{excluded ? `${eo.objects.length - excluded} of ${eo.objects.length}` : eo.objects.length}</span>
      </button>
      {open && <ExcludeObjectsDialog onClose={() => setOpen(false)} />}
    </>
  )
}

export function ExcludeObjectsDialog({ onClose }: { onClose: () => void }) {
  const eo = useExcludeObject()
  const [hover, setHover] = useState<string | null>(null)
  return (
    <Dialog open onClose={onClose} title="Exclude objects" size="wide">
      <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,280px)]">
        <ObjectMap eo={eo} hover={hover} onHover={setHover} onPick={exclude} />
        <div className="grid content-start">
          {eo.objects.map((o) => {
            const out = eo.excluded_objects.includes(o.name)
            const now = eo.current_object === o.name
            return (
              <div key={o.name} className={`flex items-center gap-2 rounded-lg px-2 py-1.5 ${hover === o.name ? 'bg-hover' : ''}`} onPointerEnter={() => setHover(o.name)} onPointerLeave={() => setHover(null)}>
                <span className="h-2.5 w-2.5 flex-none rounded-full" style={{ background: out ? 'var(--red)' : now ? 'var(--accent)' : 'var(--green)' }} />
                <span className={`min-w-0 flex-1 text-[12.5px] break-all ${out ? 'text-ink-3 line-through' : ''}`}>{partLabel(o.name)}</span>
                {now && <span className="pill tone-accent px-2! py-0.5! text-[11px]!">printing</span>}
                {!out && (
                  <button className="btn btn-sm btn-danger px-2!" onClick={() => exclude(o.name)} aria-label={`Exclude ${partLabel(o.name)}`} title="Exclude">
                    <Ban size={13} />
                  </button>
                )}
              </div>
            )
          })}
        </div>
      </div>
      <div className="mt-3 text-[12.5px] text-ink-3">Click an object on the bed or in the list to stop printing it. Green objects are still printing, amber is the one printing now, red ones are excluded.</div>
    </Dialog>
  )
}

/** The bed from above, with each object's outline, as Fluidd draws them over the G-code preview. */
function ObjectMap({ eo, hover, onHover, onPick }: { eo: ExcludeObjectState; hover: string | null; onHover: (n: string | null) => void; onPick: (n: string) => void }) {
  const bed = usePrinter((s) => {
    const min = (s.status.toolhead?.axis_minimum as number[] | undefined) ?? [s.settings.stepper_x?.position_min ?? 0, s.settings.stepper_y?.position_min ?? 0]
    const max = (s.status.toolhead?.axis_maximum as number[] | undefined) ?? [s.settings.stepper_x?.position_max ?? 300, s.settings.stepper_y?.position_max ?? 300]
    return { x0: Math.max(0, min[0]), y0: Math.max(0, min[1]), x1: max[0], y1: max[1] }
  })
  const w = bed.x1 - bed.x0
  const h = bed.y1 - bed.y0
  // Y grows up on the bed and down in SVG.
  const pt = ([x, y]: [number, number]) => `${x - bed.x0},${bed.y1 - y}`
  return (
    <svg viewBox={`-4 -4 ${w + 8} ${h + 8}`} className="aspect-square w-full rounded-xl bg-inset" role="img" aria-label="Objects on the bed">
      <rect x={0} y={0} width={w} height={h} rx={6} fill="none" stroke="var(--line-strong)" strokeWidth={1.5} />
      {Array.from({ length: Math.floor(w / 50) }, (_, i) => (
        <line key={`x${i}`} x1={(i + 1) * 50} x2={(i + 1) * 50} y1={0} y2={h} stroke="var(--line)" strokeWidth={0.75} />
      ))}
      {Array.from({ length: Math.floor(h / 50) }, (_, i) => (
        <line key={`y${i}`} y1={h - (i + 1) * 50} y2={h - (i + 1) * 50} x1={0} x2={w} stroke="var(--line)" strokeWidth={0.75} />
      ))}
      {eo.objects.map((o) => {
        const out = eo.excluded_objects.includes(o.name)
        const now = eo.current_object === o.name
        const color = out ? 'var(--red)' : now ? 'var(--accent)' : 'var(--green)'
        const hot = hover === o.name
        if (!o.polygon?.length) {
          if (!o.center) return null
          return <circle key={o.name} cx={o.center[0] - bed.x0} cy={bed.y1 - o.center[1]} r={6} fill={color} />
        }
        return (
          <g key={o.name} className={out ? '' : 'cursor-pointer'} onPointerEnter={() => onHover(o.name)} onPointerLeave={() => onHover(null)} onClick={() => !out && onPick(o.name)}>
            <title>{partLabel(o.name)}</title>
            <polygon points={o.polygon.map(pt).join(' ')} fill={color} fillOpacity={hot && !out ? 0.5 : 0.2} stroke={color} strokeWidth={hot ? 2.5 : 1.5} strokeLinejoin="round" />
            {out && o.center && (
              <g stroke="var(--red)" strokeWidth={2.5} strokeLinecap="round">
                <line x1={o.center[0] - bed.x0 - 7} y1={bed.y1 - o.center[1] - 7} x2={o.center[0] - bed.x0 + 7} y2={bed.y1 - o.center[1] + 7} />
                <line x1={o.center[0] - bed.x0 + 7} y1={bed.y1 - o.center[1] - 7} x2={o.center[0] - bed.x0 - 7} y2={bed.y1 - o.center[1] + 7} />
              </g>
            )}
          </g>
        )
      })}
    </svg>
  )
}

// The filament in the path: loaded to the toolhead, or parked at the hub, or what the Box is doing, as the fork's
// loaded summary shows it.
import { Box as BoxIcon, RefreshCw } from 'lucide-react'
import { Spool } from '../Spool'
import { slotByIndex, type BoxState } from '../../state/box'

export function LoadedSummary({ box, size = 52 }: { box: BoxState; size?: number }) {
  const busy = box.busy && box.task != null && !box.flowPaused
  const slot = slotByIndex(box, box.toolheadSlot ?? box.hubSlot) ?? null
  const loaded = slot != null && slot.index === box.loadedSlot
  const name = !slot ? '' : !slot.present ? 'Empty' : (slot.name ?? 'Unknown filament')
  const tools = slot ? (box.toolsBySlot[slot.index] ?? []) : []
  const overline = busy ? 'Box busy' : !slot ? 'Toolhead' : [loaded ? `Loaded ${slot.label}` : `At hub ${slot.label}`, tools.join(', ')].filter(Boolean).join(' · ')
  const title = busy ? (box.taskStep ?? 'Box busy') : !slot ? 'No Box filament loaded' : name
  const subtitle = !slot ? '' : busy ? `${slot.label} · ${name}` : slot.present ? [slot.vendor, slot.type].filter(Boolean).join(' · ') : ''
  return (
    <div className="flex min-w-0 items-center gap-3">
      {slot ? (
        <Spool slot={slot} size={size} label={false} loaded={loaded} />
      ) : (
        <span className="grid flex-none place-items-center rounded-full text-ink-3 shadow-[inset_0_0_0_2px_var(--line-strong)]" style={{ width: size, height: size }}>
          {busy ? <RefreshCw size={size / 2.6} className="spin" /> : <BoxIcon size={size / 2.6} />}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="section-label truncate">{overline}</div>
        <div className={`truncate text-[17px] font-bold ${busy ? 'shimmer' : ''}`}>{title}</div>
        {subtitle && <div className="truncate text-ink-3">{subtitle}</div>}
        {busy && (
          <div className="bar live mt-2 h-1!">
            <i style={{ width: '100%' }} />
          </div>
        )}
      </div>
    </div>
  )
}

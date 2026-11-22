// The filament path in one row: the spool feeding it, the hub, and the toolhead sensor, the two sensors on the way.
// Each stop lights when its sensor sees filament, and the line up to it takes the filament's colour. The external spool
// skips the hub.
import { Fragment } from 'react'
import { QIDI_BOX_EXTERNAL_SLOT, type BoxState } from '../../state/box'
import { Spool } from '../Spool'

export function FilamentRail({ box, onSelect }: { box: BoxState; onSelect: (slot: number) => void }) {
  const byIndex = new Map(box.slots.map((s) => [s.index, s]))
  const atHub = box.hubSensor === 0
  const sourceIndex = box.toolheadSlot ?? (atHub ? box.hubSlot : null)
  const source = sourceIndex != null ? byIndex.get(sourceIndex) : undefined
  const external = sourceIndex === QIDI_BOX_EXTERNAL_SLOT
  const color = source?.color ?? 'var(--accent)'
  const stops = [
    { key: 'hub', label: 'Hub', on: atHub, skip: external, tip: atHub ? `${box.hubSlot != null ? byIndex.get(box.hubSlot)?.label : 'Filament'} at the hub` : external ? 'The external spool bypasses the hub' : 'Nothing at the hub' },
    { key: 'th', label: 'Toolhead', on: box.toolheadFilament === true, tip: box.toolheadFilament == null ? 'No toolhead sensor reading' : box.toolheadFilament ? 'Filament at the toolhead sensor' : 'Nothing at the toolhead sensor' },
  ]
  // How far the filament reaches: a line is lit up to the furthest stop that sees it.
  const reach = stops.reduce((r, s, i) => (s.on ? i + 1 : r), 0)
  return (
    <div className="frail" role="group" aria-label="Filament path" style={{ '--fc': color } as React.CSSProperties}>
      {source ? (
        <button type="button" className="fr-src" onClick={() => onSelect(source.index)} title={`${source.label} ${box.toolheadSlot != null ? "loaded" : "at the hub"}`}>
          <Spool slot={source} size={18} label={false} />
          <b>{external ? 'Ext' : source.label}</b>
        </button>
      ) : (
        <span className="fr-src none" title="Nothing past the Box">
          <span className="fr-empty" />
        </span>
      )}
      {stops.map((s, i) => (
        <Fragment key={s.key}>
          <span className={`fr-seg ${i < reach ? 'on' : ''}`} />
          <span className={`fr-stop ${s.on ? 'on' : ''} ${s.skip ? 'skip' : ''}`} title={s.tip}>
            <i />
            {s.label}
          </span>
        </Fragment>
      ))}
    </div>
  )
}

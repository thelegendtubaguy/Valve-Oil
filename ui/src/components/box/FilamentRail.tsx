// The filament path in one row: the spool feeding it, the hub, and the toolhead sensor, the two sensors on the way.
// Each stop lights when its sensor sees filament, and the line up to it takes the filament's colour. The external spool
// skips the hub.
import { Fragment } from 'react'
import { QIDI_BOX_EXTERNAL_SLOT, type BoxState } from '../../state/box'
import { Spool } from '../Spool'
import { t } from '../../i18n'

export function FilamentRail({ box, onSelect }: { box: BoxState; onSelect: (slot: number) => void }) {
  const byIndex = new Map(box.slots.map((s) => [s.index, s]))
  const atHub = box.hubSensor === 0
  const sourceIndex = box.toolheadSlot ?? (atHub ? box.hubSlot : null)
  const source = sourceIndex != null ? byIndex.get(sourceIndex) : undefined
  const external = sourceIndex === QIDI_BOX_EXTERNAL_SLOT
  const color = source?.color ?? 'var(--accent)'
  const hubLabel = box.hubSlot != null ? byIndex.get(box.hubSlot)?.label : undefined
  const stops = [
    {
      key: 'hub',
      label: t('box.path.hub'),
      on: atHub,
      skip: external,
      tip: atHub ? (hubLabel ? t('box.path.slot_at_hub', { slot: hubLabel }) : t('box.path.filament_at_hub')) : external ? t('box.path.external_bypasses') : t('box.path.nothing_at_hub'),
    },
    {
      key: 'th',
      label: t('box.path.toolhead'),
      on: box.toolheadFilament === true,
      tip: box.toolheadFilament == null ? t('box.path.no_toolhead_reading') : box.toolheadFilament ? t('box.path.filament_at_toolhead') : t('box.path.nothing_at_toolhead'),
    },
  ]
  // How far the filament reaches: a line is lit up to the furthest stop that sees it.
  const reach = stops.reduce((r, s, i) => (s.on ? i + 1 : r), 0)
  return (
    <div className="frail" role="group" aria-label={t('box.screen.filament_path')} style={{ '--fc': color } as React.CSSProperties}>
      {source ? (
        <button type="button" className="fr-src" onClick={() => onSelect(source.index)} title={box.toolheadSlot != null ? t('box.path.slot_loaded', { slot: source.label }) : t('box.path.slot_at_hub', { slot: source.label })}>
          <Spool slot={source} size={18} label={false} />
          <b>{external ? t('box.external_short') : source.label}</b>
        </button>
      ) : (
        <span className="fr-src none" title={t('box.path.nothing_past_box')}>
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

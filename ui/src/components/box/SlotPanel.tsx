// The selected slot: what it holds, and its actions on one row, enabled exactly as the fork's QIDI Box card enables
// them.
import { useState } from 'react'
import { ArrowDown, ArrowUp, Flame, Nfc, Pencil, RefreshCw, Tag } from 'lucide-react'
import { Spool } from '../Spool'
import { send } from '../control/common'
import { TagWriteDialog, WriteTagIcon } from '../TagWriteDialog'
import { boxCommands, reloadText, slotActions, slotOverline, QIDI_BOX_EXTERNAL_SLOT, type BoxState, type QidiBoxSlot } from '../../state/box'
import { FilamentDialog } from './FilamentDialog'
import { t } from '../../i18n'

const JOG = 10

/** Read RFID and Write spool tag, as icon buttons. */
function TagButtons({ box, slot, onWrite }: { box: BoxState; slot: QidiBoxSlot; onWrite: () => void }) {
  const a = slotActions(box, slot)
  const isBox = slot.index !== QIDI_BOX_EXTERNAL_SLOT
  return (
    <span className="inline-flex gap-0.5 -mt-1 -mr-1.5">
      {isBox && (
        <button className="iconbtn" disabled={!a.rfid} title={t('box.slot.read_rfid')} aria-label={t('box.slot.read_rfid')} onClick={() => send(boxCommands.rfid(box, slot.index))}>
          <Nfc size={16} />
        </button>
      )}
      {box.canWriteTag && isBox && (
        <button className="iconbtn" disabled={!a.writeTag} title={t('box.slot.write_tag')} aria-label={t('box.slot.write_tag')} onClick={onWrite}>
          <WriteTagIcon size={16} />
        </button>
      )}
    </span>
  )
}

/** Load, Unload, Eject, Cut, and In/Out. */
function SlotActions({ box, slot }: { box: BoxState; slot: QidiBoxSlot }) {
  const a = slotActions(box, slot)
  const [cutting, setCutting] = useState(false)
  const isBox = slot.index !== QIDI_BOX_EXTERNAL_SLOT
  // The external spool has only Cut, and only while it's the filament loaded to the toolhead.
  const showCut = box.canCut && (isBox || box.loadedSlot === slot.index)
  const cut = async () => {
    setCutting(true)
    await send(boxCommands.cut())
    setCutting(false)
  }
  return (
    <div className={isBox || showCut ? 'mt-3.5' : ''}>
      {(isBox || showCut) && (
        <div className="flex flex-wrap items-center gap-1.5">
          {isBox && (
            <>
              <button className={`btn btn-sm ${a.load ? 'btn-primary' : ''}`} disabled={!a.load} onClick={() => send(boxCommands.load(box, slot.index))}>
                {t('box.slot.load')}
              </button>
              <button className="btn btn-sm" disabled={!a.unload} onClick={() => send(boxCommands.unload(box, slot.index))}>
                {t('box.slot.unload')}
              </button>
              <button className="btn btn-sm" disabled={!a.eject} onClick={() => send(boxCommands.eject(box, slot.index))}>
                {t('box.slot.eject')}
              </button>
            </>
          )}
          {showCut && (
            <button className="btn btn-sm" disabled={!a.cut || cutting} onClick={cut} title={t('box.slot.cut_title')}>
              {t('box.slot.cut')}
            </button>
          )}
          {box.canJog && isBox && (
            <span className="inline-flex">
              <button className="btn btn-sm rounded-r-none! px-2!" disabled={!a.jog} onClick={() => send(boxCommands.jog(slot.index, JOG))} title={t('box.slot.jog_in_title', { mm: JOG })} aria-label={t('box.slot.jog_in_title', { mm: JOG })}>
                <ArrowUp size={14} /> {t('box.slot.jog_in')}
              </button>
              <button className="btn btn-sm ml-px rounded-l-none! px-2!" disabled={!a.jog} onClick={() => send(boxCommands.jog(slot.index, -JOG))} title={t('box.slot.jog_out_title', { mm: JOG })} aria-label={t('box.slot.jog_out_title', { mm: JOG })}>
                <ArrowDown size={14} /> {t('box.slot.jog_out')}
              </button>
            </span>
          )}
        </div>
      )}
      {a.hint && <div className="tone-warn mt-2.5 rounded-lg px-3 py-2 text-[12.5px] text-ink">{a.hint}</div>}
      {box.printState === 'printing' && isBox && <div className="mt-2 text-[12.5px] text-ink-3">{t('box.slot.printing_hint')}</div>}
      {!box.canEditFilament && box.filamentDatabaseInvalid && <div className="mt-2 text-[12.5px] text-bad">{t('box.slot.database_invalid')}</div>}
    </div>
  )
}

export function SlotPanel({ box, slot }: { box: BoxState; slot: QidiBoxSlot }) {
  const [editOpen, setEditOpen] = useState(false)
  const [tagOpen, setTagOpen] = useState(false)
  const isBox = slot.index !== QIDI_BOX_EXTERNAL_SLOT
  // The filament as one name: the vendor, then its name, unless the name already starts with it ("QIDI PLA Basic").
  const name = slot.name ?? slot.type
  const filament = !slot.present ? t('box.empty') : name ? (slot.vendor && !name.toLowerCase().startsWith(slot.vendor.toLowerCase()) ? `${slot.vendor} ${name}` : name) : t('box.unknown_filament')
  const details: { key: string; icon?: React.ReactNode; text: string; cls?: string }[] = [
    ...(slot.present && slot.minTemp != null && slot.maxTemp != null ? [{ key: 'nozzle', text: t('box.slot.nozzle_temps', { min: slot.minTemp, max: slot.maxTemp }) }] : []),
    ...(slot.present && slot.boxMaxTemp != null && slot.boxMaxTemp > 0 ? [{ key: 'heat', icon: <Flame size={12} />, text: t('box.slot.heat_up_to', { temp: slot.boxMaxTemp }) }] : []),
    ...(slot.reading ? [{ key: 'rfid', icon: <Nfc size={12} />, text: t('box.slot.reading'), cls: 'text-accent-ink' }] : []),
    ...(box.fromTag[slot.index] && !slot.reading ? [{ key: 'tag', icon: <Tag size={12} />, text: t('box.slot.from_tag'), cls: 'text-accent-ink' }] : []),
    ...(slot.present && isBox ? [{ key: 'reload', icon: <RefreshCw size={12} />, text: reloadText(box, slot) }] : []),
  ]
  return (
    <section>
      <div className="flex items-start gap-3">
        <Spool slot={slot} size={56} label={false} loaded={box.loadedSlot === slot.index} />
        <div className="min-w-0 flex-1">
          <div className="section-label truncate">{slotOverline(box, slot)}</div>
          {/* The filament's name is its edit button: material, brand, and color. */}
          {box.canEditFilament && slot.present ? (
            <button type="button" className="sp-edit" disabled={!slotActions(box, slot).edit} onClick={() => setEditOpen(true)} title={t('box.slot.change_filament')}>
              <span className="truncate">{filament}</span>
              <Pencil size={15} />
            </button>
          ) : (
            <div className={`truncate text-[18px] font-bold ${slot.present && name ? '' : 'text-ink-3'}`}>{filament}</div>
          )}
        </div>
        <TagButtons box={box} slot={slot} onWrite={() => setTagOpen(true)} />
      </div>
      {details.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-x-3.5 gap-y-1 text-[12.5px] text-ink-3">
          {details.map((d) => (
            <span key={d.key} className={`inline-flex items-center gap-1 ${d.cls ?? ''}`}>
              {d.icon}
              {d.text}
            </span>
          ))}
        </div>
      )}
      <SlotActions box={box} slot={slot} />
      {editOpen && <FilamentDialog box={box} slot={slot} onClose={() => setEditOpen(false)} />}
      {tagOpen && <TagWriteDialog box={box} slot={slot} onClose={() => setTagOpen(false)} />}
    </section>
  )
}

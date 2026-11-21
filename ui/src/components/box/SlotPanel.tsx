// The selected slot: what it holds, and its actions on one row, enabled exactly as the fork's QIDI Box card enables
// them.
import { useState } from 'react'
import { ArrowDown, ArrowUp, Flame, Nfc, Pencil, RefreshCw, Tag } from 'lucide-react'
import { Spool } from '../Spool'
import { attempt } from '../toast'
import { TagWriteDialog, WriteTagIcon } from '../TagWriteDialog'
import { boxCommands, reloadText, slotActions, slotOverline, QIDI_BOX_EXTERNAL_SLOT, type BoxState, type QidiBoxSlot } from '../../state/box'
import { runGcode } from '../../state/printer'
import { FilamentDialog } from './FilamentDialog'
import { t } from '../../i18n'

const JOG = 10

/** Read RFID, Write spool tag, and Edit filament, as icon buttons. */
function TagButtons({ box, slot, className = '', onEdit, onWrite }: { box: BoxState; slot: QidiBoxSlot; className?: string; onEdit: () => void; onWrite: () => void }) {
  const a = slotActions(box, slot)
  const isBox = slot.index !== QIDI_BOX_EXTERNAL_SLOT
  return (
    <span className={`inline-flex gap-0.5 ${className}`}>
      {isBox && (
        <button className="iconbtn" disabled={!a.rfid} title={t('box.slot.read_rfid')} aria-label={t('box.slot.read_rfid')} onClick={() => attempt(() => runGcode(boxCommands.rfid(box, slot.index)))}>
          <Nfc size={16} />
        </button>
      )}
      {box.canWriteTag && isBox && (
        <button className="iconbtn" disabled={!a.writeTag} title={t('box.slot.write_tag')} aria-label={t('box.slot.write_tag')} onClick={onWrite}>
          <WriteTagIcon size={16} />
        </button>
      )}
      {box.canEditFilament && (
        <button className="iconbtn" disabled={!a.edit} title={t('box.slot.edit_filament')} aria-label={t('box.slot.edit_filament')} onClick={onEdit}>
          <Pencil size={15} />
        </button>
      )}
    </span>
  )
}

/** Load, Unload, Eject, Cut, and In/Out, then (unless the caller shows them) reading and writing the tag and editing the filament. */
export function SlotActions({ box, slot, className = '', withTagButtons = true, editOpen: editOpenProp, onEditOpen, tagOpen: tagOpenProp, onTagOpen }: { box: BoxState; slot: QidiBoxSlot; className?: string; withTagButtons?: boolean; editOpen?: boolean; onEditOpen?: (v: boolean) => void; tagOpen?: boolean; onTagOpen?: (v: boolean) => void }) {
  const a = slotActions(box, slot)
  const [editOwn, setEditOwn] = useState(false)
  const [tagOwn, setTagOwn] = useState(false)
  const editOpen = editOpenProp ?? editOwn
  const setEditOpen = onEditOpen ?? setEditOwn
  const tagOpen = tagOpenProp ?? tagOwn
  const setTagOpen = onTagOpen ?? setTagOwn
  const [cutting, setCutting] = useState(false)
  const run = (g: string) => attempt(() => runGcode(g))
  const isBox = slot.index !== QIDI_BOX_EXTERNAL_SLOT
  const cut = async () => {
    setCutting(true)
    await run(boxCommands.cut())
    setCutting(false)
  }
  return (
    <div className={className}>
      <div className="flex flex-wrap items-center gap-1.5">
        <button className="btn btn-sm btn-primary" disabled={!a.load} onClick={() => run(boxCommands.load(box, slot.index))}>
          {t('box.slot.load')}
        </button>
        <button className="btn btn-sm" disabled={!a.unload} onClick={() => run(boxCommands.unload(box, slot.index))}>
          {t('box.slot.unload')}
        </button>
        <button className="btn btn-sm" disabled={!a.eject} onClick={() => run(boxCommands.eject(box, slot.index))}>
          {t('box.slot.eject')}
        </button>
        {box.canCut && (
          <button className="btn btn-sm" disabled={!a.cut || cutting} onClick={cut} title={t('box.slot.cut_title')}>
            {t('box.slot.cut')}
          </button>
        )}
        {box.canJog && (
          <span className="inline-flex">
            <button className="btn btn-sm rounded-r-none! px-2!" disabled={!a.jog} onClick={() => run(boxCommands.jog(slot.index, JOG))} title={t('box.slot.jog_in_title', { mm: JOG })} aria-label={t('box.slot.jog_in_title', { mm: JOG })}>
              <ArrowUp size={14} /> {t('box.slot.jog_in')}
            </button>
            <button className="btn btn-sm ml-px rounded-l-none! px-2!" disabled={!a.jog} onClick={() => run(boxCommands.jog(slot.index, -JOG))} title={t('box.slot.jog_out_title', { mm: JOG })} aria-label={t('box.slot.jog_out_title', { mm: JOG })}>
              <ArrowDown size={14} /> {t('box.slot.jog_out')}
            </button>
          </span>
        )}
        {withTagButtons && <TagButtons box={box} slot={slot} className="ml-auto" onEdit={() => setEditOpen(true)} onWrite={() => setTagOpen(true)} />}
      </div>
      {!isBox && <div className="mt-2 text-[12.5px] text-ink-3">{t('box.slot.external_hint')}</div>}
      {a.hint && <div className="tone-warn mt-2.5 rounded-lg px-3 py-2 text-[12.5px] text-ink">{a.hint}</div>}
      {box.printState === 'printing' && isBox && <div className="mt-2 text-[12.5px] text-ink-3">{t('box.slot.printing_hint')}</div>}
      {!box.canEditFilament && box.filamentDatabaseInvalid && (
        <div className="mt-2 text-[12.5px] text-bad">{t('box.slot.database_invalid')}</div>
      )}
      {editOpen && <FilamentDialog box={box} slot={slot} onClose={() => setEditOpen(false)} />}
      {tagOpen && <TagWriteDialog box={box} slot={slot} onClose={() => setTagOpen(false)} />}
    </div>
  )
}

export function SlotPanel({ box, slot }: { box: BoxState; slot: QidiBoxSlot }) {
  const [editOpen, setEditOpen] = useState(false)
  const [tagOpen, setTagOpen] = useState(false)
  const isBox = slot.index !== QIDI_BOX_EXTERNAL_SLOT
  const filament = slot.present ? [slot.vendor, slot.type, slot.color].filter(Boolean).join(' · ') : ''
  const details: { key: string; icon?: React.ReactNode; text: string; cls?: string }[] = [
    ...(filament ? [{ key: 'filament', text: filament }] : []),
    ...(slot.minTemp != null && slot.maxTemp != null ? [{ key: 'nozzle', text: t('box.slot.nozzle_range', { min: slot.minTemp, max: slot.maxTemp }) }] : []),
    ...(slot.boxMaxTemp != null && slot.boxMaxTemp > 0 ? [{ key: 'drying', icon: <Flame size={12} />, text: t('box.slot.box_heating', { max: slot.boxMaxTemp }) }] : []),
    ...(slot.reading ? [{ key: 'rfid', icon: <Nfc size={12} />, text: t('box.slot.reading'), cls: 'text-accent-ink' }] : []),
    ...(box.fromTag[slot.index] && !slot.reading ? [{ key: 'tag', icon: <Tag size={12} />, text: t('box.slot.from_tag'), cls: 'text-accent-ink' }] : []),
    ...(slot.present && isBox ? [{ key: 'reload', icon: <RefreshCw size={12} />, text: reloadText(box, slot) }] : []),
  ]
  const boxNumber = Math.floor(slot.index / 4) + 1
  return (
    <section className="card">
      <div className="flex items-start gap-3">
        <Spool slot={slot} size={56} label={false} loaded={box.loadedSlot === slot.index} />
        <div className="min-w-0 flex-1">
          <div className="section-label truncate">{slotOverline(box, slot)}</div>
          <div className="truncate text-[18px] font-bold">{isBox ? t('box.box_slot', { box: boxNumber, slot: slot.label }) : t('box.external_spool')}</div>
          <div className={`truncate font-semibold ${slot.present && slot.name ? '' : 'text-ink-3'}`}>{slot.present ? (slot.name ?? t('box.unknown_filament')) : t('box.empty')}</div>
        </div>
        <TagButtons box={box} slot={slot} className="-mt-1 -mr-1.5" onEdit={() => setEditOpen(true)} onWrite={() => setTagOpen(true)} />
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
      <SlotActions box={box} slot={slot} className="mt-3.5" withTagButtons={false} editOpen={editOpen} onEditOpen={setEditOpen} tagOpen={tagOpen} onTagOpen={setTagOpen} />
    </section>
  )
}

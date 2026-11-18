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

const JOG = 10

/** Read RFID, Write spool tag, and Edit filament, as icon buttons. */
function TagButtons({ box, slot, className = '', onEdit, onWrite }: { box: BoxState; slot: QidiBoxSlot; className?: string; onEdit: () => void; onWrite: () => void }) {
  const a = slotActions(box, slot)
  const isBox = slot.index !== QIDI_BOX_EXTERNAL_SLOT
  return (
    <span className={`inline-flex gap-0.5 ${className}`}>
      {isBox && (
        <button className="iconbtn" disabled={!a.rfid} title="Read RFID" aria-label="Read RFID" onClick={() => attempt(() => runGcode(boxCommands.rfid(box, slot.index)))}>
          <Nfc size={16} />
        </button>
      )}
      {box.canWriteTag && isBox && (
        <button className="iconbtn" disabled={!a.writeTag} title="Write spool tag" aria-label="Write spool tag" onClick={onWrite}>
          <WriteTagIcon size={16} />
        </button>
      )}
      {box.canEditFilament && (
        <button className="iconbtn" disabled={!a.edit} title="Edit filament" aria-label="Edit filament" onClick={onEdit}>
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
          Load
        </button>
        <button className="btn btn-sm" disabled={!a.unload} onClick={() => run(boxCommands.unload(box, slot.index))}>
          Unload
        </button>
        <button className="btn btn-sm" disabled={!a.eject} onClick={() => run(boxCommands.eject(box, slot.index))}>
          Eject
        </button>
        {box.canCut && (
          <button className="btn btn-sm" disabled={!a.cut || cutting} onClick={cut} title="Cut the filament at the toolhead without heating">
            Cut
          </button>
        )}
        {box.canJog && (
          <span className="inline-flex">
            <button className="btn btn-sm rounded-r-none! px-2!" disabled={!a.jog} onClick={() => run(boxCommands.jog(slot.index, JOG))} title={`In ${JOG} mm`} aria-label={`In ${JOG} mm`}>
              <ArrowUp size={14} /> In
            </button>
            <button className="btn btn-sm ml-px rounded-l-none! px-2!" disabled={!a.jog} onClick={() => run(boxCommands.jog(slot.index, -JOG))} title={`Out ${JOG} mm`} aria-label={`Out ${JOG} mm`}>
              <ArrowDown size={14} /> Out
            </button>
          </span>
        )}
        {withTagButtons && <TagButtons box={box} slot={slot} className="ml-auto" onEdit={() => setEditOpen(true)} onWrite={() => setTagOpen(true)} />}
      </div>
      {!isBox && <div className="mt-2 text-[12.5px] text-ink-3">The external spool feeds the toolhead directly, so it has no Box controls. Load it by hand.</div>}
      {a.hint && <div className="tone-warn mt-2.5 rounded-lg px-3 py-2 text-[12.5px] text-ink">{a.hint}</div>}
      {box.printState === 'printing' && isBox && <div className="mt-2 text-[12.5px] text-ink-3">Loading and unloading are unavailable while printing.</div>}
      {!box.canEditFilament && box.filamentDatabaseInvalid && (
        <div className="mt-2 text-[12.5px] text-bad">The filament list qidi_filament_config/filament-0.4.json lists no filaments. Check it is valid JSON, then restart the printer.</div>
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
    ...(slot.minTemp != null && slot.maxTemp != null ? [{ key: 'nozzle', text: `Nozzle ${slot.minTemp}–${slot.maxTemp}°C` }] : []),
    ...(slot.boxMaxTemp != null && slot.boxMaxTemp > 0 ? [{ key: 'drying', icon: <Flame size={12} />, text: `Box heating up to ${slot.boxMaxTemp}°C` }] : []),
    ...(slot.reading ? [{ key: 'rfid', icon: <Nfc size={12} />, text: 'Reading RFID…', cls: 'text-accent-ink' }] : []),
    ...(box.fromTag[slot.index] && !slot.reading ? [{ key: 'tag', icon: <Tag size={12} />, text: 'Set from the spool’s tag', cls: 'text-accent-ink' }] : []),
    ...(slot.present && isBox ? [{ key: 'reload', icon: <RefreshCw size={12} />, text: reloadText(box, slot) }] : []),
  ]
  const boxNumber = Math.floor(slot.index / 4) + 1
  return (
    <section className="card">
      <div className="flex items-start gap-3">
        <Spool slot={slot} size={56} label={false} loaded={box.loadedSlot === slot.index} />
        <div className="min-w-0 flex-1">
          <div className="section-label truncate">{slotOverline(box, slot)}</div>
          <div className="truncate text-[18px] font-bold">{isBox ? `Box ${boxNumber} · Slot ${slot.label}` : 'External spool'}</div>
          <div className={`truncate font-semibold ${slot.present && slot.name ? '' : 'text-ink-3'}`}>{slot.present ? (slot.name ?? 'Unknown filament') : 'Empty'}</div>
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

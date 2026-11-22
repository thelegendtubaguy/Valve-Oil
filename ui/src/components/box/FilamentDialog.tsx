// Sets what a slot holds, as the fork's filament dialog does. Firmware 01.01.07.01 saves what the touchscreen saves: a
// QF filament ID from its filament database, which also carries the vendor, and any color as a one-element RGB list.
// Earlier firmware saves numeric filament, vendor, and color IDs from officiall_filas_list.cfg.
import { useMemo, useState } from 'react'
import { Nfc } from 'lucide-react'
import { Dialog } from '../ui'
import { Spool } from '../Spool'
import { attempt } from '../toast'
import { useStore } from '../../lib/store'
import { normalizeQidiColor } from '../../qidi/qidi-box'
import { boxCommands, filamentLists, slotActions, QIDI_BOX_EXTERNAL_SLOT, type BoxState, type QidiBoxSlot } from '../../state/box'
import { runGcode, usePrinter } from '../../state/printer'
import { ColorSwatches, FilamentPicker, PillRow } from './FilamentPicker'
import { t } from '../../i18n'

export function FilamentDialog({ box, slot, onClose }: { box: BoxState; slot: QidiBoxSlot; onClose: () => void }) {
  const lists = useStore(filamentLists, (l) => l)
  const database = box.hasController ? lists.database : null
  const legacy = lists.legacy
  const savedVendor = usePrinter((s) => {
    const v = s.status.save_variables?.variables?.[`vendor_slot${slot.index}`]
    return v != null ? String(v) : null
  })
  const [filament, setFilament] = useState<string | null>(() => (database ? (database.find((f) => f.id === slot.filamentId)?.id ?? null) : slot.filamentId))
  const [vendor, setVendor] = useState<string | null>(savedVendor)
  const [color, setColor] = useState<string | null>(slot.color)

  // Legacy color IDs by color, for the palette and for saving.
  const colorIds = useMemo(() => {
    const out: Record<string, string> = {}
    for (const [id, c] of Object.entries(legacy?.colordict ?? {})) {
      const hex = normalizeQidiColor(c)
      if (hex) out[hex] = id
    }
    return out
  }, [legacy])

  const busy = box.busy
  const a = slotActions(box, slot)
  const save = async () => {
    if (filament == null || busy) return
    const { index } = slot
    const lines: string[] = []
    if (database) {
      if (filament !== slot.filamentId) lines.push(`SAVE_VARIABLE VARIABLE=filament_slot${index} VALUE='"${filament}"'`)
      if (color && color !== slot.color) lines.push(`SAVE_VARIABLE VARIABLE=color_slot${index} VALUE="['${color.slice(1)}']"`)
    } else {
      const colorId = color ? colorIds[color] : undefined
      if (filament !== slot.filamentId) lines.push(`SAVE_VARIABLE VARIABLE=filament_slot${index} VALUE=${filament}`)
      if (vendor != null && vendor !== savedVendor) lines.push(`SAVE_VARIABLE VARIABLE=vendor_slot${index} VALUE=${vendor}`)
      if (colorId != null && color !== slot.color) lines.push(`SAVE_VARIABLE VARIABLE=color_slot${index} VALUE=${colorId}`)
    }
    if (lines.length) await attempt(() => runGcode(lines.join('\n')))
    onClose()
  }

  const boxNumber = Math.floor(slot.index / 4) + 1
  const title = slot.index === QIDI_BOX_EXTERNAL_SLOT ? t('box.external_spool') : t('box.box_slot', { box: boxNumber, slot: slot.label })
  const picked = database?.find((f) => f.id === filament)
  return (
    <Dialog
      open
      onClose={onClose}
      size="wide"
      title={title}
      footer={
        <>
          {slot.index !== QIDI_BOX_EXTERNAL_SLOT && (
            <button className="btn mr-auto" disabled={!a.rfid} title={t('box.filament_dialog.read_tag_title')} onClick={() => (attempt(() => runGcode(boxCommands.rfid(box, slot.index))), onClose())}>
              <Nfc size={15} /> {t('box.filament_dialog.read_tag')}
            </button>
          )}
          <button className="btn btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button className="btn btn-primary" disabled={busy || filament == null} onClick={save}>
            {t('common.save')}
          </button>
        </>
      }
    >
      <div className="mb-4 flex items-center gap-3">
        <Spool slot={{ label: slot.label, present: true, color, reading: false }} size={44} label={false} />
        <div className="min-w-0">
          <div className="text-[15px] font-semibold">{picked?.name ?? (database ? t('box.filament_dialog.choose') : (legacyName(legacy, filament) ?? t('box.filament_dialog.choose')))}</div>
          <div className="text-ink-3">{color ?? t('box.filament_dialog.no_color')}</div>
        </div>
      </div>
      <div className="grid gap-3.5">
        {database ? (
          <FilamentPicker database={database} value={filament} onChange={setFilament} disabled={busy} />
        ) : (
          <LegacyPicker list={legacy} filament={filament} vendor={vendor} onFilament={setFilament} onVendor={setVendor} disabled={busy} />
        )}
        <div className="grid gap-2">
          <span className="section-label">{t('box.picker.color')}</span>
          <ColorSwatches value={color} onChange={setColor} palette={database ? undefined : Object.keys(colorIds)} custom={database != null} disabled={busy} />
        </div>
      </div>
    </Dialog>
  )
}

type LegacyList = Record<string, Record<string, string>> | null

const legacyName = (list: LegacyList, id: string | null) => (id != null ? (list?.[`fila${id}`]?.filament ?? null) : null)

/** Earlier firmware: QIDI's numbered filaments and vendors from officiall_filas_list.cfg. */
function LegacyPicker({ list, filament, vendor, onFilament, onVendor, disabled }: { list: LegacyList; filament: string | null; vendor: string | null; onFilament: (id: string) => void; onVendor: (id: string) => void; disabled?: boolean }) {
  const vendors = Object.entries(list?.vendor_list ?? {})
  const filaments = Object.entries(list ?? {})
    .filter(([key, entry]) => /^fila\d+$/.test(key) && entry.filament)
    .map(([key, entry]) => ({ id: key.slice(4), name: entry.filament }))
  const name = (id: string) => filaments.find((f) => f.id === id)?.name ?? id
  return (
    <>
      <PillRow label={t('box.picker.filament')} options={filaments.map((f) => f.id)} value={filament} onChange={onFilament} show={12} disabled={disabled} render={name} />
      <PillRow label={t('box.picker.brand')} options={vendors.map(([id]) => id)} value={vendor} onChange={onVendor} show={8} disabled={disabled} render={(id) => list?.vendor_list?.[id] ?? id} />
    </>
  )
}

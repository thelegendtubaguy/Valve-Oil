// The Box card for Home, and the only place the Box is run from: every slot, each Box's air and heater, the filament
// path in one row, the selected slot's details and actions, tool mapping, Box settings, warnings, and a link to the
// filament library.
import { useEffect, useState } from 'react'
import { Box as BoxIcon, CircleAlert, Disc3, Pencil, Settings2 } from 'lucide-react'
import { BoxUnits } from '../box/BoxUnits'
import { BoxSettingsDialog } from '../box/BoxSettingsDialog'
import { HeaterDialog } from '../box/HeaterDialog'
import { SlotPanel } from '../box/SlotPanel'
import { ToolMapDialog } from '../box/ToolMapDialog'
import { FilamentRail } from '../box/FilamentRail'
import { Operation } from '../box/Operation'
import { slotByIndex, useBox, QIDI_BOX_EXTERNAL_SLOT } from '../../state/box'
import { href } from '../../lib/router'
import { t } from '../../i18n'

export function BoxDeck() {
  const box = useBox()
  const [selected, setSelected] = useState<number | null>(null)
  const [mapOpen, setMapOpen] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [heater, setHeater] = useState<number | null>(null)
  useEffect(() => {
    if (box.strandedSlot != null) setSelected(box.strandedSlot)
  }, [box.strandedSlot])
  const fallback = box.loadedSlot ?? box.slots.find((s) => s.present && s.index !== QIDI_BOX_EXTERNAL_SLOT)?.index ?? box.slots[0]?.index ?? null
  const current = slotByIndex(box, selected) ? selected : fallback
  const slot = slotByIndex(box, current)
  const onTool = (_tool: number, s: number) => (box.mappingLocked ? setSelected(s) : setMapOpen(true))
  return (
    <section className="card">
      <h3 className="card-title">
        <BoxIcon size={14} />
        QIDI Box
        <span className="aside flex items-center gap-0.5">
          <button className="iconbtn h-7 w-7" disabled={box.mappingLocked} onClick={() => setMapOpen(true)} title={box.mappingLocked ? t('home.box.tools_locked') : t('box.screen.tool_mapping')} aria-label={t('box.screen.tool_mapping')}>
            <Pencil size={14} />
          </button>
          <button className="iconbtn h-7 w-7" onClick={() => setSettingsOpen(true)} title={t('box.screen.settings')} aria-label={t('box.screen.settings')}>
            <Settings2 size={15} />
          </button>
          <a href={href('filaments')} className="iconbtn h-7 w-7" title={t('home.box.filament_library')} aria-label={t('home.box.filament_library')}>
            <Disc3 size={15} />
          </a>
        </span>
      </h3>
      {/* Where the filament is first, then the chosen slot's spool, then every slot to choose from. */}
      <div className="grid gap-3">
        {box.task && <Operation box={box} />}
        <FilamentRail box={box} onSelect={setSelected} />
        {!box.flowPaused &&
          box.warnings.map((w) => (
            <div key={w} className="tone-warn flex items-center gap-2 rounded-lg px-3 py-2">
              <CircleAlert size={15} /> <span className="text-ink">{w}</span>
            </div>
          ))}
      </div>
      {slot && (
        <div className="deck-sec">
          <SlotPanel box={box} slot={slot} />
        </div>
      )}
      <div className="deck-sec">
        <BoxUnits box={box} selected={current} onSelect={setSelected} onTool={onTool} onHeater={setHeater} />
      </div>
      <ToolMapDialog box={box} open={mapOpen} onClose={() => setMapOpen(false)} />
      <BoxSettingsDialog box={box} open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      {heater != null && <HeaterDialog box={box} boxNumber={heater} onClose={() => setHeater(null)} />}
    </section>
  )
}

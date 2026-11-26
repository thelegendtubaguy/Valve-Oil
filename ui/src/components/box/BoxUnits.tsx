// Each Box: its air and heater on the header line, then a tile per slot with the spool in its real color, the material
// and brand, and a tag mark when the Box read the spool's RFID tag. The spool says whether the slot holds filament (drawn
// or dashed) and whether it's loaded to the toolhead (a ring, and its tool badge filled); words appear only for the
// states it can't show, at the hub and reading a tag.
import { Droplet, Flame, Sun, Tag, Thermometer } from 'lucide-react'
import { Spool } from '../Spool'
import { useDryLeft } from '../../state/box-dry'
import { fmtDuration } from '../../lib/format'
import { useJobActive } from '../../state/job'
import { QIDI_BOX_EXTERNAL_SLOT, slotStatus, type BoxState, type QidiBoxSlot } from '../../state/box'
import { t, type MessageKey } from '../../i18n'

type Handlers = { selected: number | null; onSelect: (slot: number) => void; onTool: (tool: number, slot: number) => void; onHeater: (box: number) => void }

const STATUS: Partial<Record<ReturnType<typeof slotStatus>, { textKey: MessageKey; cls: string }>> = {
  hub: { textKey: 'box.units.status.hub', cls: 'text-warn' },
  reading: { textKey: 'box.units.status.reading', cls: 'text-accent-ink' },
}

const SELECTED = { boxShadow: 'inset 0 0 0 1.5px var(--accent)' }

// QIDI's Box humidity guide: below 40 %RH is normal, below 60 %RH slightly high, and above that high.
export function humidityLevel(h: number) {
  if (h < 40) return { cls: 'text-info', label: t('box.units.humidity.normal') }
  if (h < 60) return { cls: 'text-warn', label: t('box.units.humidity.slightly_high') }
  return { cls: 'text-bad', label: t('box.units.humidity.high') }
}

export function BoxUnits({ box, ...h }: { box: BoxState } & Handlers) {
  const units = Array.from({ length: box.boxCount }, (_, i) => i + 1)
  const ext = box.slots.find((s) => s.index === QIDI_BOX_EXTERNAL_SLOT)
  return (
    <div className="grid gap-3.5">
      <div className="grid gap-3.5">
        {units.map((u) => (
          <BoxUnit key={u} box={box} unit={u} {...h} />
        ))}
      </div>
      {ext && <ExternalRow box={box} slot={ext} {...h} />}
    </div>
  )
}

function BoxUnit({ box, unit, selected, onSelect, onTool, onHeater }: { box: BoxState; unit: number } & Handlers) {
  const slots = box.slots.filter((s) => s.index !== QIDI_BOX_EXTERNAL_SLOT && Math.floor(s.index / 4) === unit - 1)
  return (
    <div className="grid min-w-0 content-start gap-2">
      <ClimateRow box={box} unit={unit} onHeater={onHeater} />
      <div className="grid grid-cols-4 gap-1.5 sm:gap-2">
        {slots.map((s) => (
          <SlotTile key={s.index} box={box} slot={s} selected={selected === s.index} onSelect={onSelect} onTool={onTool} />
        ))}
      </div>
    </div>
  )
}

// The same size as the picked slot's spool above the slots (SlotPanel), so no slot's spool looks bigger than it.
export const SLOT_SPOOL = 38

function SlotTile({ box, slot, selected, onSelect, onTool }: { box: BoxState; slot: QidiBoxSlot; selected: boolean } & Omit<Handlers, 'selected' | 'onHeater'>) {
  const status = STATUS[slotStatus(box, slot)]
  const tools = box.toolsBySlot[slot.index] ?? []
  const group = box.reloadGroups[slot.index]?.letter
  const loaded = box.loadedSlot === slot.index
  // The Box turning this slot's spool while it heats shows on the spool itself, which turns, rather than on the Box's
  // line above, where it came and went every few seconds and pushed the heater button onto a line of its own.
  const turning = box.spoolRoll?.rolling === slot.label
  const title = slot.present ? [slot.label, slot.vendor, slot.name, loaded ? t('box.slot.loaded') : null].filter(Boolean).join(' · ') : t('box.units.empty_title', { slot: slot.label })
  // The slot's one control for keyboards and screen readers: supplement its visible text with the slot identity and tag state.
  const details = [title, box.fromTag[slot.index] ? t('box.units.from_tag') : null, turning ? t('box.units.turning') : null].filter(Boolean).join(' · ')
  return (
    <div className={`tile box-selectable flex! flex-col items-center gap-0.5! px-1! pt-1.5! pb-2! text-center @container ${selected ? 'bg-accent-tint!' : ''}`} style={selected ? SELECTED : undefined}>
      {/* The slot and its tag mark at the left of the top line, its tools and auto-reload group at the right. Where the
          tile is wide enough, 100 px (92 inside its padding) for a two-digit tool beside the spool, they sit in the
          top corners, the tag mark under the slot and the group under the tools, and the spool comes up between. */}
      <div className="pointer-events-none flex w-full min-w-0 flex-wrap items-center gap-0.5 pl-1 *:pointer-events-auto @min-[92px]:absolute @min-[92px]:inset-x-0 @min-[92px]:top-1.5 @min-[92px]:z-1 @min-[92px]:flex-nowrap @min-[92px]:items-start @min-[92px]:px-1.5">
        {/* A second tap target for the same slot; the button below is the one keyboards and screen readers use. */}
        <button type="button" tabIndex={-1} aria-hidden="true" title={title} onClick={() => onSelect(slot.index)} className="flex min-h-6 items-center gap-0.5 border-0 bg-transparent p-0 text-inherit @min-[92px]:flex-col @min-[92px]:items-start @min-[92px]:pt-1">
          <span className="text-[11px] font-bold text-ink-3">{slot.label}</span>
          {box.fromTag[slot.index] && <Tag size={11} className="ml-0.5 text-ink-3 @min-[92px]:ml-0" aria-label={t('box.units.from_tag')} />}
        </button>
        <span className="flex-1" />
        <div className="flex flex-wrap items-center justify-end gap-0.5 @min-[92px]:flex-col @min-[92px]:items-end">
          {tools.map((tool) => (
            <button key={tool} type="button" className={`min-h-6 min-w-6 rounded-[5px] border-0 px-1.5 py-px text-[10.5px] font-semibold @min-[92px]:px-1 ${loaded ? 'bg-accent text-on-accent' : 'tone-muted'}`} title={`${tool} → ${slot.label}`} aria-label={`${tool} → ${slot.label}`} onClick={() => onTool(Number(tool.slice(1)), slot.index)}>
              {tool}
            </button>
          ))}
          {group && (
            <span className="tone-warn rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold" title={t('box.units.reload_group', { group })}>
              {group}
            </span>
          )}
        </div>
      </div>
      <button type="button" aria-pressed={selected} title={title} onClick={() => onSelect(slot.index)} className="flex w-full min-w-0 flex-1 flex-col items-center gap-0.5 border-0 bg-transparent p-0 text-center text-inherit">
        <span className={`flex ${turning ? 'spin-slow' : ''}`} title={turning ? t('box.units.turning') : undefined}>
          <Spool slot={slot} size={SLOT_SPOOL} label={false} loaded={loaded} />
        </span>
        {slot.present && (
          <>
            <span className="w-full truncate px-0.5 text-[14px] leading-tight font-bold">{slot.type ?? t('box.units.unknown_type')}</span>
            <span className="w-full truncate px-0.5 text-[11.5px] leading-tight text-ink-2">{slot.vendor ?? '—'}</span>
          </>
        )}
        {status && <span className={`mt-0.5 text-[10px] font-bold tracking-[0.07em] uppercase ${status.cls}`}>{t(status.textKey)}</span>}
        <span className="sr-only">{details}</span>
      </button>
    </div>
  )
}

/** A Box's header line: its name, temperature, and humidity, and its heater as a button that shows what it's doing. */
function ClimateRow({ box, unit, onHeater }: { box: BoxState; unit: number; onHeater: (box: number) => void }) {
  const c = box.climates.find((x) => x.box === unit)
  const timer = useDryLeft(c)
  const inPrint = useJobActive()
  const heaterText = !c
    ? ''
    : c.drying
      ? `${t('box.units.drying_temp', { temp: c.target })}${timer && timer.left > 0 ? ` · ${fmtDuration(timer.left)}` : ''}`
      : c.target
        ? t('box.units.heating_temp', { temp: c.target })
        : box.canDry && !inPrint
          ? t('box.units.dry_or_heat')
          : t('box.heater.heat')
  return (
    <div className="flex min-h-[30px] flex-wrap items-center gap-x-3 gap-y-1">
      <span className="section-label">{t('box.box_n', { n: unit })}</span>
      {c?.temperature != null && (
        <span className="num inline-flex items-center gap-1 text-[12.5px] font-semibold">
          <Thermometer size={13} className="text-ink-3" /> {Math.round(c.temperature)} °C
        </span>
      )}
      {c?.humidity != null && (
        <span className="num inline-flex items-center gap-1 text-[12.5px] font-semibold" title={humidityLevel(c.humidity).label}>
          <Droplet size={13} className={humidityLevel(c.humidity).cls} /> {c.humidity}%
        </span>
      )}
      <span className="flex-1" />
      {c && (c.heater ? (
        <button className={`btn btn-sm shrink-0 ${c.target ? 'tool-on' : ''}`} onClick={() => onHeater(unit)} title={c.drying ? t('box.units.heater_drying_title') : c.target ? t('box.units.heater_heating_title') : t('box.units.heater_off_title')}>
          {c.target ? <Flame size={14} /> : <Sun size={14} />} {heaterText}
        </button>
      ) : c.target ? (
        <span className="inline-flex items-center gap-1.5 text-[12.5px] font-semibold text-accent-ink">
          <Flame size={13} /> {heaterText}
        </span>
      ) : null)}
    </div>
  )
}

function ExternalRow({ box, slot, selected, onSelect, onTool }: { box: BoxState; slot: QidiBoxSlot } & Handlers) {
  const tools = box.toolsBySlot[slot.index] ?? []
  const loaded = box.loadedSlot === slot.index
  const on = selected === slot.index
  return (
    <div className={`tile box-selectable grid-cols-[minmax(0,1fr)_auto] items-center gap-3! px-3! py-2! ${on ? 'bg-accent-tint!' : ''}`} style={on ? SELECTED : undefined}>
      <button type="button" aria-pressed={on} onClick={() => onSelect(slot.index)} className="flex min-w-0 items-center gap-3 border-0 bg-transparent p-0 text-left text-inherit">
        <Spool slot={slot} size={30} label={false} loaded={loaded && !on} />
        <span className="min-w-0">
          <span className="section-label block">{t('box.external_spool')}</span>
          <span className="block truncate font-semibold">{slot.name || slot.type ? [slot.vendor, slot.name ?? slot.type].filter(Boolean).join(' ') : <span className="text-ink-3">{t('box.units.not_set')}</span>}</span>
        </span>
      </button>
      <div className="flex items-center gap-1.5">
        {tools.map((tool) => (
          <button key={tool} type="button" className="tone-muted min-h-6 min-w-6 rounded-[5px] border-0 px-1.5 py-px text-[10.5px] font-semibold" aria-label={`${tool} → ${slot.label}`} onClick={() => onTool(Number(tool.slice(1)), slot.index)}>
            {tool}
          </button>
        ))}
        {loaded && <span className="text-[10px] font-bold tracking-[0.07em] text-accent-ink uppercase">{t('box.units.status.loaded')}</span>}
      </div>
    </div>
  )
}

// Each Box: its air and heater on the header line, then a tile per slot with the spool in its real color, the material
// and brand, and a tag mark when the Box read the spool's RFID tag. The spool says whether the slot holds filament (drawn
// or dashed) and whether it's loaded to the toolhead (a ring, and its tool badge filled); words appear only for the
// states it can't show, at the hub and reading a tag.
import type { KeyboardEvent } from 'react'
import { Droplet, Flame, RotateCw, Sun, Tag, Thermometer } from 'lucide-react'
import { Spool } from '../Spool'
import { useDryLeft } from '../../state/box-dry'
import { fmtDuration } from '../../lib/format'
import { QIDI_BOX_EXTERNAL_SLOT, slotStatus, type BoxState, type QidiBoxSlot } from '../../state/box'
import { t, type MessageKey } from '../../i18n'

type Handlers = { selected: number | null; onSelect: (slot: number) => void; onTool?: (tool: number, slot: number) => void; onHeater?: (box: number) => void }

const STATUS: Partial<Record<ReturnType<typeof slotStatus>, { textKey: MessageKey; cls: string }>> = {
  hub: { textKey: 'box.units.status.hub', cls: 'text-warn' },
  reading: { textKey: 'box.units.status.reading', cls: 'text-accent-ink' },
}

// QIDI's Box humidity guide: below 40 %RH is normal, below 60 %RH slightly high, and above that high.
export function humidityLevel(h: number) {
  if (h < 40) return { cls: 'text-info', label: t('box.units.humidity.normal') }
  if (h < 60) return { cls: 'text-warn', label: t('box.units.humidity.slightly_high') }
  return { cls: 'text-bad', label: t('box.units.humidity.high') }
}

export function BoxUnits({ box, stack, ...h }: { box: BoxState; stack?: boolean } & Handlers) {
  const units = Array.from({ length: box.boxCount }, (_, i) => i + 1)
  const ext = box.slots.find((s) => s.index === QIDI_BOX_EXTERNAL_SLOT)
  return (
    <div className="grid gap-3.5">
      <div className={`grid gap-3.5 ${units.length > 1 && !stack ? 'lg:grid-cols-2' : ''}`}>
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

const activate = (fn: () => void) => (e: KeyboardEvent) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), fn())

function SlotTile({ box, slot, selected, onSelect, onTool }: { box: BoxState; slot: QidiBoxSlot; selected: boolean; onSelect: (s: number) => void; onTool?: (tool: number, slot: number) => void }) {
  const status = STATUS[slotStatus(box, slot)]
  const tools = box.toolsBySlot[slot.index] ?? []
  const group = box.reloadGroups[slot.index]?.letter
  const loaded = box.loadedSlot === slot.index
  const title = slot.present ? [slot.label, slot.vendor, slot.name, loaded ? t('box.slot.loaded') : null].filter(Boolean).join(' · ') : t('box.units.empty_title', { slot: slot.label })
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      title={title}
      onClick={() => onSelect(slot.index)}
      onKeyDown={activate(() => onSelect(slot.index))}
      className={`tile flex! cursor-pointer flex-col items-center gap-0.5! px-1! pt-1.5! pb-2! text-center outline-none hover:bg-hover ${selected ? 'bg-accent-tint!' : ''}`}
      style={selected ? { boxShadow: 'inset 0 0 0 1.5px var(--accent)' } : undefined}
    >
      {/* The slot, its tag mark, then its tools and auto-reload group, all on the top line. */}
      <div className="flex w-full items-center gap-0.5 pl-1">
        <span className="text-[11px] font-bold text-ink-3">{slot.label}</span>
        {box.fromTag[slot.index] && <Tag size={11} className="ml-0.5 text-ink-3" aria-label={t('box.units.from_tag')} />}
        <span className="flex-1" />
        {tools.map((tool) => (
          <button
            key={tool}
            type="button"
            className={`rounded-[5px] border-0 px-1.5 py-px text-[10.5px] font-semibold ${loaded ? 'bg-accent text-on-accent' : 'tone-muted'}`}
            title={`${tool} → ${slot.label}`}
            onClick={(e) => (e.stopPropagation(), onTool?.(Number(tool.slice(1)), slot.index))}
            onKeyDown={(e) => e.stopPropagation()}
          >
            {tool}
          </button>
        ))}
        {group && (
          <span className="tone-warn rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold" title={t('box.units.reload_group', { group })}>
            {group}
          </span>
        )}
      </div>
      <Spool slot={slot} size={50} label={false} loaded={loaded} />
      {slot.present && (
        <>
          <div className="w-full truncate px-0.5 text-[14px] leading-tight font-bold">{slot.type ?? t('box.units.unknown_type')}</div>
          <div className="w-full truncate px-0.5 text-[11.5px] leading-tight text-ink-2">{slot.vendor ?? '—'}</div>
        </>
      )}
      {status && <div className={`mt-0.5 text-[10px] font-bold tracking-[0.07em] uppercase ${status.cls}`}>{t(status.textKey)}</div>}
    </div>
  )
}

/** A Box's header line: its name, temperature, and humidity, and its heater as a button that shows what it's doing. */
export function ClimateRow({ box, unit, onHeater }: { box: BoxState; unit: number; onHeater?: (box: number) => void }) {
  const c = box.climates.find((x) => x.box === unit)
  const timer = useDryLeft(c)
  const rolling = box.spoolRoll?.rolling
  const rollingHere = rolling != null && rolling.startsWith(String(unit))
  const inPrint = box.printState === 'printing' || box.printState === 'paused'
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
      {rollingHere && (
        <span className="inline-flex items-center gap-1 text-[12.5px] text-ink-2" title={t('box.settings.vo_box_spool_roll')}>
          <RotateCw size={12} className="spin" /> {rolling}
        </span>
      )}
      <span className="flex-1" />
      {c && (onHeater && c.heater ? (
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
    <div
      role="button"
      tabIndex={0}
      aria-pressed={on}
      onClick={() => onSelect(slot.index)}
      onKeyDown={activate(() => onSelect(slot.index))}
      className={`tile cursor-pointer grid-cols-[auto_1fr_auto] items-center gap-3! px-3! py-2! outline-none hover:bg-hover ${on ? 'bg-accent-tint!' : ''}`}
      style={on ? { boxShadow: 'inset 0 0 0 1.5px var(--accent)' } : undefined}
    >
      <Spool slot={slot} size={30} label={false} loaded={loaded && !on} />
      <div className="min-w-0">
        <div className="section-label">{t('box.external_spool')}</div>
        <div className="truncate font-semibold">{slot.name || slot.type ? [slot.vendor, slot.name ?? slot.type].filter(Boolean).join(' ') : <span className="text-ink-3">{t('box.units.not_set')}</span>}</div>
      </div>
      <div className="flex items-center gap-1.5">
        {tools.map((tool) => (
          <button key={tool} type="button" className="tone-muted rounded-[5px] border-0 px-1.5 py-px text-[10.5px] font-semibold" onClick={(e) => (e.stopPropagation(), onTool?.(Number(tool.slice(1)), slot.index))}>
            {tool}
          </button>
        ))}
        {loaded && <span className="text-[10px] font-bold tracking-[0.07em] text-accent-ink uppercase">{t('box.units.status.loaded')}</span>}
      </div>
    </div>
  )
}

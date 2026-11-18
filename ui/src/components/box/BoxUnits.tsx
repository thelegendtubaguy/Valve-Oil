// Each Box as the touchscreen draws it: a tile per slot with the spool in its real color, the material and brand,
// LOADED, READY, or EMPTY, and a tag mark when the Box read the spool's RFID tag; the Box's air underneath.
import type { KeyboardEvent } from 'react'
import { Droplet, Flame, RotateCw, Sun, Tag, Thermometer } from 'lucide-react'
import { Spool } from '../Spool'
import { QIDI_BOX_EXTERNAL_SLOT, slotStatus, type BoxState, type QidiBoxSlot } from '../../state/box'

type Handlers = { selected: number | null; onSelect: (slot: number) => void; onTool?: (tool: number, slot: number) => void; onHeater?: (box: number) => void }

const STATUS: Record<ReturnType<typeof slotStatus>, { text: string; cls: string }> = {
  loaded: { text: 'Loaded', cls: 'text-accent-ink' },
  hub: { text: 'At hub', cls: 'text-warn' },
  ready: { text: 'Ready', cls: 'text-ink-3' },
  empty: { text: 'Empty', cls: 'text-ink-3' },
  reading: { text: 'Reading tag', cls: 'text-accent-ink' },
}

// QIDI's Box humidity guide: below 40 %RH is normal, below 60 %RH slightly high, and above that high.
export function humidityLevel(h: number) {
  if (h < 40) return { cls: 'text-info', label: 'Normal humidity' }
  if (h < 60) return { cls: 'text-warn', label: 'Slightly high humidity' }
  return { cls: 'text-bad', label: 'High humidity' }
}

export function BoxUnits({ box, ...h }: { box: BoxState } & Handlers) {
  const units = Array.from({ length: box.boxCount }, (_, i) => i + 1)
  const ext = box.slots.find((s) => s.index === QIDI_BOX_EXTERNAL_SLOT)
  return (
    <div className="grid gap-3.5">
      <div className={`grid gap-3.5 ${units.length > 1 ? 'lg:grid-cols-2' : ''}`}>
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
      <div className="section-label">Box {unit}</div>
      <div className="grid grid-cols-4 gap-1.5 sm:gap-2">
        {slots.map((s) => (
          <SlotTile key={s.index} box={box} slot={s} selected={selected === s.index} onSelect={onSelect} onTool={onTool} />
        ))}
      </div>
      <ClimateRow box={box} unit={unit} onHeater={onHeater} />
    </div>
  )
}

const activate = (fn: () => void) => (e: KeyboardEvent) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), fn())

function SlotTile({ box, slot, selected, onSelect, onTool }: { box: BoxState; slot: QidiBoxSlot; selected: boolean; onSelect: (s: number) => void; onTool?: (tool: number, slot: number) => void }) {
  const status = STATUS[slotStatus(box, slot)]
  const tools = box.toolsBySlot[slot.index] ?? []
  const group = box.reloadGroups[slot.index]?.letter
  const loaded = box.loadedSlot === slot.index
  const title = slot.present ? [slot.label, slot.vendor, slot.name].filter(Boolean).join(' · ') : `${slot.label} · Empty`
  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      title={title}
      onClick={() => onSelect(slot.index)}
      onKeyDown={activate(() => onSelect(slot.index))}
      className={`tile cursor-pointer justify-items-center gap-0.5! px-1! pt-2! pb-2.5! text-center outline-none hover:bg-hover ${selected ? 'bg-accent-tint!' : ''}`}
      style={selected ? { boxShadow: 'inset 0 0 0 1.5px var(--accent)' } : loaded ? { boxShadow: 'inset 0 0 0 1px color-mix(in srgb, var(--accent) 55%, transparent)' } : undefined}
    >
      <div className="flex w-full items-center gap-1 px-1">
        <span className="text-[11px] font-bold text-ink-3">{slot.label}</span>
        <span className="flex-1" />
        {box.fromTag[slot.index] && <Tag size={12} className="text-ink-3" aria-label="Read from the spool's RFID tag" />}
      </div>
      <Spool slot={slot} size={50} label={false} loaded={loaded && !selected} />
      <div className="w-full truncate px-0.5 text-[14px] leading-tight font-bold">{slot.present ? (slot.type ?? 'Unknown') : ''}</div>
      <div className="w-full truncate px-0.5 text-[11.5px] leading-tight text-ink-2">{slot.present ? (slot.vendor ?? '—') : 'Tap to set'}</div>
      <div className={`mt-0.5 text-[10px] font-bold tracking-[0.07em] uppercase ${status.cls}`}>{status.text}</div>
      {(tools.length > 0 || group) && (
        <div className="mt-1 flex flex-wrap justify-center gap-0.5">
          {tools.map((t) => (
            <button
              key={t}
              type="button"
              className={`rounded-[5px] border-0 px-1.5 py-px text-[10.5px] font-semibold ${loaded ? 'bg-accent text-on-accent' : 'tone-muted'}`}
              title={`${t} → ${slot.label}`}
              onClick={(e) => (e.stopPropagation(), onTool?.(Number(t.slice(1)), slot.index))}
              onKeyDown={(e) => e.stopPropagation()}
            >
              {t}
            </button>
          ))}
          {group && (
            <span className="tone-warn rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold" title={`Auto-reload group ${group}`}>
              {group}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

/** The Box's temperature, humidity, and dryer, with Dry to set its heater. */
export function ClimateRow({ box, unit, onHeater }: { box: BoxState; unit: number; onHeater?: (box: number) => void }) {
  const c = box.climates.find((x) => x.box === unit)
  if (!c) return null
  const rolling = box.spoolRoll?.rolling
  const rollingHere = rolling != null && rolling.startsWith(String(unit))
  return (
    <div className="flex flex-wrap items-center gap-x-3.5 gap-y-1 rounded-xl bg-inset px-3 py-1.5 shadow-[inset_0_0_0_1px_var(--line)]">
      {c.temperature != null && (
        <span className="num inline-flex items-center gap-1.5 font-semibold">
          <Thermometer size={14} className="text-ink-3" /> {Math.round(c.temperature)} °C
        </span>
      )}
      {c.humidity != null && (
        <span className="num inline-flex items-center gap-1.5 font-semibold" title={humidityLevel(c.humidity).label}>
          <Droplet size={14} className={humidityLevel(c.humidity).cls} /> {c.humidity}% <span className="font-normal text-ink-3">RH</span>
        </span>
      )}
      <span className={`inline-flex items-center gap-1.5 ${c.target ? 'font-semibold text-accent-ink' : 'text-ink-3'}`}>
        <Flame size={14} /> {c.target ? `Drying at ${c.target} °C` : <span><span className="hidden sm:inline">Dryer </span>off</span>}
      </span>
      {rollingHere && (
        <span className="inline-flex items-center gap-1.5 text-ink-2" title="Turn spools while heating">
          <RotateCw size={13} className="spin" /> Turning {rolling}
        </span>
      )}
      <span className="flex-1" />
      {onHeater && c.heater && (
        <button className="btn btn-sm -my-0.5" onClick={() => onHeater(unit)}>
          <Sun size={14} /> Dry
        </button>
      )}
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
        <div className="section-label">External spool</div>
        <div className="truncate font-semibold">{slot.name || slot.type ? [slot.vendor, slot.name ?? slot.type].filter(Boolean).join(' ') : <span className="text-ink-3">Not set</span>}</div>
      </div>
      <div className="flex items-center gap-1.5">
        {tools.map((t) => (
          <button key={t} type="button" className="tone-muted rounded-[5px] border-0 px-1.5 py-px text-[10.5px] font-semibold" onClick={(e) => (e.stopPropagation(), onTool?.(Number(t.slice(1)), slot.index))}>
            {t}
          </button>
        ))}
        {loaded && <span className="text-[10px] font-bold tracking-[0.07em] text-accent-ink uppercase">Loaded</span>}
      </div>
    </div>
  )
}

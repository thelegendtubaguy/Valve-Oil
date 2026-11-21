// Each Box as the touchscreen draws it: a tile per slot with the spool in its real color, the material and brand,
// LOADED, READY, or EMPTY, and a tag mark when the Box read the spool's RFID tag; the Box's air underneath.
import type { KeyboardEvent } from 'react'
import { Droplet, Flame, RotateCw, Sun, Tag, Thermometer } from 'lucide-react'
import { Spool } from '../Spool'
import { QIDI_BOX_EXTERNAL_SLOT, slotStatus, type BoxState, type QidiBoxSlot } from '../../state/box'
import { t, type MessageKey } from '../../i18n'
import { tr } from '../../i18n/rich'

type Handlers = { selected: number | null; onSelect: (slot: number) => void; onTool?: (tool: number, slot: number) => void; onHeater?: (box: number) => void }

const STATUS: Record<ReturnType<typeof slotStatus>, { msg: MessageKey; cls: string }> = {
  loaded: { msg: 'box.units.status.loaded', cls: 'text-accent-ink' },
  hub: { msg: 'box.units.status.hub', cls: 'text-warn' },
  ready: { msg: 'box.units.status.ready', cls: 'text-ink-3' },
  empty: { msg: 'box.units.status.empty', cls: 'text-ink-3' },
  reading: { msg: 'box.units.status.reading', cls: 'text-accent-ink' },
}

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
      <div className="section-label">{t('box.box_n', { n: unit })}</div>
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
  const title = slot.present ? [slot.label, slot.vendor, slot.name].filter(Boolean).join(' · ') : t('box.units.empty_title', { slot: slot.label })
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
        {box.fromTag[slot.index] && <Tag size={12} className="text-ink-3" aria-label={t('box.units.from_tag')} />}
      </div>
      <Spool slot={slot} size={50} label={false} loaded={loaded && !selected} />
      <div className="w-full truncate px-0.5 text-[14px] leading-tight font-bold">{slot.present ? (slot.type ?? t('box.units.unknown_type')) : ''}</div>
      <div className="w-full truncate px-0.5 text-[11.5px] leading-tight text-ink-2">{slot.present ? (slot.vendor ?? '—') : t('box.units.tap_to_set')}</div>
      <div className={`mt-0.5 text-[10px] font-bold tracking-[0.07em] uppercase ${status.cls}`}>{t(status.msg)}</div>
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
            <span className="tone-warn rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold" title={t('box.units.reload_group', { group })}>
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
          <Droplet size={14} className={humidityLevel(c.humidity).cls} /> {tr('box.units.humidity_rh', { h: c.humidity }, { rh: (x) => <span className="font-normal text-ink-3">{x}</span> })}
        </span>
      )}
      <span className={`inline-flex items-center gap-1.5 ${c.target ? 'font-semibold text-accent-ink' : 'text-ink-3'}`}>
        <Flame size={14} /> {c.target ? t('box.units.drying', { temp: c.target }) : <span>{tr('box.units.dryer_off', {}, { wide: (x) => <span className="hidden sm:inline">{x}</span> })}</span>}
      </span>
      {rollingHere && (
        <span className="inline-flex items-center gap-1.5 text-ink-2" title={t('box.settings.vo_box_spool_roll')}>
          <RotateCw size={13} className="spin" /> {t('box.units.turning', { slot: rolling })}
        </span>
      )}
      <span className="flex-1" />
      {onHeater && c.heater && (
        <button className="btn btn-sm -my-0.5" onClick={() => onHeater(unit)}>
          <Sun size={14} /> {t('box.units.dry')}
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
        <div className="section-label">{t('box.external_spool')}</div>
        <div className="truncate font-semibold">{slot.name || slot.type ? [slot.vendor, slot.name ?? slot.type].filter(Boolean).join(' ') : <span className="text-ink-3">{t('box.units.not_set')}</span>}</div>
      </div>
      <div className="flex items-center gap-1.5">
        {tools.map((t) => (
          <button key={t} type="button" className="tone-muted rounded-[5px] border-0 px-1.5 py-px text-[10.5px] font-semibold" onClick={(e) => (e.stopPropagation(), onTool?.(Number(t.slice(1)), slot.index))}>
            {t}
          </button>
        ))}
        {loaded && <span className="text-[10px] font-bold tracking-[0.07em] text-accent-ink uppercase">{t('box.units.status.loaded')}</span>}
      </div>
    </div>
  )
}

import { Droplet, Flame } from 'lucide-react'
import { QIDI_BOX_EXTERNAL_SLOT, type BoxState } from '../state/box'
import { Spool } from './Spool'
import { t } from '../i18n'

/** Compact Box view: each Box's slots in a row with tools, inlet sensor, and filament. */
export function BoxStrip({ box, selected, onSelect, size = 46, showExternal = true, compact = false, onClimate }: { box: BoxState; selected?: number | null; onSelect?: (slot: number) => void; size?: number; showExternal?: boolean; compact?: boolean; onClimate?: (box: number) => void }) {
  const units = [...Array.from({ length: box.boxCount }, (_, i) => i + 1), ...(showExternal && !compact ? [0] : [])]
  const ext = box.slots.find((s) => s.index === QIDI_BOX_EXTERNAL_SLOT)
  return (
    <div className="grid gap-3">
      {units.map((u) => {
        const slots = box.slots.filter((s) => (u === 0 ? s.index === QIDI_BOX_EXTERNAL_SLOT : s.index !== QIDI_BOX_EXTERNAL_SLOT && Math.floor(s.index / 4) === u - 1))
        const c = box.climates.find((x) => x.box === u)
        return (
          <div key={u}>
            <div className="mb-1.5 flex flex-wrap items-center gap-2">
              <b className="text-[12.5px]">{u === 0 ? t('box.external_spool') : t('box.box_n', { n: u })}</b>
              {c?.temperature != null &&
                (onClimate && c.heater ? (
                  <button type="button" className="chip num cursor-pointer border-0 hover:bg-hover" title={t('box.strip.set_heater', { n: u })} onClick={() => onClimate(u)}>
                    <Flame size={12} className={c.target ? 'text-warn' : ''} />
                    {Math.round(c.temperature)} °C{c.target ? ` → ${c.target}` : ''}
                  </button>
                ) : (
                  <span className="chip num">
                    <Flame size={12} className={c.target ? 'text-warn' : ''} />
                    {Math.round(c.temperature)} °C{c.target ? ` → ${c.target}` : ''}
                  </span>
                ))}
              {c?.humidity != null && (
                <span className={`chip num ${c.humidity >= 60 ? 'text-bad' : c.humidity >= 40 ? 'text-warn' : ''}`}>
                  <Droplet size={12} />
                  {t('box.humidity_rh', { h: c.humidity })}
                </span>
              )}
            </div>
            <div className={compact ? 'grid grid-cols-4 gap-1' : 'flex flex-wrap gap-1'}>
              {slots.map((s) => {
                const tools = Object.entries(box.toolMap).filter(([, slot]) => slot === s.index).map(([t]) => `T${t}`)
                const group = box.reloadGroups[s.index]?.letter
                const loaded = box.loadedSlot === s.index
                const Tag = onSelect ? 'button' : 'div'
                return (
                  <Tag
                    key={s.index}
                    type={onSelect ? 'button' : undefined}
                    onClick={onSelect ? () => onSelect(s.index) : undefined}
                    className={`grid ${compact ? 'min-w-0' : 'min-w-[68px]'} justify-items-center gap-1 rounded-[10px] border-0 bg-transparent px-1 pt-1.5 pb-2 text-inherit ${onSelect ? 'hover:bg-hover' : ''} ${selected === s.index ? 'bg-accent-tint! shadow-[inset_0_0_0_1px_color-mix(in_srgb,var(--accent)_50%,transparent)]' : ''}`}
                  >
                    <div className="flex min-h-[18px] gap-0.5">
                      {tools.map((t) => (
                        <span key={t} className={`rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold ${loaded ? 'bg-accent text-white' : 'tone-muted'}`}>
                          {t}
                        </span>
                      ))}
                      {group && <span className="tone-muted rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold text-warn!" title={t('box.strip.reload_group', { group })}>{group}</span>}
                    </div>
                    <Spool slot={s} size={size} loaded={loaded} />
                    {s.index !== QIDI_BOX_EXTERNAL_SLOT && <span className={`h-1.5 w-1.5 rounded-full ${s.present ? 'bg-good' : 'bg-ink-3 opacity-50'}`} />}
                    <span className={`${compact ? 'w-full truncate' : 'max-w-[76px]'} text-center text-[11.5px] leading-tight text-ink-2`} title={[s.vendor, s.name].filter(Boolean).join(' ')}>
                      {s.present ? (s.name ?? s.type ?? '—') : s.index === QIDI_BOX_EXTERNAL_SLOT ? t('box.strip.not_loaded') : t('box.empty')}
                      {!compact && (
                        <>
                          <br />
                          <span className="text-ink-3">{s.present ? (s.vendor ?? '\u00a0') : '\u00a0'}</span>
                        </>
                      )}
                    </span>
                  </Tag>
                )
              })}
            </div>
          </div>
        )
      })}
      {compact && showExternal && ext && (
        <button type="button" onClick={onSelect ? () => onSelect(ext.index) : undefined} disabled={!onSelect} className="flex items-center gap-2.5 rounded-[10px] border-0 bg-transparent px-1 py-1 text-left text-inherit enabled:hover:bg-hover disabled:cursor-default">
          <Spool slot={ext} size={26} label={false} loaded={box.loadedSlot === ext.index} />
          <span className="min-w-0 flex-1 truncate">
            <b className="text-[12.5px]">{t('box.external_spool')}</b>
            <span className="text-ink-3"> · {ext.present && (ext.name || ext.type) ? [ext.vendor, ext.name ?? ext.type].filter(Boolean).join(' ') : t('box.strip.not_set')}</span>
          </span>
          {Object.entries(box.toolMap)
            .filter(([, slot]) => slot === ext.index)
            .map(([t]) => (
              <span key={t} className="tone-muted rounded-[5px] px-1.5 py-px text-[10.5px] font-semibold">
                T{t}
              </span>
            ))}
        </button>
      )}
    </div>
  )
}

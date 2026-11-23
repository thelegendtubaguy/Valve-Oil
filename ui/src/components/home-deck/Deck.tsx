// The Home control deck: the jog wheel, step sizes, temperature actions, outputs, and macros for the Home mockup.
import { useState, type ReactNode } from 'react'
import { ChevronDown, Fan, Footprints, House, Rabbit, Settings2, Snowflake, Turtle, Zap } from 'lucide-react'
import { MenuButton, Segmented, confirm } from '../ui'
import { objectLabel, send } from '../control/common'
import { useJobActive } from '../../state/job'
import { FAN_TYPES, FanRow, LED_TYPES, LedRow, PIN_TYPES, PinRow, QIDI_FAN_ORDER, nameOf, typeOf, useOutputs } from '../control/Outputs'
import { MacroButton, MacrosActions, useMacros, useMacrosDb } from '../control/Macros'
import { macroGroups } from '../control/macro-settings'
import { attempt } from '../toast'
import { usePrinter } from '../../state/printer'
import { applyPreset, usePresets } from '../../state/presets'
import { navigate } from '../../lib/router'
import { t, type MessageKey } from '../../i18n'
import { tr } from '../../i18n/rich'
import { FoldTitle, useFold } from './fold'
import './deck.css'

export type Mode = 'fine' | 'coarse' | 'turbo'
/** Jog step sizes. `labelKey` is a message key, looked up when it's shown. */
export const MODES: Record<Mode, { small: number; big: number; labelKey: MessageKey; icon: ReactNode }> = {
  fine: { small: 0.1, big: 1, labelKey: 'home.deck.fine', icon: <Turtle size={14} /> },
  coarse: {
    small: 1,
    big: 10,
    labelKey: 'home.deck.coarse',
    icon: <Footprints size={14} />,
  },
  turbo: { small: 10, big: 50, labelKey: 'home.deck.turbo', icon: <Rabbit size={14} /> },
}

export function useMode() {
  const [mode, setMode] = useState<Mode>(() => (localStorage.getItem('vo-jog-mode') as Mode) || 'coarse')
  const pick = (m: Mode) => {
    setMode(m)
    try {
      localStorage.setItem('vo-jog-mode', m)
    } catch {}
  }
  return [mode, pick] as const
}

// ---- jog wheel ----
const S = 280
const C = S / 2
const R0 = 38
const R1 = 86
const R2 = 136
const pt = (r: number, deg: number) => [C + r * Math.cos((deg * Math.PI) / 180), C + r * Math.sin((deg * Math.PI) / 180)]
function wedge(r0: number, r1: number, a0: number, a1: number) {
  const [x1, y1] = pt(r1, a0)
  const [x2, y2] = pt(r1, a1)
  const [x3, y3] = pt(r0, a1)
  const [x4, y4] = pt(r0, a0)
  return `M${x1} ${y1}A${r1} ${r1} 0 0 1 ${x2} ${y2}L${x3} ${y3}A${r0} ${r0} 0 0 0 ${x4} ${y4}Z`
}

const DIRS: { axis: 'X' | 'Y'; dir: 1 | -1; label: string; mid: number }[] = [
  { axis: 'Y', dir: 1, label: 'Y+', mid: -90 },
  { axis: 'X', dir: 1, label: 'X+', mid: 0 },
  { axis: 'Y', dir: -1, label: 'Y−', mid: 90 },
  { axis: 'X', dir: -1, label: 'X−', mid: 180 },
]

/** Four directions, each split into a small step (inner ring) and a big step (outer ring), with Home X and Y in the middle. */
export function JogWheel({ small, big, canX, canY, canHome, onJog, onHome, hotX, hotY }: { small: number; big: number; canX: boolean; canY: boolean; canHome: boolean; onJog: (axis: 'X' | 'Y', d: number) => void; onHome: () => void; hotX?: boolean; hotY?: boolean }) {
  const seg = (d: (typeof DIRS)[number], ring: 'inner' | 'outer') => {
    const step = ring === 'inner' ? small : big
    const ok = d.axis === 'X' ? canX : canY
    const hot = ok && (d.axis === 'X' ? hotX : hotY)
    const [r0, r1] = ring === 'inner' ? [R0, R1] : [R1, R2]
    const [lx, ly] = pt((r0 + r1) / 2, d.mid)
    const act = () => ok && onJog(d.axis, d.dir * step)
    return (
      <g
        key={d.label + ring}
        className={`jw-seg ${ring} ${ok ? '' : 'off'} ${hot ? 'hot' : ''}`}
        role="button"
        tabIndex={ok ? 0 : -1}
        aria-label={`${d.label} ${step} mm`}
        aria-disabled={!ok}
        onClick={act}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), act())}
      >
        <title>{`${d.label} ${step} mm`}</title>
        <path d={wedge(r0, r1, d.mid - 45, d.mid + 45)} />
        {ring === 'outer' ? (
          <>
            <text className="ax" x={lx} y={ly - 2} textAnchor="middle">
              {d.label}
            </text>
            <text x={lx} y={ly + 13} textAnchor="middle">
              {big}
            </text>
          </>
        ) : (
          <text x={lx} y={ly + 4} textAnchor="middle">
            {small}
          </text>
        )}
      </g>
    )
  }
  return (
    <svg className="jw" viewBox={`0 0 ${S} ${S}`} role="group" aria-label={t('control.toolhead.move_xy')}>
      {DIRS.map((d) => seg(d, 'outer'))}
      {DIRS.map((d) => seg(d, 'inner'))}
      <g className={`jw-home ${canHome ? '' : 'off'}`} role="button" tabIndex={canHome ? 0 : -1} aria-label={t('control.toolhead.home_xy')} onClick={() => canHome && onHome()} onKeyDown={(e) => e.key === 'Enter' && canHome && onHome()}>
        <title>{t('control.toolhead.home_xy')}</title>
        <circle cx={C} cy={C} r={R0 - 5} />
        <House x={C - 11} y={C - 14} width={22} height={22} />
        <text x={C} y={C + 20} textAnchor="middle" fontSize={9} fontWeight={700} fill="currentColor">
          {/* i18n-ignore: axis names */}
          XY
        </text>
      </g>
    </svg>
  )
}

export function TempActions() {
  const [presets] = usePresets()
  const active = useJobActive()
  return (
    <>
      <MenuButton
        className="btn btn-sm px-2! py-0.5! text-[11.5px]"
        label={t('control.temps.presets')}
        button={
          <>
            {t('control.temps.presets')} <ChevronDown size={12} />
          </>
        }
        items={
          presets.length
            ? presets.map((p) => ({
                label: p.name,
                onSelect: () => void attempt(() => applyPreset(p)),
              }))
            : [
                {
                  label: t('control.temps.no_presets'),
                  onSelect: () => navigate('settings'),
                },
              ]
        }
      />
      <button
        className="btn btn-sm px-2! py-0.5! text-[11.5px]"
        onClick={async () => {
          if (
            active &&
            !(await confirm({
              title: t('control.temps.cool_down_title'),
              body: t('control.temps.cool_down_body'),
              confirm: t('control.temps.cool_down'),
              danger: true,
            }))
          )
            return
          send('TURN_OFF_HEATERS')
        }}
      >
        <Snowflake size={12} /> {t('control.temps.cool_down')}
      </button>
    </>
  )
}


// ---- fans, pins, and lights ----
export function OutputsDeck({ className = '' }: { className?: string }) {
  const fans = useOutputs(FAN_TYPES)
  const pins = useOutputs(PIN_TYPES)
  const leds = useOutputs(LED_TYPES)
  const rank = (o: string) => (QIDI_FAN_ORDER.includes(nameOf(o)) ? QIDI_FAN_ORDER.indexOf(nameOf(o)) : 99)
  const settable = fans.filter((o) => ['fan', 'fan_generic'].includes(typeOf(o))).sort((a, b) => rank(a) - rank(b))
  const auto = fans.filter((o) => !settable.includes(o) && typeOf(o) !== 'temperature_fan')
  return (
    <section className={`card ${className}`}>
      <h3 className="card-title">
        <Fan size={14} /> {t('control.screen.tab_outputs')}
      </h3>
      <div className="grid gap-2.5">
        {settable.map((o) => (
          <FanRow key={o} obj={o} inline />
        ))}
        {pins.map((o) => (
          <PinRow key={o} obj={o} />
        ))}
        {leds.map((o) => (
          <LedRow key={o} obj={o} />
        ))}
        {auto.length > 0 && (
          <div className="deck-sec mt-1! pt-2!">
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
              {auto.map((o) => (
                <AutoFan key={o} obj={o} />
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}

function AutoFan({ obj }: { obj: string }) {
  const speed = usePrinter((s) => (s.status[obj]?.speed as number | undefined) ?? 0)
  return (
    <span>
      <span className="text-ink-3">{objectLabel(obj)}</span> <b className={`num ${speed > 0 ? 'text-accent-ink' : 'text-ink-3'}`}>{speed > 0 ? `${Math.round(speed * 100)}%` : t('app.temps.off')}</b>
    </span>
  )
}

// ---- macros ----
/** Home's folding macros card, by category. */
export function MacrosDeck({ className = '' }: { className?: string }) {
  const { db, loaded } = useMacrosDb()
  const macros = useMacros(db)
  const groups = macroGroups(macros, db)
  const [open, setOpen] = useState<string | null>(null)
  const [unfolded, toggle] = useFold('vo-home-macros-open')
  if (!loaded) return null
  const shown = groups.find((g) => g.id === open) ?? groups[0]
  return (
    <section className={`card ${unfolded ? 'grow' : 'folded'} ${className}`}>
      <h3 className="card-title">
        <FoldTitle open={unfolded} onToggle={toggle}>
          <Zap size={14} /> {t('control.screen.tab_macros')}
        </FoldTitle>
        <span className="aside">
          <MacrosActions />
        </span>
      </h3>
      {unfolded && (
        <>
          {groups.length > 1 && (
            <div className="mb-2.5 overflow-x-auto">
              <Segmented
                label={t('home.deck.macro_category')}
                value={shown?.id ?? ''}
                onChange={setOpen}
                options={groups.map((g) => ({
                  value: g.id,
                  label: g.name ?? t('control.macros.uncategorized'),
                }))}
              />
            </div>
          )}
          <div className="grow-body flex flex-wrap gap-1.5">
            {shown?.macros.map((m) => (
              <MacroButton key={m.name} macro={m} />
            ))}
            {!shown && (
              <span className="text-ink-3">
                {tr(macros.length ? 'home.deck.macros_hidden' : 'home.deck.no_macros', { icon: <Settings2 size={12} className="inline" /> })}
              </span>
            )}
          </div>
        </>
      )}
    </section>
  )
}

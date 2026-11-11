import { Fragment } from 'react'
import { QIDI_BOX_EXTERNAL_SLOT, type BoxState, type QidiBoxSlot } from '../state/box'
import { SpoolGlyph } from './Spool'

// Geometry, in SVG units; the drawing scales to the card's width.
const R = 24
const COL = 64
const PAD = 14
const UNIT_W = 4 * COL + PAD * 2
const EXT_W = COL + PAD * 2
const GAP = 22
const UNIT_TOP = 0
const TOOLS_Y = 34
const SPOOL_Y = 70
const INLET_Y = 104
const UNIT_BOTTOM = 116
const NODE_W = 188
const NODE_H = 30
const NODES = [
  { key: 'hub', y: 178, label: 'Hub' },
  { key: 'th', y: 220, label: 'Toolhead sensor' },
  { key: 'ex', y: 262, label: 'Extruder' },
  { key: 'noz', y: 304, label: 'Nozzle' },
] as const
const HEIGHT = 330

type Props = {
  box: BoxState
  selected: number | null
  onSelect: (slot: number) => void
  nozzleTemp: number | null
}

const curveV = (x1: number, y1: number, x2: number, y2: number) => {
  const my = (y1 + y2) / 2
  return `M${x1} ${y1} C${x1} ${my} ${x2} ${my} ${x2} ${y2}`
}

export function FilamentPath({ box, selected, onSelect, nozzleTemp }: Props) {
  const units = [...Array.from({ length: box.boxCount }, (_, i) => i + 1), 0]
  const width = units.reduce((w, u) => w + (u === 0 ? EXT_W : UNIT_W), 0) + GAP * (units.length - 1)
  const cx = width / 2
  const slotPos = new Map<number, number>()
  let x = 0
  for (const u of units) {
    if (u === 0) slotPos.set(QIDI_BOX_EXTERNAL_SLOT, x + EXT_W / 2)
    else for (let i = 0; i < 4; i++) slotPos.set((u - 1) * 4 + i, x + PAD + COL * i + COL / 2)
    x += (u === 0 ? EXT_W : UNIT_W) + GAP
  }
  const byIndex = new Map(box.slots.map((s) => [s.index, s]))
  const nodeY = (k: string) => NODES.find((n) => n.key === k)!.y
  const hubTop = nodeY('hub') - NODE_H / 2
  const toolheadSlot = box.toolheadSlot != null ? byIndex.get(box.toolheadSlot) : undefined
  const hubSlot = box.hubSlot != null ? byIndex.get(box.hubSlot) : undefined
  const extX = slotPos.get(QIDI_BOX_EXTERNAL_SLOT)!

  const toolsFor = (slot: number) =>
    Object.entries(box.toolMap)
      .filter(([, s]) => s === slot)
      .map(([t]) => Number(t))

  const sub: Record<string, string> = {
    hub: box.hubSensor === 0 ? (hubSlot ? `${hubSlot.label} here` : 'filament here') : 'empty',
    th: box.toolheadFilament == null ? '' : box.toolheadFilament ? 'filament' : 'empty',
    ex: toolheadSlot ? `${toolheadSlot.label} loaded` : '',
    noz: nozzleTemp != null ? `${Math.round(nozzleTemp)} °C` : '',
  }
  const nodeOn = (k: string) => (k === 'hub' ? box.hubSensor === 0 : k === 'th' ? box.toolheadFilament === true : toolheadSlot != null)

  return (
    <svg viewBox={`-4 -4 ${width + 8} ${HEIGHT + 8}`} className="block h-auto w-full" role="group" aria-label="Filament path">
      {/* guide strands from every slot with filament at its inlet */}
      {box.slots
        .filter((s) => s.index !== QIDI_BOX_EXTERNAL_SLOT && s.present)
        .map((s) => (
          <path key={s.index} d={curveV(slotPos.get(s.index)!, INLET_Y + 4, cx, hubTop)} stroke="var(--line-strong)" strokeWidth={2.5} fill="none" />
        ))}
      <path d={`M${cx} ${hubTop} L${cx} ${nodeY('noz')}`} stroke="var(--line-strong)" strokeWidth={3} fill="none" />
      <path d={curveV(extX, INLET_Y + 4, cx + NODE_W / 2 - 10, nodeY('th') - NODE_H / 2)} stroke="var(--line-strong)" strokeWidth={2} strokeDasharray="2 4" fill="none" />

      {/* live strands */}
      {hubSlot && <Strand d={`${curveV(slotPos.get(hubSlot.index)!, INLET_Y + 4, cx, hubTop)} L${cx} ${nodeY('hub')}`} color={hubSlot.color} />}
      {toolheadSlot && toolheadSlot.index !== QIDI_BOX_EXTERNAL_SLOT && <Strand d={`M${cx} ${nodeY('hub')} L${cx} ${nodeY('noz')}`} color={toolheadSlot.color} />}
      {toolheadSlot && toolheadSlot.index === QIDI_BOX_EXTERNAL_SLOT && (
        <Strand d={`${curveV(extX, INLET_Y + 4, cx + NODE_W / 2 - 10, nodeY('th') - NODE_H / 2)} L${cx} ${nodeY('th')} L${cx} ${nodeY('noz')}`} color={toolheadSlot.color} />
      )}

      {/* units */}
      {(() => {
        let ux = 0
        return units.map((u) => {
          const w = u === 0 ? EXT_W : UNIT_W
          const x0 = ux
          ux += w + GAP
          const climate = box.climates.find((c) => c.box === u)
          const slots = u === 0 ? [byIndex.get(QIDI_BOX_EXTERNAL_SLOT)!] : [0, 1, 2, 3].map((i) => byIndex.get((u - 1) * 4 + i)!)
          return (
            <g key={u}>
              <rect x={x0} y={UNIT_TOP} width={w} height={UNIT_BOTTOM - UNIT_TOP} rx={12} fill="var(--inset)" stroke="var(--line)" />
              <text x={x0 + 12} y={UNIT_TOP + 17} fontSize={11} fontWeight={650} fill="var(--ink-2)">
                {u === 0 ? 'External' : `Box ${u}`}
              </text>
              {climate && (
                <text x={x0 + w - 12} y={UNIT_TOP + 17} fontSize={11} fill="var(--ink-3)" textAnchor="end">
                  {[climate.temperature != null ? `${Math.round(climate.temperature)} °C` : null, climate.target ? `→ ${climate.target}` : null, climate.humidity != null ? `${climate.humidity}% RH` : null].filter(Boolean).join(' · ')}
                </text>
              )}
              {slots.map((s) => s && <SlotGlyph key={s.index} slot={s} x={slotPos.get(s.index)!} tools={toolsFor(s.index)} selected={selected === s.index} loaded={box.loadedSlot === s.index} group={box.reloadGroups[s.index]?.letter} onSelect={onSelect} />)}
            </g>
          )
        })
      })()}

      {/* path nodes */}
      {NODES.map((n) => (
        <g key={n.key} transform={`translate(${cx - NODE_W / 2} ${n.y - NODE_H / 2})`}>
          <rect width={NODE_W} height={NODE_H} rx={8} fill="var(--surface)" stroke={nodeOn(n.key) ? 'var(--accent)' : 'var(--line-strong)'} strokeWidth={nodeOn(n.key) ? 1.5 : 1} />
          <text x={12} y={19.5} fontSize={12} fontWeight={600} fill="var(--ink)">
            {n.label}
          </text>
          <text x={NODE_W - 12} y={19.5} fontSize={11} fill="var(--ink-3)" textAnchor="end">
            {sub[n.key]}
          </text>
        </g>
      ))}
    </svg>
  )
}

function Strand({ d, color }: { d: string; color: string | null }) {
  return (
    <>
      <path d={d} stroke="var(--ink-3)" strokeOpacity={0.45} strokeWidth={7} fill="none" strokeLinecap="round" />
      <path d={d} stroke={color ?? 'var(--accent)'} strokeWidth={4} fill="none" strokeLinecap="round" strokeDasharray="6 6" style={{ animation: 'vo-dash 1s linear infinite' }} />
    </>
  )
}

function SlotGlyph({ slot, x, tools, selected, loaded, group, onSelect }: { slot: QidiBoxSlot; x: number; tools: number[]; selected: boolean; loaded: boolean; group?: string; onSelect: (s: number) => void }) {
  const chips = [...tools.map((t) => `T${t}`), ...(group ? [group] : [])]
  const chipW = 26
  const startX = x - (chips.length * (chipW + 3) - 3) / 2
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`Slot ${slot.label}`}
      aria-pressed={selected}
      className="cursor-pointer outline-none"
      onClick={() => onSelect(slot.index)}
      onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelect(slot.index))}
    >
      <rect x={x - COL / 2 + 2} y={TOOLS_Y - 12} width={COL - 4} height={INLET_Y - TOOLS_Y + 20} rx={10} fill={selected ? 'var(--accent-tint)' : 'transparent'} />
      {chips.map((c, i) => (
        <Fragment key={c}>
          <rect x={startX + i * (chipW + 3)} y={TOOLS_Y - 8} width={chipW} height={15} rx={4} fill={loaded && c.startsWith('T') ? 'var(--accent)' : 'var(--surface)'} stroke={loaded && c.startsWith('T') ? 'none' : 'var(--line)'} />
          <text x={startX + i * (chipW + 3) + chipW / 2} y={TOOLS_Y + 3} textAnchor="middle" fontSize={9.5} fontWeight={650} fill={loaded && c.startsWith('T') ? '#fff' : c.startsWith('T') ? 'var(--ink-2)' : 'var(--orange)'}>
            {c}
          </text>
        </Fragment>
      ))}
      <SpoolGlyph slot={slot} cx={x} cy={SPOOL_Y} r={R} selected={selected} loaded={loaded && !selected} />
      {slot.index !== QIDI_BOX_EXTERNAL_SLOT && <circle cx={x} cy={INLET_Y} r={3.5} fill={slot.present ? 'var(--green)' : 'var(--ink-3)'} opacity={slot.present ? 1 : 0.5} />}
    </g>
  )
}

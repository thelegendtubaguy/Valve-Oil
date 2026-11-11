import { useId } from 'react'
import { qidiColorNearBackground } from '../qidi/qidi-box'
import type { QidiBoxSlot } from '../state/box'

const isDark = () => document.documentElement.classList.contains('dark')

/** A spool drawn at (cx, cy) inside an SVG: filament color, winding rings, and the slot label on the hub. */
export function SpoolGlyph({ slot, cx, cy, r, selected, loaded, label = true }: { slot: Pick<QidiBoxSlot, 'label' | 'present' | 'color' | 'reading'>; cx: number; cy: number; r: number; selected?: boolean; loaded?: boolean; label?: boolean }) {
  const gid = useId()
  const fs = Math.max(8, Math.round(r * 0.42))
  if (!slot.present || !slot.color) {
    return (
      <g>
        <circle cx={cx} cy={cy} r={r - 2} fill="none" stroke={slot.present ? 'var(--ink-3)' : 'var(--line-strong)'} strokeWidth={1.5} strokeDasharray={slot.present ? undefined : '4 4'} />
        {label && (
          <text x={cx} y={cy + fs * 0.36} textAnchor="middle" fontSize={fs} fontWeight={650} fill="var(--ink-3)">
            {slot.label}
          </text>
        )}
        {selected && <circle cx={cx} cy={cy} r={r + 2} fill="none" stroke="var(--accent)" strokeWidth={2} />}
        {slot.reading && <ReadingRing cx={cx} cy={cy} r={r + 1} />}
      </g>
    )
  }
  const light = parseInt(slot.color.slice(1), 16) > 0x999999
  const outline = qidiColorNearBackground(slot.color, isDark())
  return (
    <g>
      <defs>
        <radialGradient id={gid} cx=".35" cy=".3" r=".8">
          <stop offset="0" stopColor="#fff" stopOpacity=".5" />
          <stop offset=".6" stopColor="#fff" stopOpacity="0" />
        </radialGradient>
      </defs>
      <circle cx={cx} cy={cy} r={r - 3} fill={slot.color} stroke={outline ? 'var(--ink-3)' : 'none'} strokeWidth={outline ? 1.2 : 0} />
      <circle cx={cx} cy={cy} r={r - 3} fill={`url(#${gid})`} opacity={0.5} />
      {[0, 1, 2, 3].map((i) => (
        <circle key={i} cx={cx} cy={cy} r={r * 0.44 + i * (r * 0.11)} fill="none" stroke={light ? '#0002' : '#fff2'} />
      ))}
      <circle cx={cx} cy={cy} r={r * 0.38} fill="var(--surface)" />
      {label && (
        <text x={cx} y={cy + fs * 0.36} textAnchor="middle" fontSize={fs} fontWeight={700} fill="var(--ink)">
          {slot.label}
        </text>
      )}
      {(selected || loaded) && <circle cx={cx} cy={cy} r={r + 1} fill="none" stroke="var(--accent)" strokeWidth={selected ? 2.5 : 1.5} strokeDasharray={selected ? undefined : '3 3'} />}
      {slot.reading && <ReadingRing cx={cx} cy={cy} r={r + 1} />}
    </g>
  )
}

function ReadingRing({ cx, cy, r }: { cx: number; cy: number; r: number }) {
  return (
    <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--orange)" strokeWidth={2} strokeDasharray="9 6" className="spin" style={{ transformOrigin: `${cx}px ${cy}px` }}>
      <title>Reading RFID tag</title>
    </circle>
  )
}

export function Spool({ slot, size = 44, selected, loaded, label }: { slot: Pick<QidiBoxSlot, 'label' | 'present' | 'color' | 'reading'>; size?: number; selected?: boolean; loaded?: boolean; label?: boolean }) {
  const pad = 3
  return (
    <svg width={size + pad * 2} height={size + pad * 2} viewBox={`${-pad} ${-pad} ${size + pad * 2} ${size + pad * 2}`} aria-hidden className="flex-none overflow-visible">
      <SpoolGlyph slot={slot} cx={size / 2} cy={size / 2} r={size / 2} selected={selected} loaded={loaded} label={label} />
    </svg>
  )
}

export function slotTitle(slot: QidiBoxSlot) {
  if (!slot.present) return slot.index === 16 ? 'External spool' : 'Empty'
  return [slot.vendor, slot.name ?? slot.type].filter(Boolean).join(' ') || 'Unknown filament'
}

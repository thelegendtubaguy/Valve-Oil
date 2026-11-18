import { SpoolGlyph } from '../Spool'
import type { Spool } from './spoolman'

/** A spool in its filament color inside a ring showing how much is left, as Fluidd draws spools in a progress circle. */
export function SpoolIcon({ spool, size = 44, label = '' }: { spool: Spool | null | undefined; size?: number; label?: string }) {
  const stroke = Math.max(2.5, size / 16)
  const r = size / 2 - stroke / 2
  const c = 2 * Math.PI * r
  const left = Math.min(100, Math.max(0, spool?.progress ?? 0)) / 100
  // Spoolman allows #RRGGBBAA; the glyph shades by the opaque color.
  const color = spool?.filament.colors?.[0]?.slice(0, 7) ?? null
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden className="flex-none overflow-visible">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line)" strokeWidth={stroke} />
      {spool?.progress != null && (
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${c * left} ${c}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      )}
      <SpoolGlyph slot={{ label, present: !!spool, color, reading: false }} cx={size / 2} cy={size / 2} r={size / 2 - stroke - 2} label={!!label} />
    </svg>
  )
}

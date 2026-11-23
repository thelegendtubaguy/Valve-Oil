// Valve Oil's oil can, as on the USB installer and touchscreen screens. The light parts follow the
// theme's ink and the separating strokes its background, so the mark reads on dark and light pages.
const INK = 'var(--logo-ink, var(--ink))'
const CUT = 'var(--logo-cut, var(--rail))'

/** The oil can alone. */
export function LogoMark({ height = 40 }: { height?: number }) {
  return (
    <svg viewBox="40 8 220 470" height={height} width={(height * 220) / 470} aria-hidden>
      <rect x="50" y="150" width="200" height="190" fill={INK} />
      <ellipse cx="150" cy="340" rx="100" ry="32" fill={INK} />
      <path d="M50,190 Q150,220 250,190" stroke="#F2B05A" strokeWidth="7" fill="none" opacity="0.95" />
      <path d="M50,300 Q150,330 250,300" stroke="#F2B05A" strokeWidth="7" fill="none" opacity="0.95" />
      <ellipse cx="150" cy="150" rx="100" ry="32" fill="#F2B05A" stroke={CUT} strokeWidth="6" />
      <ellipse cx="150" cy="150" rx="34" ry="11" fill="#E39A3E" />
      <rect x="136" y="70" width="28" height="82" fill={INK} stroke={CUT} strokeWidth="5" />
      <rect x="88" y="34" width="124" height="38" fill="#A93C4B" />
      <ellipse cx="150" cy="72" rx="62" ry="19" fill="#A93C4B" />
      <ellipse cx="150" cy="34" rx="62" ry="19" fill="#C84A5A" stroke={CUT} strokeWidth="5" />
      <path d="M150,392 C164,412 180,428 180,444 A30,28 0 0 1 120,444 C120,428 136,412 150,392 Z" fill="#F2B05A" stroke={CUT} strokeWidth="5" />
    </svg>
  )
}

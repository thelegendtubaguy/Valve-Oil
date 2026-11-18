// Valve Oil's oil can and wordmark, as on the USB installer and touchscreen screens. The light parts follow the
// theme's ink and the separating strokes its background, so the mark reads on dark and light pages.
const INK = 'var(--logo-ink, var(--ink))'
const CUT = 'var(--logo-cut, var(--rail))'

function Can() {
  return (
    <>
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
    </>
  )
}

function Word() {
  return (
    <>
      <g transform="translate(370,15)">
        <polygon points="0,0 68,0 105.0,128 142,0 210,0 145.0,200 65.0,200" fill={INK} />
        <polygon points="297.0,0 385.0,0 448,200 234,200" fill={INK} />
        <polygon points="341.0,72 381.0,170 301.0,170" fill="#C84A5A" />
        <polygon points="472,0 538,0 538,140 642,140 642,200 472,200" fill={INK} />
        <polygon points="666,0 734,0 771.0,128 808,0 876,0 811.0,200 731.0,200" fill={INK} />
        <polygon points="900,0 1070,0 1070,50 966,50 966,77 1056,77 1056,123 966,123 966,150 1070,150 1070,200 900,200" fill={INK} />
      </g>
      <g transform="translate(370,255)">
        <path d="M88.0,0 C128.0,50 176,98 176,132 A88.0,74.0 0 0 1 0,132 C0,98 48.0,50 88.0,0 Z" fill={INK} />
        <path d="M88.0,78 C104.0,100 126,118 126,136 A38.0,34.0 0 0 1 50,136 C50,118 72.0,100 88.0,78 Z" fill="#F2B05A" />
        <rect x="200" y="0" width="66" height="200" fill={INK} />
        <polygon points="290,0 356,0 356,140 460,140 460,200 290,200" fill={INK} />
      </g>
    </>
  )
}

/** The oil can alone. */
export function LogoMark({ height = 40, className = '' }: { height?: number; className?: string }) {
  return (
    <svg className={className} viewBox="40 8 220 470" height={height} width={(height * 220) / 470} aria-hidden>
      <Can />
    </svg>
  )
}

/** The oil can with VALVE OIL beside it. */
export function LogoFull({ height = 80, className = '' }: { height?: number; className?: string }) {
  return (
    <svg className={className} viewBox="20 0 1440 490" height={height} width={(height * 1440) / 490} role="img" aria-label="Valve Oil">
      <Can />
      <Word />
    </svg>
  )
}

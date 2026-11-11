/** Relative luminance 0..1 of a #rrggbb color. */
export function luminance(hex: string): number {
  const n = parseInt(hex.replace('#', '').slice(0, 6), 16)
  if (Number.isNaN(n)) return 0.5
  const r = (n >> 16) & 255
  const g = (n >> 8) & 255
  const b = n & 255
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
}

export function normalizeHex(c: string | null | undefined): string | null {
  if (!c) return null
  let s = c.trim().replace(/^#/, '')
  if (s.length === 8) s = s.slice(0, 6) // RRGGBBAA
  if (s.length === 3) s = s.split('').map((x) => x + x).join('')
  return /^[0-9a-f]{6}$/i.test(s) ? `#${s.toLowerCase()}` : null
}

/** Perceptual-ish distance between two colors (weighted RGB). */
export function colorDistance(a: string, b: string): number {
  const pa = parseInt(a.slice(1), 16)
  const pb = parseInt(b.slice(1), 16)
  const r1 = (pa >> 16) & 255, g1 = (pa >> 8) & 255, b1 = pa & 255
  const r2 = (pb >> 16) & 255, g2 = (pb >> 8) & 255, b2 = pb & 255
  const rm = (r1 + r2) / 2
  const dr = r1 - r2, dg = g1 - g2, db = b1 - b2
  return Math.sqrt((2 + rm / 256) * dr * dr + 4 * dg * dg + (2 + (255 - rm) / 256) * db * db)
}

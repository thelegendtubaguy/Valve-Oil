export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return '—'
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h > 0) return `${h} h ${String(m).padStart(2, '0')} m`
  if (m > 0) return `${m} min`
  return `${s} s`
}

export function fmtClock(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return '—:—'
  const s = Math.round(seconds)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function fmtEta(remainingSeconds: number | null | undefined): string {
  if (remainingSeconds == null || !isFinite(remainingSeconds)) return '—'
  return new Date(Date.now() + remainingSeconds * 1000).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

export function fmtTemp(t: number | null | undefined): string {
  return t == null || !isFinite(t) ? '—' : `${Math.round(t)}`
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 ** 2).toFixed(1)} MB`
}

export function fmtAgo(epochSeconds: number): string {
  const d = Date.now() / 1000 - epochSeconds
  if (d < 90) return 'just now'
  if (d < 3600) return `${Math.round(d / 60)} min ago`
  if (d < 86400) return `${Math.round(d / 3600)} h ago`
  if (d < 86400 * 14) return `${Math.round(d / 86400)} days ago`
  return new Date(epochSeconds * 1000).toLocaleDateString()
}

/** Name without directories and without .gcode / .gcode.3mf. */
export function displayName(path: string): string {
  const base = path.split('/').pop() ?? path
  return base.replace(/\.gcode(\.3mf)?$/i, '')
}

/** Sensible title case for Klipper object names: "heater_generic chamber" -> "Chamber". */
export function prettyName(obj: string): string {
  const name = obj.includes(' ') ? obj.slice(obj.indexOf(' ') + 1) : obj
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

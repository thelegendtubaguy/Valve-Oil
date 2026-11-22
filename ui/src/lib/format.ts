import { locale, t } from '../i18n'

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return '—'
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h > 0) return t('common.time.hours_minutes', { h, mm: String(m).padStart(2, '0') })
  if (m > 0) return t('common.time.minutes', { m })
  return t('common.time.seconds', { s })
}

export function fmtClock(seconds: number | null | undefined): string {
  if (seconds == null || !isFinite(seconds) || seconds < 0) return '—:—'
  const s = Math.round(seconds)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function fmtEta(remainingSeconds: number | null | undefined): string {
  if (remainingSeconds == null || !isFinite(remainingSeconds)) return '—'
  return new Date(Date.now() + remainingSeconds * 1000).toLocaleTimeString(locale(), { hour: 'numeric', minute: '2-digit' })
}

export function fmtTemp(t: number | null | undefined): string {
  return t == null || !isFinite(t) ? '—' : `${Math.round(t)}`
}

export function fmtBytes(n: number): string {
  if (n < 1024) return t('common.bytes.b', { n })
  if (n < 1024 ** 2) return t('common.bytes.kb', { n: (n / 1024).toFixed(0) })
  return t('common.bytes.mb', { n: (n / 1024 ** 2).toFixed(1) })
}

export function fmtAgo(epochSeconds: number): string {
  const d = Date.now() / 1000 - epochSeconds
  if (d < 90) return t('common.time.just_now')
  if (d < 3600) return t('common.time.minutes_ago', { m: Math.round(d / 60) })
  if (d < 86400) return t('common.time.hours_ago', { h: Math.round(d / 3600) })
  if (d < 86400 * 14) return t('common.time.days_ago', { count: Math.round(d / 86400) })
  return new Date(epochSeconds * 1000).toLocaleDateString(locale())
}

/** Name without directories and without .gcode / .gcode.3mf. */
export function displayName(path: string): string {
  const base = path.split('/').pop() ?? path
  return base.replace(/\.gcode(\.3mf)?$/i, '')
}

/** Sensible title case for Klipper object names: "heater_generic chamber" -> "Chamber". */
export function prettyName(obj: string): string {
  const name = obj.includes(' ') ? obj.slice(obj.indexOf(' ') + 1) : obj
  // QIDI's chamber heater and sensor, named in the language shown like the nozzle and bed.
  if (name.toLowerCase() === 'chamber') return t('common.chamber')
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

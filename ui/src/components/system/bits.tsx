import type { ReactNode } from 'react'
import { prettyName } from '../../lib/format'
import { t } from '../../i18n'

/** A label and value row; rows with no value are left out. */
export function Row({ k, children, mono }: { k: ReactNode; children: ReactNode; mono?: boolean }) {
  if (children == null || children === '' || children === false) return null
  return (
    <div className="row-k">
      <span className="k">{k}</span>
      <span className={`v min-w-0 break-words ${mono ? 'code font-medium' : ''}`}>{children}</span>
    </div>
  )
}

/** A Klipper mcu object's name for people: mcu → Mainboard, mcu THR → THR, mcu mcu_box1 → Box 1. */
export function mcuLabel(obj: string) {
  if (obj === 'mcu') return t('system.mcu.mainboard')
  const n = obj.slice(4)
  const box = /^mcu_box(\d+)$/i.exec(n)
  if (box) return `Box ${box[1]}`
  return n.toUpperCase() === n ? n : prettyName(obj)
}

export function fmtSize(bytes: number | null | undefined) {
  if (bytes == null || !Number.isFinite(bytes)) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let v = bytes
  let i = 0
  while (v >= 1024 && i < units.length - 1) (v /= 1024), i++
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

export function fmtFreq(hz: number) {
  if (hz >= 1e6) return `${Number((hz / 1e6).toFixed(1))} MHz`
  if (hz >= 1e3) return `${Number((hz / 1e3).toFixed(1))} kHz`
  return `${hz} Hz`
}

/** Moonraker's service state as a tone and words. */
export function serviceTone(active: string | undefined) {
  if (active === 'active') return 'tone-good'
  if (active === 'failed') return 'tone-bad'
  if (active === 'activating' || active === 'deactivating' || active === 'reloading') return 'tone-warn'
  return 'tone-muted'
}

/** Fluidd's backup name: the local time as yyyyMMdd-HHmmss. */
export function timestampName(d = new Date()) {
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
}

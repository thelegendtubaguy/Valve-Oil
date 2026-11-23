import { useCallback, useState, type ReactNode } from 'react'
import { Download, Eye } from 'lucide-react'
import { KV } from '../ui'
import { client } from '../../state/printer'
import { errorText } from '../../lib/browser'
import { prettyName } from '../../lib/format'
import { t } from '../../i18n'

/** A label and value row; rows with no value are left out. */
export function Row({ k, children, mono }: { k: ReactNode; children: ReactNode; mono?: boolean }) {
  if (children == null || children === '' || children === false) return null
  return (
    <KV k={k} vClassName={`min-w-0 break-words ${mono ? 'code font-medium' : ''}`}>
      {children}
    </KV>
  )
}

export type LogFile = { filename: string; size: number; modified: number; permissions?: string }

/** Moonraker's logs folder; `load` reads it again. */
export function useLogFiles() {
  const [files, setFiles] = useState<LogFile[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await client.call<{ files: LogFile[] }>('server.files.get_directory', { path: 'logs', extended: true })
      setFiles(r.files ?? [])
      setError(null)
    } catch (e) {
      setError(errorText(e))
    }
    setLoading(false)
  }, [])
  return { files, error, loading, load }
}

/** A log's View and Download buttons. */
export function LogButtons({ file, onView, className }: { file: LogFile; onView: () => void; className: string }) {
  return (
    <>
      <button className={className} onClick={onView} aria-label={t('system.logs.view_named', { name: file.filename })} title={t('system.logs.view')}>
        <Eye size={15} />
      </button>
      <a className={className} href={client.fileUrl('logs', file.filename)} download={file.filename} aria-label={t('system.logs.download_named', { name: file.filename })} title={t('system.logs.download')}>
        <Download size={15} />
      </a>
    </>
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

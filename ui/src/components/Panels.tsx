import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Activity, ChevronRight } from 'lucide-react'
import { href } from '../lib/router'
import { client, usePrinter } from '../state/printer'
import { fmtDuration, prettyName } from '../lib/format'
import { t } from '../i18n'

/** Filament length as Fluidd reads it: mm under a metre, then metres. */
export function fmtLength(mm: number) {
  return mm < 1000 ? `${Math.round(mm)} mm` : t('app.units.m', { n: (mm / 1000).toFixed(2) })
}

function Row({ k, v }: { k: ReactNode; v: ReactNode }) {
  return (
    <div className="row-k">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  )
}

// ---- printer health ----
type Disk = { total: number; used: number; free: number }

// Where each reading turns orange (warn) and red (bad), shared by the card and the top bar's health button.
const LIMITS = { klipper: [0.7, 0.9], cpu: [0.75, 0.92], mem: [0.8, 0.92], mcu: [0.6, 0.85], timers: 120, freeShare: 0.1 } as const

type Level = 'ok' | 'warn' | 'bad'
const level = (v: number, [warn, bad]: readonly [number, number]): Level => (v >= bad ? 'bad' : v >= warn ? 'warn' : 'ok')

/** The worst of the health readings, for the top bar's health button. */
function useHealthLevel(): Level {
  const disk = useDisk()
  const key = usePrinter((s) => {
    const h = s.status.vo_klipper_health as { main_thread_load?: number | null; timers?: number } | undefined
    const levels: Level[] = []
    if (h?.main_thread_load != null) levels.push(level(h.main_thread_load, LIMITS.klipper))
    if (h?.timers != null && h.timers > LIMITS.timers) levels.push('warn')
    if (s.host?.cpu != null) levels.push(level(s.host.cpu / 100, LIMITS.cpu))
    if (s.host?.memory) levels.push(level(1 - s.host.memory.available / s.host.memory.total, LIMITS.mem))
    for (const o of s.objects) if (o === 'mcu' || o.startsWith('mcu ')) {
      const awake = s.status[o]?.last_stats?.mcu_awake as number | undefined
      if (awake != null) levels.push(level(awake, LIMITS.mcu))
    }
    return levels.includes('bad') ? 'bad' : levels.includes('warn') ? 'warn' : 'ok'
  })
  if (key === 'ok' && disk && disk.free / disk.total < LIMITS.freeShare) return 'warn'
  return key
}

/** Health in the top bar: an icon that turns orange or red when a reading crosses its limit, and the readings on click. */
export function HealthButton() {
  const lvl = useHealthLevel()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: Event) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', esc)
    return () => (window.removeEventListener('pointerdown', close), window.removeEventListener('keydown', esc))
  }, [open])
  const tone = lvl === 'bad' ? 'text-bad' : lvl === 'warn' ? 'text-warn' : ''
  const label = lvl === 'bad' ? t('app.health.bad') : lvl === 'warn' ? t('app.health.warn') : t('app.health.title')
  return (
    <div className="sm:relative" ref={ref}>
      <button type="button" className={`iconbtn ${tone} ${open ? 'bg-hover' : ''}`} onClick={() => setOpen((o) => !o)} aria-haspopup="dialog" aria-expanded={open} aria-label={label} title={label}>
        <Activity size={17} />
      </button>
      {open && (
        <div className="absolute inset-x-2 top-[calc(100%+6px)] z-50 rounded-2xl bg-surface sm:inset-x-auto sm:right-0 sm:w-[440px]" style={{ boxShadow: 'var(--shadow-overlay)' }} role="dialog" aria-label={t('app.health.title')}>
          <Health bare />
          <a href={href('system')} className="flex items-center justify-end gap-1 border-t border-line px-4 py-2.5 text-[12.5px] font-semibold text-accent-ink" onClick={() => setOpen(false)}>
            {t('app.nav.system')} <ChevronRight size={13} />
          </a>
        </div>
      )}
    </div>
  )
}

export function Health({ className = '', bare }: { className?: string; bare?: boolean }) {
  const health = usePrinter((s) => s.status.vo_klipper_health as { main_thread_load?: number | null; timers?: number } | undefined)
  const host = usePrinter((s) => s.host)
  const mcus = usePrinter((s) =>
    s.objects
      .filter((o) => o === 'mcu' || o.startsWith('mcu '))
      .map((o) => ({ name: mcuName(o), awake: s.status[o]?.last_stats?.mcu_awake as number | undefined }))
      .filter((m) => m.awake != null),
  )
  const sensors = usePrinter((s) =>
    s.objects
      .filter((o) => o.startsWith('temperature_sensor ') && !/chamber|heater_temp/i.test(o))
      .map((o) => ({ name: prettyName(o), t: s.status[o]?.temperature as number | undefined })),
  )
  const disk = useDisk()
  const mem = host?.memory ? 1 - host.memory.available / host.memory.total : null
  return (
    <section className={bare ? `px-4 pt-3 pb-3 ${className}` : `card ${className}`}>
      <h3 className="card-title">
        <Activity size={14} /> {t('app.health.title')}
      </h3>
      <div className="rows-2">
        {health?.main_thread_load != null && <Row k="Klipper" v={<LevelBar v={health.main_thread_load} limits={LIMITS.klipper} />} />}
        {health?.timers != null && <Row k={t('app.health.timers')} v={<span className={health.timers > LIMITS.timers ? 'text-warn' : ''}>{health.timers}</span>} />}
        {host?.cpu != null && <Row k={t('app.health.host_cpu')} v={<LevelBar v={host.cpu / 100} limits={LIMITS.cpu} />} />}
        {mem != null && <Row k={t('app.health.memory')} v={<LevelBar v={mem} limits={LIMITS.mem} />} />}
        {host?.cpuTemp != null && <Row k={t('app.health.host_temp')} v={`${Math.round(host.cpuTemp)} °C`} />}
        {disk && <Row k={t('app.health.free_space')} v={<span className={disk.free / disk.total < LIMITS.freeShare ? 'text-warn' : ''}>{t('app.units.gb', { n: (disk.free / 1024 ** 3).toFixed(1) })}</span>} />}
        {sensors.map((t) => (
          <Row key={t.name} k={t.name} v={t.t != null ? `${Math.round(t.t)} °C` : '—'} />
        ))}
        {host?.uptime != null && <Row k={t('app.health.up_for')} v={fmtDuration(host.uptime)} />}
      </div>
      {mcus.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[12px]" title={t('app.health.mcu_load_hint')}>
          <span className="text-ink-2">{t('app.health.mcu_load')}</span>
          {mcus.map((m) => (
            <span key={m.name} className="num">
              <span className="text-ink-3">{m.name}</span> <b className={{ bad: 'text-bad', warn: 'text-warn', ok: '' }[level(m.awake!, LIMITS.mcu)]}>{Math.round(m.awake! * 100)}%</b>
            </span>
          ))}
        </div>
      )}
    </section>
  )
}

/** Short names for Klipper's MCUs, shown after "MCU load": the mainboard, the toolhead board, and each Box. */
function mcuName(obj: string) {
  if (obj === 'mcu') return t('app.health.mainboard')
  const n = obj.slice(4)
  const box = /^mcu_box(\d+)$/i.exec(n)
  return box ? t('app.heaters.box', { n: box[1] }) : n.toUpperCase() === n ? n : prettyName(obj)
}

/** A percentage with a small bar; the bar turns orange or red past the thresholds. */
function LevelBar({ v, limits: [warn, bad] }: { v: number; limits: readonly [number, number] }) {
  const color = v >= bad ? 'var(--red)' : v >= warn ? 'var(--orange)' : 'var(--green)'
  return (
    <span className="inline-flex items-center gap-2">
      <span className="h-1 w-8 overflow-hidden rounded-full bg-inset">
        <i className="block h-full rounded-full" style={{ width: `${Math.min(100, Math.round(v * 100))}%`, background: color }} />
      </span>
      <span className={v >= bad ? 'text-bad' : v >= warn ? 'text-warn' : ''}>{Math.round(v * 100)}%</span>
    </span>
  )
}

function useDisk(): Disk | null {
  const [disk, setDisk] = useState<Disk | null>(null)
  const rev = usePrinter((s) => s.filesRev)
  const ready = usePrinter((s) => s.conn === 'open')
  useEffect(() => {
    if (!ready) return
    client
      .call<{ disk_usage?: Disk }>('server.files.get_directory', { path: 'gcodes', extended: false })
      .then((r) => setDisk(r.disk_usage ?? null))
      .catch(() => setDisk(null))
  }, [rev, ready])
  return disk
}

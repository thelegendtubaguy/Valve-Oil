import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Bell, BellOff, ChevronDown, Clock, ExternalLink, Megaphone, TriangleAlert, X } from 'lucide-react'
import { createStore, useStore } from '../lib/store'
import { fmtAgo } from '../lib/format'
import { client, usePrinter } from '../state/printer'
import { attempt } from './toast'
import { t, type MessageKey } from '../i18n'

// The top bar's notifications, as Fluidd's bell menu: Moonraker announcements (with dismiss and snooze), and the
// warnings Klipper and Moonraker report about their configuration.

export type Announcement = {
  entry_id: string
  url: string
  title: string
  description: string
  priority: 'normal' | 'high'
  date: number
  dismissed: boolean
  date_dismissed: number | null
  dismiss_wake: number | null
  source: string
  feed: string
}

const announcements = createStore<Announcement[]>([])

async function loadAnnouncements() {
  try {
    const r = await client.call<{ entries: Announcement[] }>('server.announcements.list', { include_dismissed: false })
    announcements.set(r.entries ?? [])
  } catch {
    // No announcements component.
    announcements.set([])
  }
}

function setDismissed(entry_id: string, dismissed: boolean) {
  announcements.set((list) => list.map((a) => (a.entry_id === entry_id ? { ...a, dismissed, ...(dismissed ? { date_dismissed: Date.now() / 1000 } : { date_dismissed: null, dismiss_wake: null }) } : a)))
}

let started = false
function start() {
  if (started) return
  started = true
  client.onState((c) => c === 'open' && loadAnnouncements())
  if (client.state === 'open') loadAnnouncements()
  client.on('notify_announcement_update', (p) => announcements.set(((p[0] as { entries?: Announcement[] })?.entries ?? []) as Announcement[]))
  client.on('notify_announcement_dismissed', (p) => setDismissed(String((p[0] as { entry_id: string })?.entry_id), true))
  client.on('notify_announcement_wake', (p) => setDismissed(String((p[0] as { entry_id: string })?.entry_id), false))
}
start()

const dismiss = (entry_id: string, wake_time?: number) => attempt(() => client.call('server.announcements.dismiss', wake_time ? { entry_id, wake_time } : { entry_id }))

type ConfigWarning = { type?: string; message: string; section?: string; option?: string }

/** What the bell lists: open announcements, newest first, then Klipper's and Moonraker's warnings. */
export function useNotifications() {
  const list = useStore(announcements, (a) => a)
  const open = list.filter((a) => !a.dismissed).sort((a, b) => b.date - a.date)
  const klipper = usePrinter((s) => (s.status.configfile?.warnings as ConfigWarning[] | undefined) ?? EMPTY)
  const moonraker = usePrinter((s) => s.server?.warnings ?? EMPTY_S)
  return { announcements: open, klipper, moonraker, count: open.length + klipper.length + moonraker.length }
}
const EMPTY: ConfigWarning[] = []
const EMPTY_S: string[] = []

/** The bell in the top bar, with a count, opening the list. */
export function NotificationsButton() {
  const n = useNotifications()
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
  const warn = n.klipper.length + n.moonraker.length > 0
  const high = n.announcements.some((a) => a.priority === 'high')
  const tone = warn || high ? 'bg-accent text-on-accent' : 'bg-[var(--blue)] text-white'
  return (
    <div className="sm:relative" ref={ref}>
      <button type="button" className={`iconbtn ${open ? 'bg-hover text-ink' : ''}`} onClick={() => setOpen((o) => !o)} aria-haspopup="dialog" aria-expanded={open} aria-label={n.count ? t('app.notifications.title_count', { n: n.count }) : t('app.notifications.title')} title={t('app.notifications.title')}>
        <Bell size={17} />
        {n.count > 0 && <span className={`num absolute -top-0.5 -right-0.5 grid h-[17px] min-w-[17px] place-items-center rounded-full px-1 text-[10.5px] leading-none font-bold ${tone}`} style={{ boxShadow: '0 0 0 2px var(--page)' }}>{n.count > 99 ? '99+' : n.count}</span>}
      </button>
      {open && <Panel n={n} onClose={() => setOpen(false)} />}
    </div>
  )
}

function Panel({ n, onClose }: { n: ReturnType<typeof useNotifications>; onClose: () => void }) {
  return (
    <div
      className="absolute inset-x-2 top-[calc(100%+6px)] z-50 flex max-h-[calc(100dvh-200px)] flex-col overflow-hidden rounded-2xl bg-surface sm:inset-x-auto sm:right-0 sm:w-[420px] sm:max-h-[min(80dvh,640px)]"
      style={{ boxShadow: 'var(--shadow-overlay)' }}
      role="dialog"
      aria-label={t('app.notifications.title')}
    >
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <div className="flex-1 text-[14px] font-bold">{t('app.notifications.title')}</div>
        {n.announcements.length > 1 && (
          <button className="btn btn-ghost btn-sm" onClick={() => n.announcements.forEach((a) => dismiss(a.entry_id))}>
            {t('app.notifications.clear_all')}
          </button>
        )}
        <button className="iconbtn h-7 w-7" onClick={onClose} aria-label={t('common.close')}>
          <X size={15} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-2">
        {n.count === 0 && (
          <div className="grid justify-items-center gap-2 px-4 py-8 text-center text-ink-3">
            <BellOff size={22} />
            {t('app.notifications.none')}
          </div>
        )}
        {n.klipper.length > 0 && (
          <Group label={t('app.notifications.klipper_warnings')}>
            {n.klipper.map((w, i) => (
              <Item key={`k${i}`} tone="warn" icon={<TriangleAlert size={15} />} title={<Linked text={w.message} />} meta={w.section ? `[${w.section}]${w.option ? ` ${w.option}` : ''}` : undefined} />
            ))}
          </Group>
        )}
        {n.moonraker.length > 0 && (
          <Group label={t('app.notifications.moonraker_warnings')}>
            {n.moonraker.map((w, i) => (
              <Item key={`m${i}`} tone="warn" icon={<TriangleAlert size={15} />} title={<Linked text={w} />} />
            ))}
          </Group>
        )}
        {n.announcements.length > 0 && (
          <Group label={t('app.notifications.announcements')}>
            {n.announcements.map((a) => (
              <Item
                key={a.entry_id}
                tone={a.priority === 'high' ? 'accent' : 'info'}
                icon={<Megaphone size={15} />}
                title={a.title}
                body={a.description ? <Linked text={a.description} /> : undefined}
                meta={`${fmtAgo(a.date)}${a.feed ? ` · ${a.feed}` : ''}`}
                actions={
                  <>
                    {a.url && (
                      <a className="btn btn-sm" href={a.url} target="_blank" rel="noopener noreferrer" onClick={onClose}>
                        <ExternalLink size={13} /> {t('app.notifications.more_info')}
                      </a>
                    )}
                    <Snooze onPick={(s) => dismiss(a.entry_id, s)} />
                  </>
                }
                onDismiss={() => dismiss(a.entry_id)}
              />
            ))}
          </Group>
        )}
      </div>
    </div>
  )
}

function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5 pb-2">
      <div className="section-label px-2 pt-1.5">{label}</div>
      {children}
    </div>
  )
}

const TONE = { warn: 'text-warn', accent: 'text-accent-ink', info: 'text-info' } as const
const BORDER = { warn: 'var(--orange)', accent: 'var(--accent)', info: 'var(--blue)' } as const

function Item({ tone, icon, title, body, meta, actions, onDismiss }: { tone: keyof typeof TONE; icon: ReactNode; title: ReactNode; body?: ReactNode; meta?: string; actions?: ReactNode; onDismiss?: () => void }) {
  return (
    <div className="flex gap-2.5 rounded-xl bg-inset py-2.5 pr-2 pl-3" style={{ boxShadow: `inset 3px 0 0 ${BORDER[tone]}, inset 0 0 0 1px var(--line)` }}>
      <span className={`mt-0.5 flex-none ${TONE[tone]}`}>{icon}</span>
      <div className="min-w-0 flex-1 [overflow-wrap:anywhere]">
        <div className="font-semibold text-ink">{title}</div>
        {body && <div className="mt-1 line-clamp-5 text-[12.5px] text-ink-2">{body}</div>}
        {meta && <div className="mt-1 text-[11.5px] text-ink-3">{meta}</div>}
        {actions && <div className="mt-2 flex flex-wrap gap-1.5">{actions}</div>}
      </div>
      {onDismiss && (
        <button className="iconbtn h-7 w-7 flex-none" onClick={onDismiss} aria-label={t('common.dismiss')} title={t('common.dismiss')}>
          <X size={14} />
        </button>
      )}
    </div>
  )
}

// Labels are message keys, looked up while rendering.
const SNOOZE: { labelKey: MessageKey; seconds: number }[] = [
  { labelKey: 'app.notifications.snooze_hour', seconds: 3600 },
  { labelKey: 'app.notifications.snooze_day', seconds: 86400 },
  { labelKey: 'app.notifications.snooze_week', seconds: 604800 },
]

/** Fluidd's Snooze: dismiss until a wake time, when Moonraker brings it back. */
function Snooze({ onPick }: { onPick: (seconds: number) => void }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button type="button" className="btn btn-sm" onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open}>
        <Clock size={13} /> {t('app.notifications.snooze')} <ChevronDown size={13} className={open ? 'rotate-180' : ''} />
      </button>
      {open && (
        <div className="menu top-[calc(100%+4px)] left-0 min-w-[140px]" role="menu">
          {SNOOZE.map((s) => (
            <button key={s.seconds} type="button" role="menuitem" onClick={() => (setOpen(false), onPick(s.seconds))}>
              <Clock size={14} /> {t(s.labelKey)}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** Plain text with its web addresses as links, as Fluidd's linkExternalUrls. */
function Linked({ text }: { text: string }) {
  const parts = text.split(/(https?:\/\/[^\s<>"')]+[^\s<>"').,;:!?])/g)
  return (
    <>
      {parts.map((p, i) =>
        i % 2 ? (
          <a key={i} href={p} target="_blank" rel="noopener noreferrer" className="text-accent-ink underline">
            {p}
          </a>
        ) : (
          p
        ),
      )}
    </>
  )
}

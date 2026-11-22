// Fluidd's Spoolman card: the active spool and how much is left on it, Change spool, and why live updates from Spoolman
// aren't working. With Valve Oil's vo_spoolman module it also lists each QIDI Box slot's spool.
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { CircleAlert, Disc3, ExternalLink, Info } from 'lucide-react'
import { Card, SectionLabel } from '../ui'
import { fmtAgo } from '../../lib/format'
import { usePrinter } from '../../state/printer'
import { QIDI_BOX_EXTERNAL_SLOT, qidiBoxSlotLabel } from '../../state/box'
import { ensureSpool, startSpoolman, useSpoolman, type SocketDiagnostic } from './store'
import { SpoolIcon } from './SpoolIcon'
import { SpoolSelectionDialog } from './SpoolSelectionDialog'
import { fmtLength, fmtWeight, slotSpools, spoolPageUrl, toSpool, type Spool } from './spoolman'
import { t, type MessageKey } from '../../i18n'

// Fluidd's wording, with the page named for this UI instead of Fluidd.
const LIVE_UPDATES: Record<SocketDiagnostic, MessageKey> = {
  cors: 'spoolman.live.cors',
  'mixed-content': 'spoolman.live.mixed_content',
  unreachable: 'spoolman.live.unreachable',
  reachable: 'spoolman.live.reachable',
}

const sentence = (parts: (string | null)[]) => {
  const text = parts.filter(Boolean).join(' · ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}
const slotTarget = (slot: number) => (slot === QIDI_BOX_EXTERNAL_SLOT ? t('spoolman.target_external') : t('spoolman.target_slot', { slot: qidiBoxSlotLabel(slot) }))

/**
 * Shown only while Moonraker has its Spoolman component loaded. `slots` lists each Box slot's spool when Valve Oil's
 * vo_spoolman module reports them (on by default).
 */
export function SpoolmanCard({ className = '', slots = true }: { className?: string; slots?: boolean }) {
  const enabled = usePrinter((s) => !!s.server?.components.includes('spoolman'))
  useEffect(() => {
    if (enabled) startSpoolman()
  }, [enabled])
  if (!enabled) return null
  return <SpoolmanCardBody className={className} showSlots={slots} />
}

function SpoolmanCardBody({ className, showSlots }: { className: string; showSlots: boolean }) {
  const connected = useSpoolman((s) => s.connected)
  const error = useSpoolman((s) => s.error)
  const raw = useSpoolman((s) => s.spools)
  const activeId = useSpoolman((s) => s.activeSpool)
  const url = useSpoolman((s) => s.url)
  const diagnostic = useSpoolman((s) => s.socketDiagnostic)
  const spoolmanModule = usePrinter((s) => s.status.vo_spoolman)
  const klippyReady = usePrinter((s) => s.klippy === 'ready')
  const perSlot = useMemo(() => slotSpools(spoolmanModule), [spoolmanModule])
  const spools = useMemo(() => new Map(raw.map((s) => [s.id, toSpool(s)])), [raw])
  const active = connected && activeId != null ? spools.get(activeId) : undefined
  const [open, setOpen] = useState(false)

  // The active spool or a slot's spool may be archived, so missing from the list; read those one by one.
  useEffect(() => {
    if (!connected) return
    for (const id of [activeId, ...(perSlot?.spools.values() ?? [])]) if (id != null) ensureSpool(id)
  }, [connected, activeId, perSlot])

  // With vo_spoolman, Change spool sets the loaded slot's spool (Valve Oil's Moonraker patch passes it on).
  const target = klippyReady && perSlot?.loadedSlot != null ? slotTarget(perSlot.loadedSlot) : null
  // With no slot spools set the active spool doesn't follow the loaded slot, so there's nothing per slot to show.
  const slotRows = showSlots && perSlot?.spools.size ? [...new Set([...perSlot.spools.keys(), ...(perSlot.loadedSlot != null ? [perSlot.loadedSlot] : [])])].sort((a, b) => a - b) : []

  return (
    <Card
      className={className}
      title="Spoolman"
      icon={<Disc3 size={14} />}
      aside={
        <button className="btn btn-sm" disabled={!connected} onClick={() => setOpen(true)}>
          {t('spoolman.change_spool')}
        </button>
      }
    >
      {connected && diagnostic && <Note tone="warn">{t(LIVE_UPDATES[diagnostic])}</Note>}
      {active ? (
        <ActiveSpool spool={active} url={url} />
      ) : connected ? (
        <Note tone="info">{t('spoolman.inactive')}</Note>
      ) : (
        <Note tone="warn">
          {t('spoolman.unavailable')}
          {error && <span className="mt-0.5 block text-[12px] opacity-80">{error}</span>}
        </Note>
      )}
      {connected && slotRows.length > 0 && (
        <div className="mt-3.5">
          <SectionLabel className="mb-1.5">{t('spoolman.box_slots')}</SectionLabel>
          <div className="grid gap-0.5">
            {slotRows.map((slot) => (
              <SlotRow key={slot} slot={slot} spoolId={perSlot!.spools.get(slot) ?? null} spool={spools.get(perSlot!.spools.get(slot) ?? -1)} loaded={perSlot!.loadedSlot === slot} />
            ))}
          </div>
        </div>
      )}
      <SpoolSelectionDialog open={open} onClose={() => setOpen(false)} target={target} />
    </Card>
  )
}

function ActiveSpool({ spool, url }: { spool: Spool; url: string | null }) {
  const f = spool.filament
  const color = f.colors?.length ? f.colors : null
  return (
    <div>
      <div className="flex min-w-0 items-center gap-3">
        <SpoolIcon spool={spool} size={56} />
        <div className="min-w-0 flex-1">
          <div className="section-label flex items-center gap-1.5">
            {t('spoolman.active_spool')}
            {url ? (
              <a className="inline-flex items-center gap-1 text-accent-ink normal-case hover:underline" href={spoolPageUrl(url, spool.id)} target="_blank" rel="noopener noreferrer" title={t('spoolman.open_in_spoolman')}>
                #{spool.id} <ExternalLink size={11} />
              </a>
            ) : (
              <span className="normal-case">#{spool.id}</span>
            )}
          </div>
          <div className="truncate text-[17px] font-bold">{f.name || spool.filament_name}</div>
          <div className="truncate text-ink-3">{[f.vendor?.name, f.material].filter(Boolean).join(' · ') || '—'}</div>
        </div>
      </div>
      {spool.progress != null && (
        <div className="bar mt-3 h-1.5!" role="progressbar" aria-label={t('spoolman.row.remaining')} aria-valuenow={Math.round(spool.progress)} aria-valuemin={0} aria-valuemax={100}>
          <i style={{ width: `${Math.min(100, Math.max(0, spool.progress))}%` }} />
        </div>
      )}
      {/* Two columns only when the card itself is wide, since Home's side columns are narrow on any screen. */}
      <div className="@container mt-2">
        <div className="grid gap-x-[18px] @sm:grid-cols-2">
        <Row k={t('spoolman.row.vendor')}>{f.vendor?.name || '—'}</Row>
        <Row k={t('spoolman.row.material')}>{f.material || '—'}</Row>
        <Row k={t('spoolman.row.filament')}>{f.name || '—'}</Row>
        <Row k={t('spoolman.row.colors')}>
          {color ? (
            <span className="inline-flex items-center gap-1">
              {color.map((c) => (
                <span key={c} className="swatch" style={{ background: c }} title={c.toUpperCase()} />
              ))}
            </span>
          ) : (
            '—'
          )}
        </Row>
        <Row k={t('spoolman.row.remaining')}>
          <Amount value={fmtWeight(spool.remaining_weight)} of={fmtWeight(spool.initial_weight)} />
          <Amount value={fmtLength(spool.remaining_length)} of={fmtLength(spool.initial_length)} sub />
        </Row>
        <Row k={t('spoolman.row.used')}>
          <Amount value={fmtWeight(spool.used_weight)} of={fmtWeight(spool.initial_weight)} />
          <Amount value={fmtLength(spool.used_length)} of={fmtLength(spool.initial_length)} sub />
        </Row>
        {spool.location && <Row k={t('spoolman.row.location')}>{spool.location}</Row>}
        {spool.lot_nr && <Row k={t('spoolman.row.lot_nr')}>{spool.lot_nr}</Row>}
        <Row k={t('spoolman.row.first_used')}>{spool.first_used ? fmtAgo(spool.first_used / 1000) : t('spoolman.row.never')}</Row>
        <Row k={t('spoolman.row.last_used')}>{spool.last_used ? fmtAgo(spool.last_used / 1000) : t('spoolman.row.never')}</Row>
        </div>
      </div>
      {spool.comment && <p className="mt-2 mb-0 text-[12.5px] text-ink-2">{spool.comment}</p>}
    </div>
  )
}

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-3 border-b border-line py-[5px]">
      <span className="text-ink-2">{k}</span>
      <span className="num min-w-0 truncate text-right font-[650]">{children}</span>
    </div>
  )
}

function Amount({ value, of, sub }: { value: string; of: string; sub?: boolean }) {
  return (
    <span className={sub ? 'block text-[12px] font-medium text-ink-3' : 'block'}>
      {value} <small className="font-medium text-ink-3">/ {of}</small>
    </span>
  )
}

function SlotRow({ slot, spoolId, spool, loaded }: { slot: number; spoolId: number | null; spool: Spool | undefined; loaded: boolean }) {
  return (
    <div className={`flex min-w-0 items-center gap-2.5 rounded-lg px-2 py-1 ${loaded ? 'bg-accent-tint' : ''}`}>
      <span className={`w-7 flex-none text-[13px] font-bold ${loaded ? 'text-accent-ink' : 'text-ink-2'}`}>{qidiBoxSlotLabel(slot)}</span>
      <SpoolIcon spool={spool} size={32} />
      <div className="min-w-0 flex-1">
        <div className="truncate font-semibold">
          {spoolId == null ? <span className="text-ink-3">{t('spoolman.no_spool')}</span> : (spool?.filament_name ?? t('spoolman.spool_n', { id: spoolId }))}
        </div>
        <div className="truncate text-[12px] text-ink-3">{sentence([slot === QIDI_BOX_EXTERNAL_SLOT ? t('box.external_spool') : null, loaded ? t('spoolman.loaded') : null, spoolId != null ? `#${spoolId}` : null])}</div>
      </div>
      {spool && <span className="num flex-none text-[12.5px] font-semibold">{fmtWeight(spool.remaining_weight, 0)}</span>}
    </div>
  )
}

function Note({ tone, children }: { tone: 'warn' | 'info'; children: ReactNode }) {
  const Icon = tone === 'warn' ? CircleAlert : Info
  return (
    <div className={`mb-2.5 flex items-start gap-2 rounded-lg px-3 py-2 last:mb-0 ${tone === 'warn' ? 'tone-warn' : 'tone-info'}`}>
      <Icon size={16} className="mt-px flex-none" />
      <div className="min-w-0 text-[12.5px] leading-snug">{children}</div>
    </div>
  )
}

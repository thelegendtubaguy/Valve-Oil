// Write a filament to the RFID tag on a Box slot's spool (VO_BOX_RFID_WRITE), so the Box sets the slot from it
// whenever the spool is loaded.
import { useMemo, useRef, useState } from 'react'
import { CircleAlert, CircleCheck, Hand, LoaderCircle, Plus, RotateCcw, X } from 'lucide-react'
import { Dialog } from './ui'
import { Spool } from './Spool'
import { toast } from './toast'
import { useStore } from '../lib/store'
import { boxCommands, filamentLists, type BoxState, type QidiBoxSlot } from '../state/box'
import { runGcode, usePrinter } from '../state/printer'
import { ColorSwatches, FilamentPicker } from './box/FilamentPicker'
import { BoxScene } from './box/BoxScene'
import { t } from '../i18n'
import { tr } from '../i18n/rich'

export type TagJob = {
  run: number
  running: boolean
  action?: string
  test?: boolean
  slot?: string
  seconds?: number
  elapsed?: number
  turn?: number
  turned?: number
  lost?: number
  filament?: string | null
  result?: { written: boolean; verified: boolean; layout: string; reason?: string } | null
  error?: string | null
}

/** Why a tag wasn't written, in the operator's terms. */
function refusal(layout: string, reason?: string) {
  const brands: Record<string, string> = { anycubic: 'Anycubic', elegoo: 'Elegoo', creality: 'Creality', tigertag: 'TigerTag', opentag3d: 'OpenTag3D' }
  if (brands[layout]) return t('box.tag.refusal_brand', { brand: brands[layout] })
  switch (layout) {
    case 'bambu':
      return t('box.tag.refusal_bambu')
    case 'locked':
      return t('box.tag.refusal_locked')
    case 'unsupported':
      return t('box.tag.refusal_unsupported')
    default:
      // The printer's reason, in English.
      return reason ? t('box.tag.refusal_reason', { reason }) : t('box.tag.refusal_unknown')
  }
}

export function TagWriteDialog({ box, slot, onClose }: { box: BoxState; slot: QidiBoxSlot; onClose: () => void }) {
  const lists = useStore(filamentLists, (l) => l)
  const database = useMemo(() => lists.database ?? [], [lists.database])
  const variables = usePrinter((s) => s.status.save_variables?.variables ?? {})
  const job = usePrinter((s) => s.status.tltg_box_rfid?.job as TagJob | undefined)

  // Start from the slot's own filament and colors.
  const savedId = slot.filamentId ?? String(variables[`filament_slot${slot.index}`] ?? '')
  const savedColors = useMemo(() => {
    const raw = variables[`color_slot${slot.index}`]
    return (Array.isArray(raw) ? raw : [raw, slot.color])
      .map((c) => String(c ?? '').replace(/^#/, '').toUpperCase())
      .filter((c) => /^[0-9A-F]{6}$/.test(c))
      .map((c) => `#${c}`)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  const initial = database.find((f) => f.id === savedId)
  const [id, setId] = useState(initial?.id ?? database[0]?.id ?? '')
  const [colors, setColors] = useState<string[]>(savedColors.length ? savedColors.slice(0, 4) : ['#FFFFFF'])
  const [picked, setPicked] = useState(0)

  // The run this dialog started, and how it ended.
  const [phase, setPhase] = useState<'form' | 'running' | 'done' | 'failed'>('form')
  const [message, setMessage] = useState('')
  const startRun = useRef(0)
  const closed = useRef(false)
  const mine = job && job.run > startRun.current && job.action === 'write' && !job.test ? job : undefined

  const filament = database.find((f) => f.id === id)
  // The Box turns a spool sitting on an empty slot's rollers; with filament fed, it's turned by hand.
  const turns = !slot.present && Number(variables[`slot${slot.index}`] ?? 0) === 0

  const setColor = (c: string) => setColors((cs) => cs.map((x, i) => (i === picked ? c.toUpperCase() : x)))

  const write = async () => {
    if (!filament) return
    startRun.current = job?.run ?? 0
    setPhase('running')
    try {
      await runGcode(boxCommands.writeTag(slot.index, filament.id, colors))
      setPhase('done')
      setMessage('')
      if (closed.current) toast(t('box.tag.written_toast', { slot: slot.label, filament: filament.name }))
    } catch (e) {
      setPhase('failed')
      setMessage(e instanceof Error ? e.message : String(e))
      if (closed.current) toast(t('box.tag.failed_toast', { slot: slot.label }), 'error')
    }
  }
  const close = () => {
    closed.current = true
    onClose()
  }

  const preview = { label: slot.label, present: true, color: colors[0] ?? null, reading: phase === 'running' }
  const title = t('box.tag.title', { slot: slot.label })

  if (phase !== 'form') {
    const result = mine?.result
    const failure = phase === 'failed' ? (result && !result.written ? refusal(result.layout, result.reason) : result && !result.verified ? t('box.tag.not_verified') : null) : null
    const notFound = phase === 'failed' && !result
    return (
      <Dialog
        open
        onClose={close}
        title={title}
        footer={
          phase === 'running' ? (
            <button className="btn" onClick={close}>
              {t('box.tag.hide')}
            </button>
          ) : phase === 'done' ? (
            <button className="btn btn-primary" onClick={close}>
              {t('common.done')}
            </button>
          ) : (
            <>
              <button className="btn" onClick={close}>
                {t('common.close')}
              </button>
              <button className="btn btn-primary" onClick={() => setPhase('form')}>
                {t('box.tag.try_again')}
              </button>
            </>
          )
        }
      >
        <div className="flex items-center gap-3.5">
          <Spool slot={preview} size={52} />
          <div className="min-w-0 flex-1">
            <div className="font-semibold">{filament?.name}</div>
            <ColorRow colors={colors} />
          </div>
        </div>
        {phase === 'running' && <Progress job={mine} turns={turns} />}
        {phase === 'done' && (
          <div className="tone-good mt-4 flex items-start gap-2.5 rounded-lg px-3 py-2.5">
            <CircleCheck size={17} className="mt-px flex-none" />
            <span className="text-ink">{tr('box.tag.done', { filament: filament?.name ?? '' }, { b: (x) => <b>{x}</b> })}</span>
          </div>
        )}
        {phase === 'failed' && (
          <div className="tone-bad mt-4 flex items-start gap-2.5 rounded-lg px-3 py-2.5">
            <CircleAlert size={17} className="mt-px flex-none" />
            <span className="text-ink">
              {notFound ? (
                tr('box.tag.not_found', {}, { b: (x) => <b>{x}</b> })
              ) : failure ? (
                tr('box.tag.not_written', { reason: failure }, { b: (x) => <b>{x}</b> })
              ) : (
                message
              )}
            </span>
          </div>
        )}
      </Dialog>
    )
  }

  return (
    <Dialog
      open
      onClose={close}
      title={title}
      footer={
        <>
          <button className="btn" onClick={close}>
            {t('common.cancel')}
          </button>
          <button className="btn btn-primary" disabled={!filament || !box.canWriteTag} onClick={write}>
            {t('box.tag.write')}
          </button>
        </>
      }
    >
      <div className="grid gap-3.5">
        <BoxScene slots={box.slots} slot={slot} colors={colors} />
        <div className="text-ink-2">{t('box.tag.intro')}</div>
        <FilamentPicker database={database} value={filament?.id ?? null} onChange={setId} />
        <div className="grid gap-1.5">
          <span className="section-label">{t('box.tag.colors')}</span>
          <div className="flex flex-wrap items-center gap-1.5">
            {colors.map((c, i) => (
              <span key={i} className="relative">
                <button
                  type="button"
                  aria-label={t('box.tag.color_n', { n: i + 1 })}
                  onClick={() => setPicked(i)}
                  className="h-8 w-8 rounded-full border-0"
                  style={{ background: c, boxShadow: picked === i ? '0 0 0 2px var(--surface), 0 0 0 4px var(--accent)' : 'inset 0 0 0 1px oklch(0 0 0 / .25)' }}
                />
                {colors.length > 1 && picked === i && (
                  <button
                    type="button"
                    aria-label={t('box.tag.remove_color', { n: i + 1 })}
                    className="absolute -top-1.5 -right-1.5 grid h-4 w-4 place-items-center rounded-full border-0 bg-surface text-ink-2"
                    style={{ boxShadow: 'var(--shadow-overlay)' }}
                    onClick={() => {
                      setColors((cs) => cs.filter((_, j) => j !== i))
                      setPicked(0)
                    }}
                  >
                    <X size={10} />
                  </button>
                )}
              </span>
            ))}
            {colors.length < 4 && (
              <button
                type="button"
                className="grid h-8 w-8 place-items-center rounded-full border border-dashed border-line-strong bg-transparent text-ink-3"
                aria-label={t('box.tag.add_color')}
                onClick={() => {
                  setColors((cs) => [...cs, cs[cs.length - 1]])
                  setPicked(colors.length)
                }}
              >
                <Plus size={14} />
              </button>
            )}
          </div>
          <ColorSwatches value={colors[picked] ?? null} onChange={setColor} size={26} />
        </div>
        <div className="tone-muted flex items-start gap-2.5 rounded-lg px-3 py-2.5">
          {turns ? <RotateCcw size={16} className="mt-0.5 flex-none" /> : <Hand size={16} className="mt-0.5 flex-none" />}
          <span className="text-ink-2">
            {turns ? t('box.tag.turns_hint') : t('box.tag.hand_hint')}
          </span>
        </div>
      </div>
    </Dialog>
  )
}

function ColorRow({ colors }: { colors: string[] }) {
  return (
    <div className="mt-1 flex items-center gap-1">
      {colors.map((c, i) => (
        <span key={i} className="inline-block h-3.5 w-3.5 rounded-full" style={{ background: c, boxShadow: 'inset 0 0 0 1px oklch(0 0 0 / .25)' }} title={c} />
      ))}
      <span className="num ml-1 text-[12.5px] text-ink-3">{colors.join(' · ')}</span>
    </div>
  )
}

function Progress({ job, turns }: { job: TagJob | undefined; turns: boolean }) {
  const turn = Math.abs(job?.turn ?? 0)
  const pct = turn ? Math.min(1, (job?.turned ?? 0) / turn) : job?.seconds ? Math.min(1, (job.elapsed ?? 0) / job.seconds) : 0
  const left = job?.seconds != null ? Math.max(0, Math.round(job.seconds - (job.elapsed ?? 0))) : null
  return (
    <div className="mt-4 grid gap-2">
      <div className="flex items-center gap-2">
        <LoaderCircle size={15} className="spin flex-none" />
        <span className="shimmer">{turns ? t('box.tag.turning') : t('box.tag.by_hand')}</span>
        <span className="num ml-auto text-ink-3">{turns ? t('box.tag.turned', { turned: Math.round(job?.turned ?? 0), total: turn || '—' }) : left != null ? t('box.tag.left', { s: left }) : ''}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-inset">
        <div className="h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${pct * 100}%` }} />
      </div>
      {(job?.lost ?? 0) > 0 && <div className="text-[12.5px] text-ink-3">{t('box.tag.lost')}</div>}
    </div>
  )
}

export function WriteTagIcon({ size = 15 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M6 8.32a7.43 7.43 0 0 1 0 7.36" />
      <path d="M9.46 6.21a11.76 11.76 0 0 1 0 11.58" />
      <path d="m20.5 12.5-6.6 6.6-3 .9.9-3 6.6-6.6a1.5 1.5 0 0 1 2.1 2.1Z" />
    </svg>
  )
}


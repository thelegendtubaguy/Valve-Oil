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
  if (brands[layout]) return `It holds ${brands[layout]} data, which the Box already reads. Valve Oil writes only blank tags and tags it wrote before.`
  switch (layout) {
    case 'bambu':
      return 'It’s a Bambu Lab tag, which the Box already reads. Bambu tags can’t be rewritten.'
    case 'locked':
      return 'The tag is locked against writing.'
    case 'unsupported':
      return 'The Box can’t write this kind of tag. Use a MIFARE Classic 1K or NTAG213, 215, or 216 tag.'
    default:
      return reason ? `The tag ${reason}.` : 'The tag holds data Valve Oil doesn’t write over.'
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
      if (closed.current) toast(`${slot.label}’s spool tag now holds ${filament.name}.`)
    } catch (e) {
      setPhase('failed')
      setMessage(e instanceof Error ? e.message : String(e))
      if (closed.current) toast(`${slot.label}’s spool tag wasn’t written.`, 'error')
    }
  }
  const close = () => {
    closed.current = true
    onClose()
  }

  const preview = { label: slot.label, present: true, color: colors[0] ?? null, reading: phase === 'running' }
  const title = `Write ${slot.label}’s spool tag`

  if (phase !== 'form') {
    const result = mine?.result
    const failure = phase === 'failed' ? (result && !result.written ? refusal(result.layout, result.reason) : result && !result.verified ? 'The tag didn’t read back as written; it may have moved away mid-write.' : null) : null
    const notFound = phase === 'failed' && !result
    return (
      <Dialog
        open
        onClose={close}
        title={title}
        footer={
          phase === 'running' ? (
            <button className="btn" onClick={close}>
              Hide
            </button>
          ) : phase === 'done' ? (
            <button className="btn btn-primary" onClick={close}>
              Done
            </button>
          ) : (
            <>
              <button className="btn" onClick={close}>
                Close
              </button>
              <button className="btn btn-primary" onClick={() => setPhase('form')}>
                Try again
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
            <span className="text-ink">
              <b>Tag written and checked.</b> Whenever this spool is loaded into a Box slot, the slot is set to {filament?.name}.
            </span>
          </div>
        )}
        {phase === 'failed' && (
          <div className="tone-bad mt-4 flex items-start gap-2.5 rounded-lg px-3 py-2.5">
            <CircleAlert size={17} className="mt-px flex-none" />
            <span className="text-ink">
              {notFound ? (
                <>
                  <b>No tag came past the reader.</b> The Box reads a tag only on the side of the spool facing the reader. If the tag is on the other side, turn the spool over and try again.
                </>
              ) : failure ? (
                <>
                  <b>Tag not written.</b> {failure}
                </>
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
            Cancel
          </button>
          <button className="btn btn-primary" disabled={!filament || !box.canWriteTag} onClick={write}>
            Write tag
          </button>
        </>
      }
    >
      <div className="grid gap-3.5">
        <BoxScene slots={box.slots} slot={slot} colors={colors} />
        <div className="text-ink-2">Put what’s on this spool on its RFID tag. The Box then sets the slot from the tag whenever the spool is loaded, here or in any Box slot.</div>
        <FilamentPicker database={database} value={filament?.id ?? null} onChange={setId} />
        <div className="grid gap-1.5">
          <span className="section-label">Colors · up to four for a multicolor spool</span>
          <div className="flex flex-wrap items-center gap-1.5">
            {colors.map((c, i) => (
              <span key={i} className="relative">
                <button
                  type="button"
                  aria-label={`Color ${i + 1}`}
                  onClick={() => setPicked(i)}
                  className="h-8 w-8 rounded-full border-0"
                  style={{ background: c, boxShadow: picked === i ? '0 0 0 2px var(--surface), 0 0 0 4px var(--accent)' : 'inset 0 0 0 1px oklch(0 0 0 / .25)' }}
                />
                {colors.length > 1 && picked === i && (
                  <button
                    type="button"
                    aria-label={`Remove color ${i + 1}`}
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
                aria-label="Add a color"
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
            {turns
              ? 'The Box turns the spool backward until its tag comes past the reader, about one turn at most.'
              : 'Filament is fed from this slot, so the Box can’t turn the spool. Once you start, turn it slowly backward by hand for up to a minute.'}{' '}
            The tag must be on the side of the spool facing the reader. Only blank tags and tags Valve Oil wrote before are written.
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
        <span className="shimmer">{turns ? 'Turning the spool to find its tag…' : 'Turn the spool slowly backward by hand…'}</span>
        <span className="num ml-auto text-ink-3">{turns ? `${Math.round(job?.turned ?? 0)} of ${turn || '—'} mm` : left != null ? `${left} s left` : ''}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-inset">
        <div className="h-full rounded-full bg-accent transition-[width] duration-300" style={{ width: `${pct * 100}%` }} />
      </div>
      {(job?.lost ?? 0) > 0 && <div className="text-[12.5px] text-ink-3">The tag moved away before it could be written; trying again as it comes past.</div>}
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


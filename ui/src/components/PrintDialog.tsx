import { useEffect, useMemo, useState } from 'react'
import { Clock, Layers, Play, Trash2, TriangleAlert } from 'lucide-react'
import { Dialog, HoldButton, Toggle } from './ui'
import { attempt, toast } from './toast'
import { Spool } from './Spool'
import { client, runGcode } from '../state/printer'
import { fetchMeta, thumbnailUrl, type FileMeta } from '../state/files'
import { boxCommands, filamentLists, loadFilamentLists, useBox, QIDI_BOX_EXTERNAL_SLOT } from '../state/box'
import { matchQidiBoxSlots, qidiBoxFileTools, qidiBoxSlotsForTool, normalizeQidiColor, qidiTextColor, type QidiBoxFileMetadata } from '../qidi/qidi-box'
import { displayName, fmtBytes, fmtDuration, fmtAgo } from '../lib/format'
import { useJob, isActive } from '../state/job'

// Upstream Moonraker reports filament_type as one ';'-separated string; the Box matcher wants one entry per filament.
function boxMetadata(meta: FileMeta): QidiBoxFileMetadata {
  const split = (v: unknown) => (typeof v === 'string' ? v.split(';').map((x) => x.trim()) : Array.isArray(v) ? v.map(String) : undefined)
  return { ...(meta as QidiBoxFileMetadata), filament_type: split(meta.filament_type) }
}

// Files already rescanned for the filaments they use, so a file Moonraker still can't read is rescanned once.
const rescanned = new Set<string>()

/** File details and Start print. With a QIDI Box connected, each filament in the file is matched to a loaded slot first. */
export function PrintDialog({ filename, onClose }: { filename: string; onClose: () => void }) {
  const [meta, setMeta] = useState<FileMeta | null>(null)
  const box = useBox()
  const job = useJob()
  const hasBox = box.boxCount > 0
  const [mapping, setMapping] = useState<Record<number, number | null>>({})
  const [starting, setStarting] = useState(false)

  useEffect(() => {
    let live = true
    fetchMeta(filename, true).then(async (m) => {
      if (!live) return
      setMeta(m)
      // Older metadata may not name the tools a file uses; rescan once so QIDI's Moonraker fills them in.
      if (hasBox && m && !qidiBoxFileTools(boxMetadata(m)).named && !rescanned.has(filename)) {
        rescanned.add(filename)
        try {
          const r = await client.call<FileMeta>('server.files.metascan', { filename })
          if (live && r) setMeta(r)
        } catch {}
      }
    })
    return () => {
      live = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filename])

  const fileTools = useMemo(() => (meta ? qidiBoxFileTools(boxMetadata(meta)) : null), [meta])
  // Slots on firmware before 01.01.07.01 resolve through QIDI's filament list, so it's read before matching.
  const [listsReady, setListsReady] = useState(() => filamentLists.get().legacy != null || filamentLists.get().database != null)
  useEffect(() => {
    if (hasBox && !listsReady) loadFilamentLists().finally(() => setListsReady(true))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  // Match once per file; the user's picks win after that.
  useEffect(() => {
    if (fileTools && hasBox && listsReady) setMapping(matchQidiBoxSlots(fileTools.tools, box.slots))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fileTools, listsReady])

  const useBoxForPrint = box.enabled
  const missing = fileTools ? fileTools.tools.filter((t) => mapping[t.tool] == null) : []
  const mappingOk = !hasBox || !useBoxForPrint || !fileTools || (fileTools.named ? missing.length === 0 : missing.length < fileTools.tools.length)
  const busy = isActive(job.state)

  const start = async () => {
    setStarting(true)
    try {
      if (hasBox && useBoxForPrint && fileTools) {
        const lines = fileTools.tools.filter((t) => mapping[t.tool] != null).map((t) => boxCommands.mapTool(t.tool, mapping[t.tool]!))
        if (lines.length) await runGcode(lines.join('\n'))
      }
      await client.call('printer.print.start', { filename })
      toast(`Printing ${displayName(filename)}`)
      location.hash = '#/home'
      onClose()
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), 'error')
    }
    setStarting(false)
  }

  const remove = async () => {
    await attempt(() => client.call('server.files.delete_file', { path: `gcodes/${filename}` }))
    onClose()
  }

  const thumb = thumbnailUrl(filename, meta, 200)
  return (
    <Dialog
      open
      onClose={onClose}
      title={<span className="block truncate">{displayName(filename)}</span>}
      footer={
        <>
          <HoldButton className="mr-auto" onHold={remove} disabled={busy && job.filename === filename}>
            <Trash2 size={14} /> Hold to delete
          </HoldButton>
          <button className="btn" onClick={onClose}>
            Close
          </button>
          <button className="btn btn-primary" disabled={busy || !mappingOk || starting || !meta} onClick={start}>
            <Play size={14} /> Start print
          </button>
        </>
      }
    >
      <div className="flex gap-4">
        <div className="grid h-[120px] w-[150px] flex-none place-items-center overflow-hidden rounded-xl bg-inset shadow-[inset_0_0_0_1px_var(--line)]">
          {thumb ? <img src={thumb} alt="" className="max-h-full max-w-full object-contain" /> : <Layers size={28} className="text-ink-3" />}
        </div>
        <div className="grid min-w-0 content-start gap-1.5">
          <div className="flex flex-wrap gap-1.5">
            {meta?.estimated_time != null && (
              <span className="chip">
                <Clock size={12} /> {fmtDuration(meta.estimated_time)}
              </span>
            )}
            {meta?.layer_height != null && <span className="chip">{meta.layer_height} mm layers</span>}
            {meta?.filament_weight_total != null && <span className="chip">{Math.round(meta.filament_weight_total)} g</span>}
          </div>
          <div className="text-ink-3">
            {[meta?.slicer, meta?.size != null ? fmtBytes(meta.size) : null, meta?.modified ? `modified ${fmtAgo(meta.modified)}` : null].filter(Boolean).join(' · ')}
          </div>
          {busy && <div className="text-warn">A print is running. Start this one after it finishes.</div>}
        </div>
      </div>

      {hasBox && fileTools && (
        <div className="mt-4 grid gap-3">
          <div className="flex items-center gap-3 rounded-xl bg-inset px-3.5 py-2.5 shadow-[inset_0_0_0_1px_var(--line)]">
            <div className="min-w-0 flex-1">
              <div className="font-semibold">Use QIDI Box</div>
              <div className="text-[12.5px] text-ink-3">{useBoxForPrint ? 'Each filament prints from the Box slot chosen below.' : 'Prints from the external spool.'}</div>
            </div>
            <Toggle checked={useBoxForPrint} label="Use QIDI Box" onChange={(v) => attempt(() => runGcode(boxCommands.setting('enable_box', v)))} />
          </div>
          {useBoxForPrint && fileTools.tools.length > 0 && <div className="text-ink-2">Choose the Box slot for each filament in the file. Only loaded slots of the same filament type are listed; slots are matched by nearest color.</div>}
          {useBoxForPrint && !fileTools.named && (
            <div className="text-ink-2">This file doesn’t say which filaments it uses, so every filament in its slicer project is listed. Any left without a slot keep the slot they were last mapped to.</div>
          )}
          {fileTools.tools.map((t) => {
            const options = qidiBoxSlotsForTool(t, box.slots)
            const color = normalizeQidiColor(t.color)
            return (
              <div key={t.tool} className="grid gap-2 rounded-xl px-3.5 py-2.5 shadow-[inset_0_0_0_1px_var(--line)]">
                <div className="flex items-center gap-2.5">
                  <span className="inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[12.5px] font-bold" style={{ background: color ?? 'var(--inset)', color: qidiTextColor(color), boxShadow: 'inset 0 0 0 1px rgb(128 128 128 / 0.4)' }}>
                    T{t.tool} · {t.type || '?'}
                  </span>
                  <span className="flex-1" />
                  {!useBoxForPrint && <span className="text-ink-3">External spool</span>}
                </div>
                {useBoxForPrint &&
                  (options.length ? (
                    <div className="flex flex-wrap gap-1.5">
                      {options.map((s) => (
                        <button key={s.index} type="button" className={`choice pl-1.5! ${mapping[t.tool] === s.index ? 'on' : ''}`} aria-pressed={mapping[t.tool] === s.index} onClick={() => setMapping((m) => ({ ...m, [t.tool]: m[t.tool] === s.index ? null : s.index }))} title={[s.vendor, s.name].filter(Boolean).join(' ')}>
                          <Spool slot={s} size={20} label={false} />
                          {s.label}
                          <span className="font-medium text-ink-3">{s.name ?? s.type}</span>
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="text-[12.5px] text-ink-3">No {t.type || 'matching'} filament is loaded in the Box.</div>
                  ))}
              </div>
            )
          })}
          {useBoxForPrint && !mappingOk && (
            <div className="tone-warn flex items-center gap-2 rounded-lg px-3 py-2 text-ink">
              <TriangleAlert size={15} className="flex-none" /> Choose a Box slot for every filament, or turn off the QIDI Box to print from the external spool.
            </div>
          )}
          {!useBoxForPrint && box.toolheadFilament === false && (
            <div className="tone-warn flex items-center gap-2 rounded-lg px-3 py-2 text-ink">
              <TriangleAlert size={15} className="flex-none" /> No filament is detected at the toolhead. Load the external spool before printing.
            </div>
          )}
          {useBoxForPrint && box.slots.every((s) => s.index === QIDI_BOX_EXTERNAL_SLOT || !s.present) && <div className="text-ink-3">No Box slot has filament loaded.</div>}
        </div>
      )}
    </Dialog>
  )
}

import { useEffect, useMemo, useState, type CSSProperties } from 'react'
import { Box as BoxIcon, Camera, ChevronRight, Folder, History, Layers, Pause, Play, RotateCcw, X } from 'lucide-react'
import { Card, Dialog, HoldButton } from '../components/ui'
import { BoxStrip } from '../components/BoxStrip'
import { FileRow } from '../components/FileCard'
import { DropZone } from '../components/Upload'
import { PrintDialog } from '../components/PrintDialog'
import { TempChart } from '../components/TempChart'
import { MiniConsole } from '../components/MiniConsole'
import { Health, JobDetails, Thermals } from '../components/Panels'
import { Motion } from '../components/Motion'
import { HeaterDialog, Operation, SlotActionButtons, ToolMapDialog } from './Box'
import { Spool, slotTitle } from '../components/Spool'
import { attempt } from '../components/toast'
import { client, onKlippyReady, runGcode, usePrinter } from '../state/printer'
import { slotByIndex, useBox, useBoxPresent } from '../state/box'
import { jobProgress, remainingSeconds, useJob } from '../state/job'
import { thumbnailUrl, useFileMeta, useFiles, useHistory, useHistoryTotals, type HistoryJob } from '../state/files'
import { displayName, fmtAgo, fmtDuration, fmtEta } from '../lib/format'
import { normalizeQidiColor } from '../qidi/qidi-box'
import { href } from '../lib/router'

const area = (name: string): CSSProperties => ({ gridArea: name })

/**
 * Home is a cockpit: on a desktop it fills the screen in three columns (the job and heaters, the camera and
 * temperatures, the Box and console); on a tablet it's two columns and on a phone one.
 */
export function HomeScreen() {
  const job = useJob()
  const meta = useFileMeta(job.filename || null)
  const showJob = !!job.filename && job.state !== 'standby'
  const boxPresent = useBoxPresent()
  const camera = useWebcam()
  const jobs = useHistory(500)
  return (
    <div className="cockpit">
      <div className="col">
        {showJob ? <JobCard style={area('job')} /> : <ReadyCard style={area('job')} jobs={jobs} />}
        <div style={area('thermals')}>
          <Thermals className="h-full" />
        </div>
        <RecentFiles jobs={jobs} />
      </div>
      <div className="col">
        {camera && <CameraCard cam={camera} />}
        <div style={area('chart')} className={camera ? 'h-[300px] wide:h-[290px] wide:flex-none' : 'grow h-[300px]'}>
          <TempChart className="h-full" />
        </div>
        <div className="strip">
          <div style={area('motion')}>
            <Motion className="h-full" />
          </div>
          <div style={area('details')}>{showJob ? <JobDetails job={job} meta={meta} className="h-full" /> : <HistoryCard jobs={jobs} />}</div>
        </div>
      </div>
      <div className="col">
        {boxPresent && <BoxCard />}
        <div style={area('health')}>
          <Health className="h-full" />
        </div>
        <div style={area('console')} className="grow">
          <MiniConsole className="h-full" />
        </div>
      </div>
    </div>
  )
}

// ---- the current or last job ----
function JobCard({ style }: { style?: CSSProperties }) {
  const job = useJob()
  const meta = useFileMeta(job.filename)
  const [tune, setTune] = useState(false)
  const p = jobProgress(job)
  const remaining = remainingSeconds(job, meta)
  const thumb = thumbnailUrl(job.filename, meta, 200)
  const colors = (meta?.filament_colors ?? []).map(normalizeQidiColor)
  const types = (meta?.filament_type ?? '').split(';').map((t) => t.trim())
  const finished = job.state === 'complete' || job.state === 'cancelled' || job.state === 'error'
  const statusText = { printing: 'Now printing', paused: 'Paused', complete: 'Finished', cancelled: 'Cancelled', error: 'Stopped with an error', standby: '' }[job.state]
  const fill = finished ? (job.state === 'complete' ? 1 : p) : p

  return (
    <section className="card grid content-start gap-3.5" style={style}>
      <div className="flex gap-3.5">
        <div className="relative grid h-[104px] w-[104px] flex-none place-items-center overflow-hidden rounded-xl bg-inset shadow-[inset_0_0_0_1px_var(--line)]">
          {thumb ? (
            <>
              <img src={thumb} alt="" className="max-h-[90%] max-w-[90%] object-contain opacity-35 grayscale" />
              {/* The printed part of the thumbnail fills in from the bottom as the print progresses. */}
              <img src={thumb} alt="" className="absolute max-h-[90%] max-w-[90%] object-contain transition-[clip-path] duration-700" style={{ clipPath: `inset(${(1 - fill) * 100}% 0 0 0)` }} />
            </>
          ) : (
            <Layers size={28} className="text-ink-3" />
          )}
        </div>
        <div className="grid min-w-0 content-start gap-1">
          <div className={`lbl ${job.state === 'paused' ? 'text-warn!' : job.state === 'error' ? 'text-bad!' : ''}`}>{statusText}</div>
          <div className="line-clamp-2 text-[15px] leading-snug font-semibold break-all" title={job.filename}>
            {displayName(job.filename)}
          </div>
          <div className="mt-0.5 flex flex-wrap gap-1">
            {colors.map((c, i) =>
              types[i] || c ? (
                <span key={i} className="chip">
                  {c && <span className="swatch" style={{ background: c }} />}
                  {types[i] || `T${i}`}
                </span>
              ) : null,
            )}
          </div>
        </div>
      </div>
      {job.message && job.state !== 'printing' && <div className={`rounded-lg px-3 py-2 text-ink ${job.state === 'error' ? 'tone-bad' : 'tone-warn'}`}>{job.message}</div>}
      {!finished ? (
        <>
          <div className="flex items-end justify-between gap-3">
            <div className="big num text-[38px]!">
              {Math.floor(p * 100)}
              <span className="text-[18px] text-ink-3">%</span>
            </div>
            {job.totalLayers != null && (
              <div className="num pb-1 text-right text-ink-2">
                <Layers size={12} className="mr-1 inline" />
                {job.currentLayer ?? 0} / {job.totalLayers}
              </div>
            )}
          </div>
          <div className={`bar -mt-1.5 ${job.state === 'printing' ? 'live' : ''}`}>
            <i style={{ width: `${p * 100}%` }} />
          </div>
          <div className="grid grid-cols-3 gap-2">
            <Stat label="Remaining" value={fmtDuration(remaining)} />
            <Stat label="Done at" value={fmtEta(remaining)} />
            <Stat label="Elapsed" value={fmtDuration(job.totalDuration)} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            {job.state === 'paused' ? (
              <button className="btn btn-primary btn-lg" onClick={() => attempt(() => client.call('printer.print.resume'))}>
                <Play size={14} /> Resume
              </button>
            ) : (
              <button className="btn btn-lg" onClick={() => attempt(() => client.call('printer.print.pause'))}>
                <Pause size={14} /> Pause
              </button>
            )}
            <HoldButton className="btn-lg" onHold={() => attempt(() => client.call('printer.print.cancel'))}>
              <X size={14} /> Hold to cancel
            </HoldButton>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button className="chip cursor-pointer border-0 hover:bg-hover" onClick={() => setTune(true)}>
              Speed {Math.round(job.speedFactor * 100)}%
            </button>
            <button className="chip cursor-pointer border-0 hover:bg-hover" onClick={() => setTune(true)}>
              Flow {Math.round(job.extrudeFactor * 100)}%
            </button>
            <button className="chip num cursor-pointer border-0 hover:bg-hover" onClick={() => setTune(true)}>
              Z {job.zOffset >= 0 ? '+' : ''}
              {job.zOffset.toFixed(3)}
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="text-ink-2">
            {fmtDuration(job.printDuration)} printing{job.filamentUsed > 0 ? ` · ${(job.filamentUsed / 1000).toFixed(1)} m of filament` : ''}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <ReprintButton filename={job.filename} />
            <button className="btn" onClick={() => attempt(() => runGcode('SDCARD_RESET_FILE'))}>
              Done
            </button>
          </div>
        </>
      )}
      {tune && <TuneDialog onClose={() => setTune(false)} />}
    </section>
  )
}

// ---- between prints ----
function ReadyCard({ style, jobs }: { style?: CSSProperties; jobs: HistoryJob[] | null }) {
  const last = jobs?.[0]
  return (
    <section className="card grid content-start gap-3" style={style}>
      <div>
        <div className="lbl">Ready to print</div>
        {last ? (
          <div className="mt-1 flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <div className="truncate font-semibold" title={last.filename}>
                {displayName(last.filename)}
              </div>
              <div className="text-[12.5px] text-ink-3">
                Last print, {last.status === 'completed' ? 'finished' : last.status === 'cancelled' ? 'cancelled' : 'failed'} {last.end_time ? fmtAgo(last.end_time) : ''}
              </div>
            </div>
            <ReprintButton filename={last.filename} small />
          </div>
        ) : (
          <div className="mt-1 text-ink-2">Upload a file or send one from your slicer.</div>
        )}
      </div>
      <DropZone className="py-3!" />
      <QuickActions bare />
    </section>
  )
}

function RecentFiles({ jobs }: { jobs: HistoryJob[] | null }) {
  const files = useFiles()
  const [open, setOpen] = useState<string | null>(null)
  const byFile = useMemo(() => {
    const m = new Map<string, { last: HistoryJob; count: number }>()
    for (const j of jobs ?? []) {
      const e = m.get(j.filename)
      if (!e) m.set(j.filename, { last: j, count: j.status === 'completed' ? 1 : 0 })
      else if (j.status === 'completed') e.count++
    }
    return m
  }, [jobs])
  const recent = useMemo(() => {
    if (!files) return []
    const lastUsed = (p: string, mod: number) => Math.max(byFile.get(p)?.last.start_time ?? 0, mod)
    return [...files].sort((a, b) => lastUsed(b.path, b.modified) - lastUsed(a.path, a.modified)).slice(0, 20)
  }, [files, byFile])
  return (
    <section className="card grow" style={area('files')}>
      <h3 className="card-title">
        <Folder size={14} /> Recent files
        <a href={href('files')} className="aside inline-flex items-center gap-1">
          All files <ChevronRight size={13} />
        </a>
      </h3>
      <div className="grow-body -mx-1.5 max-wide:max-h-[340px] max-wide:overflow-auto">
        {recent.map((f) => (
          <FileRow key={f.path} path={f.path} last={byFile.get(f.path)?.last} count={byFile.get(f.path)?.count ?? 0} onOpen={() => setOpen(f.path)} />
        ))}
        {files && files.length === 0 && <div className="px-1.5 text-ink-3">No files yet.</div>}
      </div>
      {open && <PrintDialog filename={open} onClose={() => setOpen(null)} />}
    </section>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="lbl">{label}</div>
      <div className="num text-[15px] font-semibold">{value}</div>
    </div>
  )
}

function ReprintButton({ filename, small }: { filename: string; small?: boolean }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button className={`btn ${small ? 'btn-sm' : 'btn-primary'}`} onClick={() => setOpen(true)}>
        <RotateCcw size={small ? 13 : 14} /> Print again
      </button>
      {open && <PrintDialog filename={filename} onClose={() => setOpen(false)} />}
    </>
  )
}

function TuneDialog({ onClose }: { onClose: () => void }) {
  const job = useJob()
  const row = (label: string, value: string, buttons: [string, string][]) => (
    <div className="flex flex-wrap items-center gap-2 border-b border-line py-2.5">
      <span className="w-24 font-medium">{label}</span>
      <span className="num w-16 font-semibold">{value}</span>
      <div className="flex flex-wrap gap-1.5">
        {buttons.map(([text, g]) => (
          <button key={text} className="btn btn-sm" onClick={() => attempt(() => runGcode(g))}>
            {text}
          </button>
        ))}
      </div>
    </div>
  )
  const sp = Math.round(job.speedFactor * 100)
  const fl = Math.round(job.extrudeFactor * 100)
  return (
    <Dialog open onClose={onClose} title="Tune the print">
      {row('Speed', `${sp}%`, [
        ['−10', `M220 S${Math.max(10, sp - 10)}`],
        ['100%', 'M220 S100'],
        ['+10', `M220 S${sp + 10}`],
      ])}
      {row('Flow', `${fl}%`, [
        ['−1', `M221 S${Math.max(50, fl - 1)}`],
        ['100%', 'M221 S100'],
        ['+1', `M221 S${fl + 1}`],
      ])}
      {row('Z offset', `${job.zOffset >= 0 ? '+' : ''}${job.zOffset.toFixed(3)}`, [
        ['−0.01', 'SET_GCODE_OFFSET Z_ADJUST=-0.01 MOVE=1'],
        ['+0.01', 'SET_GCODE_OFFSET Z_ADJUST=0.01 MOVE=1'],
        ['−0.05', 'SET_GCODE_OFFSET Z_ADJUST=-0.05 MOVE=1'],
        ['+0.05', 'SET_GCODE_OFFSET Z_ADJUST=0.05 MOVE=1'],
      ])}
      <div className="mt-3 text-[12.5px] text-ink-3">Z offset changes apply to this print only.</div>
    </Dialog>
  )
}

// ---- camera ----
type Webcam = { name: string; enabled: boolean; service: string; stream_url: string; snapshot_url: string; flip_horizontal: boolean; flip_vertical: boolean; rotation: number }

function useWebcam(): Webcam | null {
  const [cam, setCam] = useState<Webcam | null>(null)
  const ready = usePrinter((s) => s.conn === 'open')
  useEffect(() => {
    if (!ready) return
    const load = () =>
      client
        .call<{ webcams: Webcam[] }>('server.webcams.list')
        .then((r) => setCam(r.webcams.find((w) => w.enabled !== false && /mjpeg|uv4l|ipstream|stream/i.test(w.service + w.stream_url)) ?? null))
        .catch(() => setCam(null))
    load()
    const off = onKlippyReady(load)
    return () => {
      off()
    }
  }, [ready])
  return cam
}

function CameraCard({ cam }: { cam: Webcam }) {
  const [failed, setFailed] = useState(false)
  const [full, setFull] = useState(false)
  const src = new URL(cam.stream_url, client.httpBase + '/').toString()
  const transform = [cam.flip_horizontal ? 'scaleX(-1)' : '', cam.flip_vertical ? 'scaleY(-1)' : '', cam.rotation ? `rotate(${cam.rotation}deg)` : ''].join(' ')
  return (
    <section className="card grow p-2.5!" style={area('cam')}>
      <div
        className={`relative overflow-hidden bg-[#14161b] ${full ? 'fixed inset-0 z-80 rounded-none' : 'aspect-video rounded-[10px] wide:aspect-auto wide:min-h-0 wide:flex-1'}`}
        onDoubleClick={() => setFull((f) => !f)}
      >
        {!failed ? (
          <img src={src} alt={cam.name} className="absolute inset-0 h-full w-full object-contain" style={{ transform }} onError={() => setFailed(true)} />
        ) : (
          <div className="grid h-full place-items-center text-[#9aa1ad]">
            <span className="flex items-center gap-2">
              <Camera size={16} /> Camera unavailable
            </span>
          </div>
        )}
        <div className="absolute top-2.5 left-2.5 flex items-center gap-1.5 rounded-full bg-black/55 px-2 py-0.5 text-[11px] font-semibold text-white backdrop-blur">
          <i className="h-1.5 w-1.5 rounded-full bg-[#ff4d4f]" style={{ animation: 'vo-blink 1.4s infinite' }} />
          {cam.name}
        </div>
        <button className="absolute right-2.5 bottom-2.5 rounded-md border-0 bg-black/50 px-2 py-0.5 text-[11px] text-white" onClick={() => setFull((f) => !f)}>
          {full ? 'Close' : 'Full screen'}
        </button>
      </div>
    </section>
  )
}

function BoxCard() {
  const box = useBox()
  const [selected, setSelected] = useState<number | null>(null)
  const [heater, setHeater] = useState<number | null>(null)
  const [mapOpen, setMapOpen] = useState(false)
  // Filament stuck between the hub and the toolhead selects its slot, as on the Box page.
  useEffect(() => {
    if (box.strandedSlot != null) setSelected(box.strandedSlot)
  }, [box.strandedSlot])
  const loaded = box.slots.find((s) => s.index === box.loadedSlot)
  const slot = slotByIndex(box, selected)
  return (
    <section className="card" style={area('box')}>
      <h3 className="card-title">
        <BoxIcon size={14} />
        <a href={href('box')}>QIDI Box</a>
        <span className="aside flex items-center gap-1.5">
          <span className="text-ink-3">{loaded ? `${loaded.label} loaded` : 'Nothing loaded'}</span>
          <button className="btn btn-sm px-2! py-0.5! text-[11.5px]" disabled={box.mappingLocked} onClick={() => setMapOpen(true)} title={box.mappingLocked ? 'Tool mapping can be changed while the printer is idle' : 'Change which slot each tool loads'}>
            Tools
          </button>
          <a href={href('box')} className="iconbtn h-6 w-6" title="Open the QIDI Box page" aria-label="Open the QIDI Box page">
            <ChevronRight size={14} />
          </a>
        </span>
      </h3>
      {box.task && (
        <div className="mb-2.5">
          <Operation box={box} bare />
        </div>
      )}
      <BoxStrip box={box} size={38} compact selected={selected} onSelect={(i) => setSelected((s) => (s === i ? null : i))} onClimate={setHeater} />
      {slot ? (
        <div className="mt-2.5 rounded-[10px] bg-inset p-2.5 shadow-[inset_0_0_0_1px_var(--line)]">
          <div className="mb-2 flex items-center gap-2">
            <Spool slot={slot} size={22} label={false} />
            <span className="min-w-0 flex-1 truncate">
              <b>{slot.label}</b> <span className="text-ink-2">{slotTitle(slot)}</span>
            </span>
            <button className="iconbtn h-6 w-6" onClick={() => setSelected(null)} aria-label="Close">
              <X size={14} />
            </button>
          </div>
          <SlotActionButtons box={box} slot={slot} withTagButtons />
        </div>
      ) : (
        <div className="mt-1.5 text-[12px] text-ink-3">Click a slot for its actions, or a Box’s temperature for its heater.</div>
      )}
      {heater != null && <HeaterDialog box={box} boxNumber={heater} onClose={() => setHeater(null)} />}
      <ToolMapDialog box={box} open={mapOpen} onClose={() => setMapOpen(false)} />
    </section>
  )
}

function HistoryCard({ jobs }: { jobs: HistoryJob[] | null }) {
  const totals = useHistoryTotals()
  const weeks = 20
  const cells = useMemo(() => {
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const start = new Date(today)
    start.setDate(start.getDate() - (weeks * 7 - 1) - today.getDay())
    const days = new Map<number, { ok: number; bad: number }>()
    for (const j of jobs ?? []) {
      const d = new Date(j.start_time * 1000)
      d.setHours(0, 0, 0, 0)
      const k = d.getTime()
      const e = days.get(k) ?? { ok: 0, bad: 0 }
      if (j.status === 'completed') e.ok++
      else if (j.status !== 'cancelled' && j.status !== 'in_progress') e.bad++
      days.set(k, e)
    }
    const out: { date: Date; ok: number; bad: number }[] = []
    for (let i = 0; i < weeks * 7; i++) {
      const d = new Date(start)
      d.setDate(start.getDate() + i)
      out.push({ date: d, ...(days.get(d.getTime()) ?? { ok: 0, bad: 0 }) })
    }
    return out
  }, [jobs])
  const done = (jobs ?? []).filter((j) => j.status === 'completed').length
  const failed = (jobs ?? []).filter((j) => !['completed', 'cancelled', 'in_progress'].includes(j.status)).length
  const max = Math.max(1, ...cells.map((c) => c.ok))
  return (
    <Card
      className="h-full"
      title={`Last ${weeks} weeks`}
      icon={<History size={14} />}
      aside={
        <>
          <span className="text-good">{done} done</span> · <span className="text-bad">{failed} failed</span>
        </>
      }
    >
      <div className="grid grid-flow-col grid-rows-7 gap-[3px] overflow-hidden" style={{ gridAutoColumns: '10px' }}>
        {cells.map((c) => (
          <i
            key={c.date.getTime()}
            title={`${c.date.toLocaleDateString()}: ${c.ok} done${c.bad ? `, ${c.bad} failed` : ''}`}
            className="h-2.5 w-2.5 rounded-[2px]"
            style={{
              background: c.bad ? 'var(--red)' : c.ok ? `color-mix(in srgb, var(--green) ${Math.round(35 + (c.ok / max) * 65)}%, transparent)` : 'var(--inset)',
              boxShadow: c.ok || c.bad ? undefined : 'inset 0 0 0 1px var(--line)',
            }}
          />
        ))}
      </div>
      {totals && (
        <div className="mt-2.5 flex flex-wrap gap-x-4 gap-y-1">
          <Stat label="Print time" value={`${Math.round(totals.total_print_time / 3600)} h`} />
          <Stat label="Filament" value={`${(totals.total_filament_used / 1000).toFixed(0)} m`} />
          <Stat label="Jobs" value={String(totals.total_jobs)} />
        </div>
      )}
    </Card>
  )
}

function QuickActions({ bare }: { bare?: boolean }) {
  const lights = usePrinter((s) => s.objects.filter((o) => o.startsWith('output_pin ') && /light|led/i.test(o)))
  const lightValues = usePrinter((s) => lights.map((o) => (s.status[o]?.value as number | undefined) ?? 0))
  const homed = usePrinter((s) => (s.status.toolhead?.homed_axes as string | undefined) ?? '')
  const buttons = (
      <div className="flex flex-wrap gap-1.5">
        <button className="btn btn-sm" onClick={() => attempt(() => runGcode('G28'))}>
          Home all{homed === 'xyz' ? ' ✓' : ''}
        </button>
        <button className="btn btn-sm" onClick={() => attempt(() => runGcode('M84'))}>
          Motors off
        </button>
        {lights.map((o, i) => (
          <button key={o} className="btn btn-sm" onClick={() => attempt(() => runGcode(`SET_PIN PIN=${o.slice('output_pin '.length)} VALUE=${lightValues[i] > 0 ? 0 : 1}`))}>
            {lightValues[i] > 0 ? 'Lights off' : 'Lights on'}
          </button>
        ))}
      </div>
  )
  return bare ? buttons : <Card title="Quick actions">{buttons}</Card>
}

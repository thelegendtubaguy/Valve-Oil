import { useEffect, useMemo, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { Camera, ChevronRight, Folder, Layers, Pause, Play, RotateCcw, SlidersHorizontal, X } from 'lucide-react'
import { HoldButton } from '../components/ui'
import { SpoolmanCard } from '../components/spoolman/SpoolmanCard'
import { FileRow } from '../components/FileCard'
import { DropZone } from '../components/Upload'
import { PrintDialog } from '../components/PrintDialog'
import { MiniConsole } from '../components/MiniConsole'
import { fmtLength } from '../components/Panels'
import { TuneDialog } from '../components/printing/TunePanel'
import { ExcludeObjectsButton } from '../components/printing/ExcludeObjects'
import { PauseAtLayerButton } from '../components/printing/PauseAtLayer'
import { CameraDeck } from '../components/home-deck/CameraDeck'
import { MacrosDeck, OutputsDeck } from '../components/home-deck/Deck'
import { BoxDeck } from '../components/home-deck/BoxDeck'
import { ToolheadCard } from '../components/home-deck/ToolheadCard'
import { TempsCard } from '../components/home-deck/TempsCard'
import { attempt } from '../components/toast'
import { client, onKlippyReady, runGcode, usePrinter } from '../state/printer'
import { useBoxPresent } from '../state/box'
import { jobProgress, remainingSeconds, useJob } from '../state/job'
import { thumbnailUrl, useFileMeta, useFiles, useHistory, type HistoryJob } from '../state/files'
import { displayName, fmtAgo, fmtDuration, fmtEta } from '../lib/format'
import { normalizeQidiColor } from '../qidi/qidi-box'
import { href } from '../lib/router'
import { t } from '../i18n'
import { tr } from '../i18n/rich'

const area = (name: string): CSSProperties => ({ gridArea: name })

/**
 * Home, in four columns on a desktop. The camera spans the left two, in its own shape, with the Box and the fans and
 * lights under it; then the toolhead over the temperatures; then the print, recent files, and the macros and console,
 * folded until opened. The last card in each column stretches, so the columns end together. A tablet has two columns
 * and a phone one.
 */
export function HomeScreen() {
  const job = useJob()
  const showJob = !!job.filename && job.state !== 'standby'
  const jobs = useHistory(500)
  const boxPresent = useBoxPresent()
  return (
    <div className="deck deck-4">
      <div className="half">
        <CameraDeck />
        <div className="half-cols">
          <div className="col">
            {boxPresent && <BoxDeck />}
            <SpoolmanCard />
          </div>
          <div className="col">
            <OutputsDeck />
          </div>
        </div>
      </div>
      <div className="col">
        <ToolheadCard />
        <TempsCard />
      </div>
      <div className="col">
        {showJob ? <JobCard /> : <ReadyCard jobs={jobs} />}
        <RecentFiles jobs={jobs} className="min-h-[240px]" />
        <MacrosDeck grow fold="vo-home-macros-open" className="min-h-[260px]" />
        <MiniConsole noMacros fold="vo-home-console-open" className="min-h-[320px]" />
      </div>
    </div>
  )
}

// ---- the current or last job ----
export function JobCard({ style }: { style?: CSSProperties }) {
  const job = useJob()
  const meta = useFileMeta(job.filename)
  const [tune, setTune] = useState(false)
  const p = jobProgress(job)
  const remaining = remainingSeconds(job, meta)
  const thumb = thumbnailUrl(job.filename, meta, 200)
  const colors = (meta?.filament_colors ?? []).map(normalizeQidiColor)
  const types = (meta?.filament_type ?? '').split(';').map((t) => t.trim())
  const finished = job.state === 'complete' || job.state === 'cancelled' || job.state === 'error'
  const statusText = {
    printing: t('home.job.now_printing'),
    paused: t('home.job.paused'),
    complete: t('home.job.finished'),
    cancelled: t('home.job.cancelled'),
    error: t('home.job.error'),
    standby: '',
  }[job.state]
  const fill = finished ? (job.state === 'complete' ? 1 : p) : p

  return (
    <section className="card grid content-start gap-3.5 @container" style={style}>
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
            <Stat label={t('home.job.remaining')} value={fmtDuration(remaining)} />
            <Stat label={t('home.job.done_at')} value={fmtEta(remaining)} />
            <Stat label={t('home.job.elapsed')} value={fmtDuration(job.totalDuration)} />
          </div>
          <div className="grid grid-cols-3 gap-2">
            {job.state === 'paused' ? (
              <button className="btn btn-primary btn-lg px-2!" onClick={() => attempt(() => client.call('printer.print.resume'))}>
                <Play size={14} /> {t('home.job.resume')}
              </button>
            ) : (
              <button className="btn btn-lg px-2!" onClick={() => attempt(() => client.call('printer.print.pause'))}>
                <Pause size={14} /> {t('home.job.pause')}
              </button>
            )}
            <button className="btn btn-lg px-2!" onClick={() => setTune(true)}>
              <SlidersHorizontal size={14} /> {t('home.job.tune')}
            </button>
            <HoldButton className="btn-lg px-2!" onHold={() => attempt(() => client.call('printer.print.cancel'))}>
              <X size={14} /> {tr('home.job.cancel_hold', {}, { hint: (c) => <span className="hidden text-[11.5px] font-medium opacity-80 @min-[400px]:inline">{c}</span> })}
            </HoldButton>
          </div>
          <div className="flex flex-wrap gap-1.5">
            <button className="chip cursor-pointer border-0 hover:bg-hover" onClick={() => setTune(true)}>
              {t('home.job.speed', { pct: Math.round(job.speedFactor * 100) })}
            </button>
            <button className="chip cursor-pointer border-0 hover:bg-hover" onClick={() => setTune(true)}>
              {t('home.job.flow', { pct: Math.round(job.extrudeFactor * 100) })}
            </button>
            <button className="chip num cursor-pointer border-0 hover:bg-hover" onClick={() => setTune(true)}>
              Z {job.zOffset >= 0 ? '+' : ''}
              {job.zOffset.toFixed(3)}
            </button>
            <span className="flex-1" />
            <ExcludeObjectsButton className="chip cursor-pointer border-0 hover:bg-hover" />
            <PauseAtLayerButton className="chip cursor-pointer border-0 hover:bg-hover" />
          </div>
        </>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2">
            <Stat label={t('home.job.printing')} value={fmtDuration(job.printDuration)} />
            <Stat label={t('home.job.filament')} value={job.filamentUsed > 0 ? fmtLength(job.filamentUsed) : meta?.filament_total ? fmtLength(meta.filament_total) : '—'} />
            <Stat label={t('home.job.slicer')} value={meta?.estimated_time ? fmtDuration(meta.estimated_time) : '—'} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <ReprintButton filename={job.filename} />
            <button className="btn" onClick={() => attempt(() => runGcode('SDCARD_RESET_FILE'))}>
              {t('common.done')}
            </button>
          </div>
        </>
      )}
      {tune && <TuneDialog onClose={() => setTune(false)} />}
    </section>
  )
}

// ---- between prints ----
export function ReadyCard({ style, jobs }: { style?: CSSProperties; jobs: HistoryJob[] | null }) {
  const last = jobs?.[0]
  return (
    <section className="card grid content-start gap-3" style={style}>
      <div>
        <div className="lbl">{t('home.ready.title')}</div>
        {last ? (
          <div className="mt-1 flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <div className="truncate font-semibold" title={last.filename}>
                {displayName(last.filename)}
              </div>
              <div className="text-[12.5px] text-ink-3">
                {t(last.status === 'completed' ? 'home.ready.last_finished' : last.status === 'cancelled' ? 'home.ready.last_cancelled' : 'home.ready.last_failed', { ago: last.end_time ? fmtAgo(last.end_time) : '' })}
              </div>
            </div>
            <ReprintButton filename={last.filename} small />
          </div>
        ) : (
          <div className="mt-1 text-ink-2">{t('home.ready.empty')}</div>
        )}
      </div>
      <DropZone className="py-3!" />
    </section>
  )
}

export function RecentFiles({ jobs, className = '' }: { jobs: HistoryJob[] | null; className?: string }) {
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
    <section className={`card grow ${className}`} style={area('files')}>
      <h3 className="card-title">
        <Folder size={14} /> {t('home.recent.title')}
        <a href={href('files')} className="aside inline-flex items-center gap-1">
          {t('home.recent.all')} <ChevronRight size={13} />
        </a>
      </h3>
      <div className="grow-body -mx-1.5 max-wide:max-h-[340px] max-wide:overflow-auto">
        {recent.map((f) => (
          <FileRow key={f.path} path={f.path} last={byFile.get(f.path)?.last} count={byFile.get(f.path)?.count ?? 0} onOpen={() => setOpen(f.path)} />
        ))}
        {files && files.length === 0 && <div className="px-1.5 text-ink-3">{t('home.recent.none')}</div>}
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
        <RotateCcw size={small ? 13 : 14} /> {t('home.job.print_again')}
      </button>
      {open && <PrintDialog filename={filename} onClose={() => setOpen(false)} />}
    </>
  )
}

// ---- camera ----
export type Webcam = { name: string; enabled: boolean; service: string; stream_url: string; snapshot_url: string; flip_horizontal: boolean; flip_vertical: boolean; rotation: number; aspect_ratio?: string }

/** Moonraker's enabled cameras that stream, in its order. */
export function useWebcams(): Webcam[] {
  const [cams, setCams] = useState<Webcam[]>([])
  const ready = usePrinter((s) => s.conn === 'open')
  useEffect(() => {
    if (!ready) return
    const load = () =>
      client
        .call<{ webcams: Webcam[] }>('server.webcams.list')
        .then((r) => setCams(r.webcams.filter((w) => w.enabled !== false && /mjpeg|uv4l|ipstream|stream/i.test(w.service + w.stream_url))))
        .catch(() => setCams([]))
    load()
    const off = onKlippyReady(load)
    return () => {
      off()
    }
  }, [ready])
  return cams
}

/** Full screen for the camera: the browser's, or where a page element can't have it (iPhone), the whole page. */
function useFullScreen() {
  const box = useRef<HTMLDivElement>(null)
  const [native, setNative] = useState(false)
  const [page, setPage] = useState(false)
  useEffect(() => {
    const onChange = () => setNative(document.fullscreenElement != null && document.fullscreenElement === box.current)
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])
  useEffect(() => {
    if (!page) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setPage(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [page])
  const toggle = () => {
    if (native) void document.exitFullscreen?.()
    else if (page) setPage(false)
    else if (document.fullscreenEnabled && box.current?.requestFullscreen) box.current.requestFullscreen().catch(() => setPage(true))
    else setPage(true)
  }
  return { box, page, full: native || page, toggle }
}

/** The camera's width over height: Moonraker's aspect_ratio ("4:3"), turned for a 90° or 270° rotation. */
export function cameraRatio(cam: Webcam, measured?: number | null) {
  const m = /^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$/.exec(cam.aspect_ratio ?? '')
  const r = measured ?? (m ? Number(m[1]) / Number(m[2]) : 16 / 9)
  return cam.rotation % 180 ? 1 / r : r
}

/**
 * One camera's stream with its name and Full screen. `fit` gives it the camera's own shape, and in a row of cameras a
 * share of the width in proportion to that shape, so cameras side by side are the same height. `name` replaces the
 * name tag, for a camera picker.
 */
export function CameraView({ cam, toggle, fit, name }: { cam: Webcam; toggle?: ReactNode; fit?: boolean; name?: ReactNode }) {
  const [failed, setFailed] = useState(false)
  // The stream's own size, once its first frame arrives, so the card takes the camera's shape without letterboxing.
  const [measured, setMeasured] = useState<number | null>(null)
  const img = useRef<HTMLImageElement>(null)
  useEffect(() => {
    if (!fit) return
    const t = setInterval(() => {
      const el = img.current
      if (el?.naturalWidth && el.naturalHeight) {
        setMeasured(el.naturalWidth / el.naturalHeight)
        clearInterval(t)
      }
    }, 250)
    return () => clearInterval(t)
  }, [fit, cam.stream_url])
  const fs = useFullScreen()
  const src = new URL(cam.stream_url, client.httpBase + '/').toString()
  const transform = [cam.flip_horizontal ? 'scaleX(-1)' : '', cam.flip_vertical ? 'scaleY(-1)' : '', cam.rotation ? `rotate(${cam.rotation}deg)` : ''].join(' ')
  const size = fs.page ? 'fixed inset-0 z-80' : fs.full ? 'relative' : fit ? 'relative min-w-0 rounded-[10px]' : 'relative aspect-video rounded-[10px] wide:aspect-auto wide:min-h-0 wide:flex-1'
  const ratio = cameraRatio(cam, measured)
  return (
    <div ref={fs.box} className={`overflow-hidden bg-[#14161b] ${size}`} style={fit && !fs.full ? { aspectRatio: ratio, flex: `${ratio} 1 0` } : undefined} onDoubleClick={fs.toggle}>
      {!failed ? (
        <img ref={img} src={src} alt={cam.name} className="absolute inset-0 h-full w-full object-contain" style={{ transform }} onError={() => setFailed(true)} />
      ) : (
        <div className="grid h-full place-items-center text-[#9aa1ad]">
          <span className="flex items-center gap-2">
            <Camera size={16} /> {t('home.camera.unavailable')}
          </span>
        </div>
      )}
      <div className="absolute top-2.5 left-2.5 z-10">{name ?? <span className="cam-tag">{cam.name}</span>}</div>
      <button className="absolute right-2.5 bottom-2.5 rounded-md border-0 bg-black/50 px-2 py-0.5 text-[11px] text-white" onClick={fs.toggle}>
        {fs.full ? t('common.close') : t('home.camera.full_screen')}
      </button>
      {!fs.full && toggle}
    </div>
  )
}

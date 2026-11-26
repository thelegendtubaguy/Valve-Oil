import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Camera, ChevronRight, Folder, Layers, Pause, Play, RotateCcw, SlidersHorizontal, X } from 'lucide-react'
import { HoldButton, confirm } from '../components/ui'
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
import { ExternalSpoolDeck } from '../components/home-deck/ExternalSpoolDeck'
import { ToolheadCard } from '../components/home-deck/ToolheadCard'
import { TempsCard } from '../components/home-deck/TempsCard'
import { attempt } from '../components/toast'
import { client, onKlippyReady, runGcode, useConnected } from '../state/printer'
import { useBoxPresent } from '../state/box'
import { jobProgress, remainingSeconds, useJob } from '../state/job'
import { fileHistory, thumbnailUrl, useFileMeta, useFiles, useHistory, type HistoryJob } from '../state/files'
import { displayName, fmtAgo, fmtDuration, fmtEta } from '../lib/format'
import { normalizeQidiColor } from '../qidi/qidi-box'
import { readLocal, saveLocal, useMedia } from '../lib/browser'
import { href } from '../lib/router'
import { t } from '../i18n'
import { tr } from '../i18n/rich'

/**
 * Home, in four columns on a desktop. The camera spans the left two, in its own shape, with the Box (or, with no Box,
 * the external spool) and the fans and lights under it; then the toolhead over the temperatures; then the print,
 * recent files, and the macros and console, folded until opened. The last card in each column stretches, so the columns end together. A tablet has two columns
 * under the camera, each stacking its cards straight under one another, and a phone one.
 */
export function HomeScreen() {
  const job = useJob()
  const showJob = !!job.filename && job.state !== 'standby'
  const jobs = useHistory(500)
  const boxPresent = useBoxPresent()
  const twoColumns = useMedia('(min-width: 768px) and (not (min-width: 100rem))')
  const jobCard = showJob ? <JobCard /> : <ReadyCard jobs={jobs} />
  const recent = <RecentFiles jobs={jobs} className="min-h-[240px]" />
  const macros = <MacrosDeck className="min-h-[260px]" />
  const consoleCard = <MiniConsole className="min-h-[320px]" />
  // The camera and the first cards under it keep their places in every layout, so changing layout doesn't restart them.
  return (
    <div className="deck deck-4">
      <div className="half">
        <CameraDeck />
        <div className="half-cols">
          <div className="col">
            {boxPresent ? <BoxDeck /> : <ExternalSpoolDeck />}
            <SpoolmanCard />
            {twoColumns && (
              <>
                <ToolheadCard />
                {jobCard}
                {macros}
              </>
            )}
          </div>
          <div className="col">
            <OutputsDeck />
            {twoColumns && (
              <>
                <TempsCard />
                {recent}
                {consoleCard}
              </>
            )}
          </div>
        </div>
      </div>
      {!twoColumns && (
        <>
          <div className="col">
            <ToolheadCard />
            <TempsCard />
          </div>
          <div className="col">
            {jobCard}
            {recent}
            {macros}
            {consoleCard}
          </div>
        </>
      )}
    </div>
  )
}

// ---- the current or last job ----
const CHIP = 'chip cursor-pointer border-0 hover:bg-hover'

const cancelPrint = () => attempt(() => client.call('printer.print.cancel'))

// Holding Cancel cancels at once; a tap asks first, so a press that doesn't know about holding isn't ignored.
async function confirmCancel() {
  if (await confirm({ title: t('home.job.cancel_confirm_title'), body: t('home.job.cancel_confirm_body'), confirm: t('home.job.cancel_confirm'), dismiss: t('home.job.keep_printing'), danger: true })) cancelPrint()
}

export function JobCard() {
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
  const fill = job.state === 'complete' ? 1 : p

  return (
    <section className="card grid content-start gap-3.5 @container">
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
            <HoldButton className="btn-lg px-2!" onHold={cancelPrint} onTap={confirmCancel}>
              <X size={14} /> {tr('home.job.cancel_hold', {}, { hint: (c) => <span className="hidden text-[11.5px] font-medium opacity-80 @min-[400px]:inline">{c}</span> })}
            </HoldButton>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {[t('home.job.speed', { pct: Math.round(job.speedFactor * 100) }), t('home.job.flow', { pct: Math.round(job.extrudeFactor * 100) })].map((label, i) => (
              <button key={i} className={CHIP} onClick={() => setTune(true)}>
                {label}
              </button>
            ))}
            <button className={`num ${CHIP}`} onClick={() => setTune(true)}>
              Z {job.zOffset >= 0 ? '+' : ''}
              {job.zOffset.toFixed(3)}
            </button>
            <span className="flex-1" />
            <ExcludeObjectsButton className={CHIP} />
            <PauseAtLayerButton className={CHIP} />
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
export function ReadyCard({ jobs }: { jobs: HistoryJob[] | null }) {
  const last = jobs?.[0]
  return (
    <section className="card grid grid-cols-1 content-start gap-3">
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
        ) : jobs === null ? (
          // Until history arrives, the room the last print takes, so the card doesn't grow as the page loads.
          <div className="mt-1 h-[39px]" aria-hidden="true" />
        ) : (
          <div className="mt-1 text-ink-2">{t('home.ready.empty')}</div>
        )}
      </div>
      <DropZone className="py-3!" />
    </section>
  )
}

const RECENT_ROWS_KEY = 'vo-home-recent-rows'
// A FileRow's height; the list is capped at 340 px in the narrower layouts.
const RECENT_ROW_PX = 52

export function RecentFiles({ jobs, className = '' }: { jobs: HistoryJob[] | null; className?: string }) {
  const files = useFiles()
  const [open, setOpen] = useState<string | null>(null)
  const recent = useMemo(() => {
    if (!files) return []
    const historyOf = fileHistory(jobs)
    const rows = files.map((f) => ({ ...f, history: historyOf(f.path, f.modified) }))
    const lastUsed = (f: (typeof rows)[number]) => Math.max(f.history?.last.start_time ?? 0, f.modified)
    return rows.sort((a, b) => lastUsed(b) - lastUsed(a)).slice(0, 20)
  }, [files, jobs])
  // Until the list arrives, the card keeps the room its rows took last time, so the cards under it don't move.
  useEffect(() => {
    if (files) saveLocal(RECENT_ROWS_KEY, String(recent.length))
  }, [files, recent.length])
  const reserved = files ? undefined : Math.min(Number(readLocal(RECENT_ROWS_KEY)) || 0, 20) * RECENT_ROW_PX
  return (
    <section className={`card grow ${className}`}>
      <h3 className="card-title">
        <Folder size={14} /> {t('home.recent.title')}
        <a href={href('files')} className="aside inline-flex items-center gap-1">
          {t('home.recent.all')} <ChevronRight size={13} />
        </a>
      </h3>
      <div className="grow-body -mx-1.5 max-wide:max-h-[340px] max-wide:overflow-auto" style={reserved ? { minHeight: Math.min(reserved, 340) } : undefined}>
        {recent.map((f) => (
          <FileRow key={f.path} path={f.path} last={f.history?.last} count={f.history?.count ?? 0} onOpen={() => setOpen(f.path)} />
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

const WEBCAMS_KEY = 'vo-webcams'

function savedWebcams(): Webcam[] {
  try {
    const v = JSON.parse(readLocal(WEBCAMS_KEY) ?? '[]')
    return Array.isArray(v) ? v.filter((c) => c && typeof c.name === 'string' && typeof c.stream_url === 'string') : []
  } catch {
    return []
  }
}

/**
 * Moonraker's enabled cameras that stream, in its order. It starts from the list this browser last read, so after a
 * reload the camera card is there from the start rather than arriving and pushing the cards below it down.
 */
export function useWebcams(): Webcam[] {
  const [cams, setCams] = useState<Webcam[]>(savedWebcams)
  const ready = useConnected()
  useEffect(() => {
    if (!ready) return
    const load = () =>
      client
        .call<{ webcams: Webcam[] }>('server.webcams.list')
        .then((r) => {
          const list = r.webcams.filter((w) => w.enabled !== false && /mjpeg|uv4l|ipstream|stream/i.test(w.service + w.stream_url))
          saveLocal(WEBCAMS_KEY, JSON.stringify(list))
          setCams(list)
        })
        .catch(() => setCams([]))
    load()
    const off = onKlippyReady(load)
    return () => void off()
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

/** The widest a camera of this shape can be and stay under the height Home's layout caps it at, if any. */
export const camMaxWidth = (ratio: number) => `calc(var(--cam-max-h, 100000px) * ${ratio})`

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
  const [exhausted, setExhausted] = useState(false)
  const [retryKey, setRetryKey] = useState(0)
  const failures = useRef(0)
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // The stream's own size, once its first frame arrives, so the card takes the camera's shape without letterboxing.
  // The shape last measured in this browser comes first, so a reload doesn't change the card's height.
  const shapeKey = `vo-camera-shape:${cam.stream_url}`
  const [measured, setMeasured] = useState<number | null>(() => Number(readLocal(shapeKey)) || null)
  const img = useRef<HTMLImageElement>(null)
  const clearRetry = () => {
    if (retryTimer.current) clearTimeout(retryTimer.current)
    retryTimer.current = null
  }
  const retry = () => {
    clearRetry()
    failures.current = 0
    setFailed(false)
    setExhausted(false)
    setRetryKey((key) => key + 1)
  }
  useEffect(() => {
    clearRetry()
    failures.current = 0
    setFailed(false)
    setExhausted(false)
    setRetryKey(0)
    return clearRetry
  }, [cam.stream_url])
  useEffect(() => {
    if (!fit) return
    const t = setInterval(() => {
      const el = img.current
      if (el?.naturalWidth && el.naturalHeight) {
        const shape = el.naturalWidth / el.naturalHeight
        saveLocal(shapeKey, String(shape))
        setMeasured(shape)
        clearInterval(t)
      }
    }, 250)
    return () => clearInterval(t)
  }, [fit, cam.stream_url])
  const fs = useFullScreen()
  const srcUrl = new URL(cam.stream_url, client.httpBase + '/')
  if (retryKey) srcUrl.searchParams.set('vo_retry', String(retryKey))
  const src = srcUrl.toString()
  const transform = [cam.flip_horizontal ? 'scaleX(-1)' : '', cam.flip_vertical ? 'scaleY(-1)' : '', cam.rotation ? `rotate(${cam.rotation}deg)` : ''].join(' ')
  const size = fs.page ? 'fixed inset-0 z-80' : fs.full ? 'relative' : fit ? 'relative min-w-0 rounded-[10px]' : 'relative aspect-video rounded-[10px] wide:aspect-auto wide:min-h-0 wide:flex-1'
  const ratio = cameraRatio(cam, measured)
  return (
    <div ref={fs.box} className={`overflow-hidden bg-[#14161b] ${size}`} style={fit && !fs.full ? { aspectRatio: ratio, flex: `${ratio} 1 0`, maxWidth: camMaxWidth(ratio) } : undefined} onDoubleClick={fs.toggle}>
      {!failed ? (
        <img
          ref={img}
          src={src}
          alt={cam.name}
          className="absolute inset-0 h-full w-full object-contain"
          style={{ transform }}
          onLoad={() => {
            clearRetry()
            failures.current = 0
            setExhausted(false)
          }}
          onError={() => {
            clearRetry()
            const attempt = ++failures.current
            setFailed(true)
            const delays = [1000, 3000, 8000]
            if (attempt <= delays.length) {
              retryTimer.current = setTimeout(() => {
                retryTimer.current = null
                setFailed(false)
                setRetryKey((key) => key + 1)
              }, delays[attempt - 1])
            } else {
              setExhausted(true)
            }
          }}
        />
      ) : (
        <div className="grid h-full place-content-center justify-items-center gap-2 text-[#9aa1ad]">
          <span className="flex items-center gap-2">
            <Camera size={16} /> {t('home.camera.unavailable')}
          </span>
          {exhausted && (
            <button className="btn btn-sm" onClick={retry}>
              <RotateCcw size={13} /> {t('common.retry')}
            </button>
          )}
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

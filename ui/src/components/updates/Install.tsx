import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { Circle, CircleCheck, CircleX, Download, Eye, LoaderCircle, RefreshCw, ScrollText } from 'lucide-react'
import { Card, Empty } from '../ui'
import { Notice } from '../settings/common'
import type { LogFile } from '../system/Logs'
import { fmtSize } from '../system/bits'
import { client, usePrinter } from '../../state/printer'
import { TLTG_RUN_LOG_NAME, type TltgProgressOrigin, type TltgStatusProgress } from '../../qidi/tltg-status'

// The System screen's log viewer, loaded when a log is opened.
const LogViewer = lazy(() => import('../system/Logs').then((m) => ({ default: m.LogViewer })))

const ORIGIN: Record<TltgProgressOrigin, string> = {
  ssh: 'Started over SSH',
  usb: 'Started from a USB drive',
  automatic: 'Started by automatic updates',
  firmware_update: 'Continuing after the QIDI firmware update',
}

const ORIGIN_SHORT: Record<TltgProgressOrigin, string> = {
  ssh: 'over SSH',
  usb: 'from a USB drive',
  automatic: 'by automatic updates',
  firmware_update: 'after the QIDI firmware update',
}

// A finished install that didn't succeed stays on the Updates screen this long, until it's closed.
const SHOW_STOPPED_MS = 24 * 60 * 60 * 1000
const DISMISSED_KEY = 'vo-install-dismissed'

const parseTime = (iso: string | null) => (iso ? Date.parse(iso) : NaN)
const fmtTime = (ms: number) => new Date(ms).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })

function fmtElapsed(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

export function installTitle(p: TltgStatusProgress) {
  const to = p.to_version ? ` ${p.to_version}` : ''
  if (p.from_version && p.to_version && p.from_version !== p.to_version) return `Updating Valve Oil from ${p.from_version} to ${p.to_version}`
  if (p.from_version) return `Installing Valve Oil${to} again`
  return `Installing Valve Oil${to}`
}

/** "Step 4 of 9: System changes", for the banner on other screens. */
export function installStepText(p: TltgStatusProgress) {
  const step = p.index > 0 ? p.steps[p.index - 1] : null
  return step ? `Step ${p.index} of ${p.steps.length}: ${step}` : 'Checking the printer'
}

function useNow(running: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [running])
  return now
}

function loadDismissed(): string | null {
  try {
    return localStorage.getItem(DISMISSED_KEY)
  } catch {
    return null
  }
}

/**
 * The install running now, step by step, as the touchscreen shows it, whether it was started over SSH, from a USB drive,
 * or by automatic updates. An install that stopped stays here for a day, with its log, until it's closed.
 */
export function InstallStepsCard({ progress }: { progress: TltgStatusProgress | null }) {
  const [dismissed, setDismissed] = useState(loadDismissed)
  const [viewing, setViewing] = useState(false)
  const running = progress?.state === 'running'
  const now = useNow(running)
  if (!progress) return null
  const started = parseTime(progress.started_at)
  const finished = parseTime(progress.finished_at)
  const stopped = progress.state === 'failed' || progress.state === 'interrupted'
  if (!running && !(stopped && (Number.isNaN(finished) || now - finished < SHOW_STOPPED_MS) && dismissed !== progress.started_at)) return null

  const close = () => {
    setDismissed(progress.started_at)
    try {
      if (progress.started_at) localStorage.setItem(DISMISSED_KEY, progress.started_at)
    } catch {
      // Private browsing can refuse storage; it's closed for this visit.
    }
  }
  const elapsed = !Number.isNaN(started) ? (running ? now : Number.isNaN(finished) ? now : finished) - started : null
  const current = progress.index

  return (
    <Card className="lg:col-span-2">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="section-label">{progress.origin ? ORIGIN[progress.origin] : 'Valve Oil installer'}</div>
          <div className="mt-1 flex items-center gap-2.5 text-[20px] font-bold tracking-[-0.01em]">
            {running ? <LoaderCircle size={20} className="spin flex-none text-accent-ink" /> : <CircleX size={20} className="flex-none text-bad" />}
            <span className="min-w-0">{running ? installTitle(progress) : progress.state === 'interrupted' ? 'The install stopped before it finished' : "The install didn't finish"}</span>
          </div>
        </div>
        <div className="grid justify-items-end gap-0.5 text-right">
          {current > 0 && (
            <span className="num text-[13px] font-semibold text-ink-2">
              Step {current} of {progress.steps.length}
            </span>
          )}
          {elapsed != null && <span className="num text-[12.5px] text-ink-3">{running ? fmtElapsed(elapsed) : `Ran ${fmtElapsed(elapsed)}`}</span>}
        </div>
      </div>

      {progress.steps.length > 0 && (
        <div className="bar mt-3">
          <i style={{ width: `${(Math.max(0, current - (running ? 1 : 0)) / progress.steps.length) * 100}%` }} />
        </div>
      )}

      {/* Two columns from a phone's width up, each read top to bottom. */}
      <ol className="mt-3 grid list-none gap-0.5 p-0 sm:grid-flow-col sm:grid-cols-2 sm:gap-x-3" style={{ gridTemplateRows: `repeat(${Math.ceil(progress.steps.length / 2)}, auto)` }}>
        {progress.steps.map((step, i) => {
          const n = i + 1
          const state = n < current ? 'done' : n === current ? (running ? 'run' : 'fail') : 'wait'
          return (
            <li key={`${i}-${step}`} className={`task ${state}`}>
              <span className="ic">
                {state === 'done' ? <CircleCheck size={15} /> : state === 'run' ? <LoaderCircle size={15} className="spin" /> : state === 'fail' ? <CircleX size={15} /> : <Circle size={15} />}
              </span>
              <span className={`min-w-0 [overflow-wrap:anywhere] ${state === 'run' ? 'font-semibold text-ink' : ''}`}>
                {step}
                {state === 'run' && progress.detail && <span className="block text-[12px] font-normal text-ink-3">{progress.detail}</span>}
              </span>
              <span />
            </li>
          )
        })}
      </ol>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
        {running ? (
          <span className="flex-1 font-medium text-warn">Don't turn the printer off. Klipper and Moonraker restart during the install, so this page may lose contact for a moment.</span>
        ) : (
          <span className="flex-1 text-ink-2">
            {progress.state === 'interrupted'
              ? 'The installer stopped running partway, for example because its SSH session closed. Run the installer again to finish.'
              : 'Its log says what went wrong and what state the printer was left in.'}
          </span>
        )}
        {progress.log && (
          <button className="btn btn-sm" onClick={() => setViewing(true)}>
            <Eye size={14} /> View log
          </button>
        )}
        {!running && (
          <button className="btn btn-sm btn-ghost" onClick={close}>
            Close
          </button>
        )}
      </div>
      {viewing && progress.log && (
        <Suspense>
          <LogViewer file={{ filename: progress.log, size: 0, modified: 0 }} onClose={() => setViewing(false)} />
        </Suspense>
      )}
    </Card>
  )
}

const KIND: Record<string, string> = {
  install: 'Install',
  usb: 'USB install',
  uninstall: 'Uninstall',
  'restore-backup': 'Backup restore',
}

const runKind = (name: string) => {
  const mode = name.replace(/^valve-oil-/, '').replace(/-\d{8}-\d{6}(-\d+)?\.log$/, '')
  return KIND[mode] ?? mode.replace(/-/g, ' ')
}

/**
 * The log of each install, update, uninstall, and backup restore, from Moonraker's logs folder where the installer
 * keeps its newest ten, with how the last install was started and how it ended.
 */
export function InstallerLogsCard({ progress }: { progress: TltgStatusProgress | null }) {
  const [files, setFiles] = useState<LogFile[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [viewing, setViewing] = useState<LogFile | null>(null)
  const conn = usePrinter((s) => s.conn)
  const load = useCallback(async () => {
    setLoading(true)
    try {
      const r = await client.call<{ files: LogFile[] }>('server.files.get_directory', { path: 'logs', extended: true })
      setFiles(r.files ?? [])
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setLoading(false)
  }, [])
  // Again when Moonraker comes back, and when an install starts or ends, which writes a log.
  useEffect(() => {
    if (conn === 'open') load()
  }, [conn, load, progress?.state, progress?.started_at])
  const logs = useMemo(() => (files ?? []).filter((f) => TLTG_RUN_LOG_NAME.test(f.filename)).sort((a, b) => b.modified - a.modified), [files])

  return (
    <Card
      title="Installer logs"
      icon={<ScrollText size={15} />}
      aside={
        <button className="iconbtn h-7 w-7" onClick={load} disabled={loading || conn !== 'open'} aria-label="Refresh" title="Refresh">
          <RefreshCw size={14} className={loading ? 'spin' : ''} />
        </button>
      }
    >
      <LastInstall progress={progress} />
      {error ? (
        <Empty>{error}</Empty>
      ) : files == null ? (
        <Empty>Loading…</Empty>
      ) : logs.length === 0 ? (
        <Empty>No installer logs yet. Each install, update, uninstall, and backup restore saves one here.</Empty>
      ) : (
        <div className="grid">
          {logs.map((f) => (
            <div key={f.filename} className="flex items-center gap-3 border-t border-line py-2 first:border-t-0">
              <div className="min-w-0 flex-1">
                <div className="font-medium">
                  {runKind(f.filename)}
                  {progress?.state === 'running' && progress.log === f.filename && <span className="ml-2 text-[12px] font-semibold text-accent-ink">Running</span>}
                </div>
                <div className="num text-[12.5px] text-ink-3">
                  {fmtTime(f.modified * 1000)} · {fmtSize(f.size)}
                </div>
              </div>
              <button className="iconbtn" onClick={() => setViewing(f)} aria-label={`View ${f.filename}`} title="View">
                <Eye size={15} />
              </button>
              <a className="iconbtn" href={client.fileUrl('logs', f.filename)} download={f.filename} aria-label={`Download ${f.filename}`} title="Download">
                <Download size={15} />
              </a>
            </div>
          ))}
        </div>
      )}
      <div className="mt-2 text-[12px] text-ink-3">The installer keeps its newest ten logs. Attach the one that went wrong when you report a problem.</div>
      {viewing && (
        <Suspense>
          <LogViewer file={viewing} onClose={() => setViewing(null)} />
        </Suspense>
      )}
    </Card>
  )
}

/** How the last install was started and how it ended, or nothing while one runs or none was recorded. */
function LastInstall({ progress }: { progress: TltgStatusProgress | null }) {
  if (!progress || progress.state === 'running') return null
  const when = parseTime(progress.finished_at ?? progress.started_at)
  const how = progress.origin ? ` ${ORIGIN_SHORT[progress.origin]}` : ''
  const at = Number.isNaN(when) ? '' : `, ${fmtTime(when)}`
  const step = progress.index > 0 ? progress.steps[progress.index - 1] : null
  const [tone, text] =
    progress.state === 'succeeded'
      ? (['good', `Last install: ${progress.to_version ?? 'Valve Oil'}${how}${at}.`] as const)
      : progress.state === 'handed_off'
        ? (['info', `Last install${how}${at} went on in the QIDI firmware update.`] as const)
        : (['warn', `The last install${how}${at} stopped${step ? ` at ${step}` : ''}.`] as const)
  return (
    <Notice tone={tone} className="mb-2">
      {text}
    </Notice>
  )
}

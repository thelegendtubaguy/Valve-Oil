import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { Circle, CircleCheck, CircleX, Eye, LoaderCircle, RefreshCw, ScrollText } from 'lucide-react'
import { Card, Empty, Notice } from '../ui'
import { fmtSize, LogButtons, useLogFiles, type LogFile } from '../system/bits'
import { fmtDateTime } from '../settings/common'
import { useConnected } from '../../state/printer'
import { readLocal, saveLocal } from '../../lib/browser'
import { VO_RUN_LOG_NAME, type VoProgressOrigin, type VoStatusProgress } from '../../qidi/vo-status'
import { t, type MessageKey } from '../../i18n'

// The System screen's log viewer, loaded when a log is opened.
const LogViewer = lazy(() => import('../system/Logs').then((m) => ({ default: m.LogViewer })))

/** How an install was started: "Started over SSH". */
const origin = (o: VoProgressOrigin) => t(`updates.install.origin.${o}`)
/** The same, to follow "Last install": "over SSH". */
const originShort = (o: VoProgressOrigin) => t(`updates.install.origin_short.${o}`)

// A finished install that didn't succeed stays on the Updates screen this long, until it's closed.
const SHOW_STOPPED_MS = 24 * 60 * 60 * 1000
const DISMISSED_KEY = 'vo-install-dismissed'

const parseTime = (iso: string | null) => (iso ? Date.parse(iso) : NaN)

function fmtElapsed(ms: number) {
  const total = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

export function installTitle(p: VoStatusProgress) {
  if (p.from_version && p.to_version && p.from_version !== p.to_version) return t('updates.install.updating_title', { from: p.from_version, to: p.to_version })
  if (p.from_version) return p.to_version ? t('updates.install.reinstalling_version_title', { version: p.to_version }) : t('updates.install.reinstalling_title')
  return p.to_version ? t('updates.install.installing_version_title', { version: p.to_version }) : t('updates.install.installing_title')
}

/** "Step 4 of 9: System changes", for the banner on other screens. */
export function installStepText(p: VoStatusProgress) {
  const step = p.index > 0 ? p.steps[p.index - 1] : null
  return step ? t('updates.install.step_named', { n: p.index, total: p.steps.length, step }) : t('updates.install.checking')
}

function useNow(running: boolean) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running])
  return now
}

/**
 * The install running now, step by step, as the touchscreen shows it, whether it was started over SSH, from a USB drive,
 * or by automatic updates. An install that stopped stays here for a day, with its log, until it's closed.
 */
export function InstallStepsCard({ progress }: { progress: VoStatusProgress | null }) {
  const [dismissed, setDismissed] = useState(() => readLocal(DISMISSED_KEY))
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
    if (progress.started_at) saveLocal(DISMISSED_KEY, progress.started_at)
  }
  const elapsed = !Number.isNaN(started) ? (running ? now : Number.isNaN(finished) ? now : finished) - started : null
  const current = progress.index

  return (
    <Card className="lg:col-span-2">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <div className="min-w-0 flex-1">
          <div className="section-label">{progress.origin ? origin(progress.origin) : t('updates.install.installer')}</div>
          <div className="mt-1 flex items-center gap-2.5 text-[20px] font-bold tracking-[-0.01em]">
            {running ? <LoaderCircle size={20} className="spin flex-none text-accent-ink" /> : <CircleX size={20} className="flex-none text-bad" />}
            <span className="min-w-0">{running ? installTitle(progress) : progress.state === 'interrupted' ? t('updates.install.interrupted_title') : t('updates.install.failed_title')}</span>
          </div>
        </div>
        <div className="grid justify-items-end gap-0.5 text-right">
          {current > 0 && (
            <span className="num text-[13px] font-semibold text-ink-2">{t('updates.install.step', { n: current, total: progress.steps.length })}</span>
          )}
          {elapsed != null && <span className="num text-[12.5px] text-ink-3">{running ? fmtElapsed(elapsed) : t('updates.install.ran', { time: fmtElapsed(elapsed) })}</span>}
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
          <span className="flex-1 font-medium text-warn">{t('updates.install.dont_turn_off')}</span>
        ) : (
          <span className="flex-1 text-ink-2">
            {progress.state === 'interrupted' ? t('updates.install.interrupted_body') : t('updates.install.failed_body')}
          </span>
        )}
        {progress.log && (
          <button className="btn btn-sm" onClick={() => setViewing(true)}>
            <Eye size={14} /> {t('updates.install.view_log')}
          </button>
        )}
        {!running && (
          <button className="btn btn-sm btn-ghost" onClick={close}>
            {t('common.close')}
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

// The run kinds the installer names its logs by (valve-oil-<mode>-...); another mode is shown as its name.
const KINDS = ['install', 'usb', 'uninstall', 'restore-backup']

const runKind = (name: string) => {
  const mode = name.replace(/^valve-oil-/, '').replace(/-\d{8}-\d{6}(-\d+)?\.log$/, '')
  return KINDS.includes(mode) ? t(`updates.logs.kind.${mode.replace(/-/g, '_')}` as MessageKey) : mode.replace(/-/g, ' ')
}

/**
 * The log of each install, update, uninstall, and backup restore, from Moonraker's logs folder where the installer
 * keeps its newest ten, with how the last install was started and how it ended.
 */
export function InstallerLogsCard({ progress }: { progress: VoStatusProgress | null }) {
  const { files, error, loading, load } = useLogFiles()
  const [viewing, setViewing] = useState<LogFile | null>(null)
  const open = useConnected()
  // Again when Moonraker comes back, and when an install starts or ends, which writes a log.
  useEffect(() => {
    if (open) load()
  }, [open, load, progress?.state, progress?.started_at])
  const logs = useMemo(() => (files ?? []).filter((f) => VO_RUN_LOG_NAME.test(f.filename)).sort((a, b) => b.modified - a.modified), [files])

  return (
    <Card
      title={t('updates.logs.title')}
      icon={<ScrollText size={15} />}
      aside={
        <button className="iconbtn h-7 w-7" onClick={load} disabled={loading || !open} aria-label={t('updates.refresh')} title={t('updates.refresh')}>
          <RefreshCw size={14} className={loading ? 'spin' : ''} />
        </button>
      }
    >
      <LastInstall progress={progress} />
      {error ? (
        <Empty>{error}</Empty>
      ) : files == null ? (
        <Empty>{t('common.loading')}</Empty>
      ) : logs.length === 0 ? (
        <Empty>{t('updates.logs.none')}</Empty>
      ) : (
        <div className="grid">
          {logs.map((f) => (
            <div key={f.filename} className="flex items-center gap-3 border-t border-line py-2 first:border-t-0">
              <div className="min-w-0 flex-1">
                <div className="font-medium">
                  {runKind(f.filename)}
                  {progress?.state === 'running' && progress.log === f.filename && <span className="ml-2 text-[12px] font-semibold text-accent-ink">{t('updates.logs.running')}</span>}
                </div>
                <div className="num text-[12.5px] text-ink-3">
                  {fmtDateTime(f.modified * 1000)} · {fmtSize(f.size)}
                </div>
              </div>
              <LogButtons file={f} onView={() => setViewing(f)} className="iconbtn" />
            </div>
          ))}
        </div>
      )}
      <div className="mt-2 text-[12px] text-ink-3">{t('updates.logs.kept')}</div>
      {viewing && (
        <Suspense>
          <LogViewer file={viewing} onClose={() => setViewing(null)} />
        </Suspense>
      )}
    </Card>
  )
}

/** How the last install was started and how it ended, or nothing while one runs or none was recorded. */
function LastInstall({ progress }: { progress: VoStatusProgress | null }) {
  if (!progress || progress.state === 'running') return null
  const when = parseTime(progress.finished_at ?? progress.started_at)
  // How it was started, then when, follow "Last install" in each message: " over SSH", ", Nov 20, 2026, 3:04 PM".
  const how = progress.origin ? ` ${originShort(progress.origin)}` : ''
  const at = Number.isNaN(when) ? '' : `, ${fmtDateTime(when)}`
  const step = progress.index > 0 ? progress.steps[progress.index - 1] : null
  const [tone, text] =
    progress.state === 'succeeded'
      ? (['good', t('updates.logs.last_succeeded', { version: progress.to_version ?? 'Valve Oil', how, at })] as const)
      : progress.state === 'handed_off'
        ? (['info', t('updates.logs.last_handed_off', { how, at })] as const)
        : (['warn', step ? t('updates.logs.last_stopped_at', { how, at, step }) : t('updates.logs.last_stopped', { how, at })] as const)
  return (
    <Notice tone={tone} className="mb-2">
      {text}
    </Notice>
  )
}

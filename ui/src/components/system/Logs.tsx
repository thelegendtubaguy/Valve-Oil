import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Download, FileText, LifeBuoy, RefreshCw, ScrollText, Search } from 'lucide-react'
import { CancelButton, Card, Dialog, Empty, Loading, Spinner, Toggle } from '../ui'
import { attempt, toast } from '../toast'
import { client, usePrinter } from '../../state/printer'
import { useJobActive } from '../../state/job'
import { href } from '../../lib/router'
import { errorText } from '../../lib/browser'
import { fmtAgo } from '../../lib/format'
import { fmtSize, LogButtons, useLogFiles, type LogFile } from './bits'
import { tailText, type LogTail } from './log-tail'
import { locale, t } from '../../i18n'
import { tr } from '../../i18n/rich'

export function Logs() {
  const { files, error, loading, load } = useLogFiles()
  const [viewing, setViewing] = useState<LogFile | null>(null)
  const [rollover, setRollover] = useState(false)
  const has = usePrinter((s) => s.server?.registered_directories.includes('logs') ?? true)
  useEffect(() => {
    load()
  }, [load])
  // Current logs first (klippy.log before klippy.log.2026-11-15), then by name.
  const sorted = useMemo(() => (files ?? []).slice().sort((a, b) => rank(a.filename) - rank(b.filename) || a.filename.localeCompare(b.filename, undefined, { numeric: true })), [files])
  if (!has) return <Empty>{t('system.logs.no_folder')}</Empty>
  return (
    <div className="grid grid-cols-1 gap-3.5">
      <Card
        title={t('system.logs.title')}
        icon={<ScrollText size={15} />}
        aside={
          <span className="flex items-center gap-1">
            <button className="btn btn-ghost btn-sm" onClick={() => setRollover(true)} title={t('system.logs.rollover_hint')}>
              {t('system.logs.rollover_logs')}
            </button>
            <button className="iconbtn h-7 w-7" onClick={load} disabled={loading} aria-label={t('system.refresh')} title={t('system.refresh')}>
              <RefreshCw size={14} className={loading ? 'spin' : ''} />
            </button>
          </span>
        }
      >
        <a href={href('updates')} className="tone-info mb-3 flex items-start gap-2.5 rounded-xl px-3 py-2.5">
          <LifeBuoy size={16} className="mt-0.5 flex-none" />
          <span className="text-ink">
            {tr('system.logs.report_hint', {}, { b: (c) => <b>{c}</b> })}
          </span>
        </a>
        {error ? (
          <Empty>{error}</Empty>
        ) : !files ? (
          <Loading className="p-6" />
        ) : sorted.length === 0 ? (
          <Empty>{t('system.logs.none')}</Empty>
        ) : (
          <div className="-mx-1 overflow-x-auto">
            <table className="table">
              <thead>
                <tr>
                  <th>{t('system.logs.name')}</th>
                  <th className="hidden text-right sm:table-cell">{t('system.logs.size')}</th>
                  <th className="hidden sm:table-cell">{t('system.logs.modified')}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {sorted.map((f) => (
                  <tr key={f.filename}>
                    <td className="max-w-0 min-w-40">
                      <button className="flex w-full min-w-0 items-center gap-2 border-0 bg-transparent p-0 text-left text-ink" onClick={() => setViewing(f)}>
                        <FileText size={14} className="flex-none text-ink-3" />
                        <span className={`code truncate ${rank(f.filename) === 0 ? 'font-semibold' : ''}`}>{f.filename}</span>
                      </button>
                      <div className="mt-0.5 pl-[22px] text-[12px] text-ink-3 sm:hidden">
                        {fmtSize(f.size)} · {fmtAgo(f.modified)}
                      </div>
                    </td>
                    <td className="num hidden text-right whitespace-nowrap text-ink-2 sm:table-cell">{fmtSize(f.size)}</td>
                    <td className="hidden whitespace-nowrap text-ink-2 sm:table-cell" title={new Date(f.modified * 1000).toLocaleString(locale())}>
                      {fmtAgo(f.modified)}
                    </td>
                    <td className="text-right whitespace-nowrap">
                      <LogButtons file={f} onView={() => setViewing(f)} className="iconbtn inline-grid h-8 w-8 align-middle" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {viewing && <LogViewer file={viewing} onClose={() => setViewing(null)} />}
      <RolloverDialog open={rollover} onClose={() => setRollover(false)} onDone={load} />
    </div>
  )
}

const rank = (name: string) => (/\.log$/.test(name) ? 0 : 1)

const CHUNK = 256 * 1024

export function LogViewer({ file, onClose }: { file: LogFile; onClose: () => void }) {
  const [bytes, setBytes] = useState(CHUNK)
  const [tail, setTail] = useState<LogTail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [onlyMatches, setOnlyMatches] = useState(true)
  const [follow, setFollow] = useState(false)
  const pre = useRef<HTMLDivElement>(null)
  const atBottom = useRef(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setTail(await fetchTail(client.fileUrl('logs', file.filename), file.size, bytes))
      setError(null)
    } catch (e) {
      setError(errorText(e))
    }
    setLoading(false)
  }, [file.filename, bytes])
  useEffect(() => {
    load()
  }, [load])
  useEffect(() => {
    if (!follow) return
    const timer = setInterval(load, 3000)
    return () => clearInterval(timer)
  }, [follow, load])
  // Stay at the end as the log grows, unless the reader has scrolled up.
  useEffect(() => {
    const el = pre.current
    if (el && atBottom.current) el.scrollTop = el.scrollHeight
  }, [tail, query, onlyMatches])

  const q = query.trim().toLowerCase()
  const lines = useMemo(() => {
    const all = tail?.lines ?? []
    const numbered = all.map((text, i) => ({ n: i, text }))
    return q && onlyMatches ? numbered.filter((l) => l.text.toLowerCase().includes(q)) : numbered
  }, [tail, q, onlyMatches])
  const matches = q ? (onlyMatches ? lines.length : lines.filter((l) => l.text.toLowerCase().includes(q)).length) : 0

  return (
    <Dialog
      open
      onClose={onClose}
      size="full"
      title={
        <span className="flex min-w-0 items-center gap-2">
          <FileText size={16} className="flex-none text-accent-ink" />
          <span className="code truncate">{file.filename}</span>
        </span>
      }
      footer={
        <>
          <span className="mr-auto text-[12.5px] text-ink-3">
            {tail ? (tail.truncated ? t('system.logs.showing_last', { shown: fmtSize(tail.shown), total: fmtSize(tail.total) }) : t('system.logs.all_shown', { total: fmtSize(tail.total) })) : ''}
          </span>
          {tail?.truncated && (
            <button className="btn btn-sm" disabled={loading} onClick={() => setBytes((b) => b * 4)}>
              {t('system.logs.show_more')}
            </button>
          )}
          <a className="btn btn-sm" href={client.fileUrl('logs', file.filename)} download={file.filename}>
            <Download size={14} /> {t('system.logs.download')}
          </a>
        </>
      }
    >
      <div className="flex h-full min-h-0 flex-col gap-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <label className="relative min-w-0 flex-1 basis-56">
            <Search size={15} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
            <input className="field pl-8" placeholder={t('system.logs.search')} value={query} onChange={(e) => setQuery(e.target.value)} aria-label={t('system.logs.search_log')} />
          </label>
          {q && <span className="num text-[12.5px] text-ink-3">{t('system.logs.matching', { count: matches })}</span>}
          <label className="flex items-center gap-2 text-[13px] text-ink-2">
            <Toggle checked={onlyMatches} onChange={setOnlyMatches} label={t('system.logs.only_matching')} /> {t('system.logs.only_matching')}
          </label>
          <label className="flex items-center gap-2 text-[13px] text-ink-2" title={t('system.logs.follow_hint')}>
            <Toggle checked={follow} onChange={setFollow} label={t('system.logs.follow')} /> {t('system.logs.follow')}
          </label>
          <button className="btn btn-sm" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin' : ''} /> {t('system.refresh')}
          </button>
        </div>
        <div
          ref={pre}
          className="code min-h-0 flex-1 overflow-auto rounded-xl bg-inset p-3 text-[12px] leading-[1.55]"
          style={{ boxShadow: 'inset 0 0 0 1px var(--line)', minHeight: 240 }}
          onScroll={(e) => {
            const el = e.currentTarget
            atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
          }}
        >
          {error ? (
            <div className="text-bad">{error}</div>
          ) : !tail ? (
            <Spinner size={18} />
          ) : lines.length === 0 ? (
            <div className="text-ink-3">{q ? t('system.logs.no_match') : t('system.logs.empty')}</div>
          ) : (
            lines.map((l) => (
              <div key={l.n} className={`break-all whitespace-pre-wrap ${/^(!!|Traceback|.*\bERROR\b|.*\bError\b:)/.test(l.text) ? 'text-bad' : ''}`}>
                {q ? highlight(l.text, q) : l.text || ' '}
              </div>
            ))
          )}
        </div>
      </div>
    </Dialog>
  )
}

function highlight(text: string, q: string) {
  const lower = text.toLowerCase()
  const out: React.ReactNode[] = []
  let i = 0
  for (let at = lower.indexOf(q); at >= 0; at = lower.indexOf(q, at + q.length)) {
    out.push(text.slice(i, at), <mark key={at} className="rounded-sm bg-accent-tint px-px text-accent-ink">{text.slice(at, at + q.length)}</mark>)
    i = at + q.length
  }
  out.push(text.slice(i))
  return out
}

/**
 * The last `bytes` of a log through an HTTP range request, so a 50 MB klippy.log isn't downloaded to read its end. The
 * size comes from the listing: logs only grow until they're rolled over, which a 416 answer catches.
 */
async function fetchTail(url: string, size: number, bytes: number): Promise<LogTail> {
  const start = Math.max(0, size - bytes)
  let res = await fetch(url, start > 0 ? { headers: { Range: `bytes=${start}-` }, cache: 'no-store' } : { cache: 'no-store' })
  if (res.status === 416) res = await fetch(url, { cache: 'no-store' })
  if (!res.ok) throw new Error(t('system.logs.read_failed', { status: res.status }))
  const text = await res.text()
  const from = res.status === 206 ? start : 0
  return tailText(text, from, Math.max(size, from + text.length))
}

function RolloverDialog({ open, onClose, onDone }: { open: boolean; onClose: () => void; onDone: () => void }) {
  const busyPrinting = useJobActive()
  const [app, setApp] = useState<'' | 'klipper' | 'moonraker'>('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (open) setApp(busyPrinting ? 'moonraker' : '')
  }, [open, busyPrinting])
  const go = async () => {
    setBusy(true)
    const ok = await attempt(async () => (await client.call('server.logs.rollover', app ? { application: app } : {}), true))
    setBusy(false)
    if (ok) {
      toast(t('system.logs.rolled_over'))
      onClose()
      onDone()
    }
  }
  const opt = (value: typeof app, label: React.ReactNode, disabled = false) => (
    <label className={`flex items-center gap-2.5 rounded-lg px-2 py-1.5 ${disabled ? 'opacity-45' : 'hover:bg-hover'}`}>
      <input type="radio" name="rollover" checked={app === value} disabled={disabled} onChange={() => setApp(value)} className="accent-[var(--accent)]" />
      {label}
    </label>
  )
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t('system.logs.rollover_logs')}
      footer={
        <>
          <CancelButton onClick={onClose} />
          <button className="btn btn-primary" onClick={go} disabled={busy}>
            {busy && <Spinner size={14} />} {t('system.logs.rollover')}
          </button>
        </>
      }
    >
      <p className="mt-0 text-ink-2">{busyPrinting ? t('system.logs.rollover_about_printing') : t('system.logs.rollover_about')}</p>
      <div className="grid gap-0.5">
        {opt('', t('system.logs.all'), busyPrinting)}
        {opt(
          'klipper',
          <>
            {/* i18n-ignore: a file name */}
            Klipper <span className="code text-ink-3">(klippy.log)</span>
          </>,
          busyPrinting,
        )}
        {opt(
          'moonraker',
          <>
            {/* i18n-ignore: a file name */}
            Moonraker <span className="code text-ink-3">(moonraker.log)</span>
          </>,
        )}
      </div>
    </Dialog>
  )
}

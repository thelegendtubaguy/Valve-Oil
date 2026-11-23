// The Diagnostics tab's fault check: Valve Oil's status service reads the printer's logs for its known fault signatures
// (GET and POST api/diagnostics) and this card shows what it found. The service's text is English and is shown as it
// arrives; only this card's own words are translated.
import { useCallback, useEffect, useState } from 'react'
import { Download, ExternalLink, Stethoscope } from 'lucide-react'
import { Card, Segmented, Spinner } from '../ui'
import { statusRequest } from '../../state/valve-oil'
import { VoStatusRequestError } from '../../qidi/vo-status'
import { FAULT_CHECK_POLL_MS, FAULT_CHECK_SINCE, faultReportName, parseFaultCheck, type FaultCheck, type FaultCheckSince, type FaultFinding, type FaultResult, type FaultSeverity } from './fault-check'
import { locale, t, type MessageKey } from '../../i18n'
import { tr } from '../../i18n/rich'

const SEVERITY_TONE: Record<FaultSeverity, string> = {
  fault: 'tone-warn',
  fixed: 'tone-good',
  repaired: 'tone-good',
  info: 'tone-muted',
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))

export function FaultCheckCard() {
  const [since, setSince] = useState<FaultCheckSince>('all')
  const [check, setCheck] = useState<FaultCheck | null>(null)
  const [loaded, setLoaded] = useState(false)
  // An older Valve Oil, whose status service has no Diagnostics check.
  const [absent, setAbsent] = useState(false)
  const [error, setError] = useState('')
  const [starting, setStarting] = useState(false)
  // Counts failed requests, so a check that's running is asked for again after one fails.
  const [failures, setFailures] = useState(0)

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const parsed = parseFaultCheck(await statusRequest('/diagnostics', 'GET', signal))
      if (signal?.aborted) return
      setAbsent(false)
      if (parsed) {
        setCheck(parsed)
        setError('')
      } else setError(t('system.diagnostics.check.unreadable'))
    } catch (e) {
      if (signal?.aborted) return
      if (e instanceof VoStatusRequestError && e.status === 404) setAbsent(true)
      else {
        setError(errorText(e))
        setFailures((n) => n + 1)
      }
    }
    setLoaded(true)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    load(controller.signal)
    return () => controller.abort()
  }, [load])

  // While a check runs, ask again every 2 seconds until it finishes.
  const running = check?.state === 'running'
  useEffect(() => {
    if (!running) return
    const controller = new AbortController()
    const timer = setTimeout(() => load(controller.signal), FAULT_CHECK_POLL_MS)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [running, check, failures, load])

  const start = async () => {
    setStarting(true)
    setError('')
    try {
      const parsed = parseFaultCheck(await statusRequest('/diagnostics', 'POST', undefined, { since }))
      if (parsed) setCheck(parsed)
      else setError(t('system.diagnostics.check.unreadable'))
    } catch (e) {
      if (e instanceof VoStatusRequestError && e.status === 404) setAbsent(true)
      else {
        // The printer's reason, such as a check already running.
        setError(errorText(e))
        if (e instanceof VoStatusRequestError && e.status === 409) load()
      }
    } finally {
      setStarting(false)
    }
  }

  const downloadReport = () => {
    if (!check?.report) return
    const url = URL.createObjectURL(new Blob([check.report], { type: 'text/plain;charset=utf-8' }))
    const a = document.createElement('a')
    a.href = url
    a.download = faultReportName(check.finished_at)
    document.body.append(a)
    a.click()
    a.remove()
    // Deferred, as revoking right after the click can cancel the download.
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  const title = t('system.diagnostics.check.title')
  const icon = <Stethoscope size={15} />
  if (absent) {
    return (
      <Card title={title} icon={icon}>
        <p className="m-0 text-ink-2">{t('system.diagnostics.check.needs_newer')}</p>
      </Card>
    )
  }

  const busy = running || starting
  const result = check?.state === 'done' ? check.result : null
  const finished = check?.finished_at ? new Date(check.finished_at) : null
  return (
    <Card title={title} icon={icon}>
      <p className="m-0 text-ink-2">{t('system.diagnostics.check.about')}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2.5">
        <Segmented
          value={since}
          label={t('system.diagnostics.check.period')}
          options={FAULT_CHECK_SINCE.map((v) => ({ value: v, label: t(`system.diagnostics.check.since.${v}` as MessageKey) }))}
          onChange={setSince}
        />
        <button className="btn btn-primary" disabled={busy || !loaded} onClick={start}>
          {starting ? <Spinner size={14} /> : <Stethoscope size={14} />} {check && check.state !== 'idle' ? t('system.diagnostics.check.run_again') : t('system.diagnostics.check.run')}
        </button>
        {result && check?.report && (
          <button className="btn" onClick={downloadReport}>
            <Download size={14} /> {t('system.diagnostics.check.download_report')}
          </button>
        )}
      </div>
      {error && <p className="mb-0 text-warn">{error}</p>}
      <div className="mt-3.5">
        {!loaded ? (
          <div className="grid place-items-center p-4">
            <Spinner size={20} />
          </div>
        ) : running ? (
          <p className="m-0 flex items-center gap-2 text-ink-2">
            <Spinner size={15} /> {t('system.diagnostics.check.running')}
          </p>
        ) : check?.state === 'failed' ? (
          <div className="grid gap-1.5">
            <p className="m-0 font-semibold text-warn">{t('system.diagnostics.check.failed')}</p>
            {check.error && <p className="m-0 text-ink-2">{check.error}</p>}
          </div>
        ) : check?.state === 'done' && !result ? (
          <p className="m-0 text-warn">{t('system.diagnostics.check.unreadable')}</p>
        ) : result ? (
          <ResultView result={result} finished={finished && Number.isFinite(finished.getTime()) ? finished : null} />
        ) : check ? (
          <p className="m-0 text-ink-3">{t('system.diagnostics.check.not_run')}</p>
        ) : null}
      </div>
    </Card>
  )
}

function Reach({ result }: { result: FaultResult }) {
  const { from, to, complete, stopped } = result.checked
  return (
    <p className="m-0 text-[13px] text-ink-3">
      {from && to ? t('system.diagnostics.check.reach', { from, to }) : t('system.diagnostics.check.reach_unknown')}
      {!complete && ' '}
      {!complete && (stopped === 'time' ? t('system.diagnostics.check.stopped_time') : t('system.diagnostics.check.partial'))}
    </p>
  )
}

function ResultView({ result, finished }: { result: FaultResult; finished: Date | null }) {
  return (
    <div className="grid gap-3">
      <div className="grid gap-1">
        {result.findings.length === 0 ? (
          <p className="m-0 font-semibold text-good">{t('system.diagnostics.check.none_found')}</p>
        ) : (
          <p className="m-0 font-semibold">{t('system.diagnostics.check.found', { count: result.findings.length })}</p>
        )}
        <Reach result={result} />
        {finished && <p className="m-0 text-[13px] text-ink-3">{t('system.diagnostics.check.finished', { when: finished.toLocaleString(locale()) })}</p>}
      </div>
      {result.findings.map((f) => (
        <FindingView key={f.id} finding={f} />
      ))}
      {result.findings.length > 0 && <p className="m-0 text-[13px] text-ink-3">{tr('system.diagnostics.check.report_hint', {}, { b: (c) => <b>{c}</b> })}</p>}
    </div>
  )
}

function FindingView({ finding: f }: { finding: FaultFinding }) {
  // i18n-ignore: log times and a dash between them
  const when = f.first && f.last && f.first !== f.last ? `${f.first} – ${f.last}` : (f.first ?? f.last)
  return (
    <article className="grid gap-2 rounded-xl bg-inset p-3.5 shadow-[inset_0_0_0_1px_var(--line)]">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
        <b className="text-[14.5px] text-ink">{f.title}</b>
        <span className={`pill ${SEVERITY_TONE[f.severity]} py-0.5!`}>{t(`system.diagnostics.check.severity.${f.severity}` as MessageKey)}</span>
      </div>
      <div className="flex flex-wrap gap-x-3 text-[12.5px] text-ink-3">
        {f.count > 0 && <span>{t('system.diagnostics.check.times', { count: f.count })}</span>}
        {when && <span className="num">{when}</span>}
      </div>
      {f.meaning && <p className="m-0 text-ink-2">{f.meaning}</p>}
      {f.action && (
        <p className="m-0">
          <b>{t('system.diagnostics.check.what_to_do')}</b> {f.action}
        </p>
      )}
      {f.details.length > 0 && (
        <ul className="m-0 grid gap-0.5 pl-5 text-ink-2">
          {f.details.map((d, i) => (
            <li key={i}>{d}</li>
          ))}
        </ul>
      )}
      {f.docs && (
        <a className="btn btn-sm w-fit" href={f.docs} target="_blank" rel="noreferrer">
          <ExternalLink size={13} /> {t('system.diagnostics.check.docs')}
        </a>
      )}
      {f.evidence.length > 0 && (
        <details>
          <summary className="cursor-pointer text-[13px] font-semibold text-ink-2">{t('system.diagnostics.check.evidence', { count: f.evidence.length })}</summary>
          <pre className="m-0 mt-2 max-h-[240px] overflow-auto rounded-lg bg-surface p-2.5 font-mono text-[12px] whitespace-pre-wrap text-ink-2 shadow-[inset_0_0_0_1px_var(--line)] [overflow-wrap:anywhere]">
            {f.evidence.join('\n')}
          </pre>
        </details>
      )}
    </article>
  )
}

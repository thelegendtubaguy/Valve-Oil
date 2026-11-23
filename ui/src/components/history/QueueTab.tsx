import { useEffect, useMemo, useState, type DragEvent } from 'react'
import { ArrowUpToLine, Copy, Ellipsis, GripVertical, ListOrdered, Pause, Play, Plus, RefreshCw, Search, Trash2 } from 'lucide-react'
import { CancelButton, Card, Dialog, Empty, Field, Loading, MenuButton, confirm } from '../ui'
import { attempt, toast } from '../toast'
import { fetchMeta, useFileMeta, useFiles, type FileMeta } from '../../state/files'
import { loadQueue, queueActions, useQueue } from '../../state/history'
import { useJob, isActive, remainingSeconds } from '../../state/job'
import { usePrinter } from '../../state/printer'
import { displayName } from '../../lib/format'
import { fmtDateTime, fmtLength, fmtSpan, fmtWeight, moveJob, multiplyFilenames, queueTotals, type QueuedJob } from './logic'
import { Thumb } from './bits'
import { locale, t, type MessageKey } from '../../i18n'

/** When the queue should be done: a time today, else with the day. */
function eta(seconds: number) {
  const d = new Date(Date.now() + seconds * 1000)
  return d.toDateString() === new Date().toDateString() ? d.toLocaleTimeString(locale(), { hour: 'numeric', minute: '2-digit' }) : d.toLocaleString(locale(), { weekday: 'short', hour: 'numeric', minute: '2-digit' })
}

const STATE_TONE = { ready: 'good', loading: 'accent', starting: 'accent', paused: 'muted' } as const

/** Metadata for each queued file, fetched once per file name and refreshed when files change. */
function useQueueMeta(jobs: QueuedJob[]) {
  const rev = usePrinter((s) => s.filesRev)
  const names = useMemo(() => [...new Set(jobs.map((j) => j.filename))].sort().join('\n'), [jobs])
  const [meta, setMeta] = useState<Record<string, FileMeta | null>>({})
  useEffect(() => {
    if (!names) return
    let live = true
    Promise.all(names.split('\n').map(async (n) => [n, await fetchMeta(n, rev > 0)] as const)).then((r) => live && setMeta(Object.fromEntries(r)))
    return () => {
      live = false
    }
  }, [names, rev])
  return meta
}

/** Moonraker's job queue, as Fluidd's Job Queue card: add, reorder, multiply, remove, pause, and start. */
export function QueueTab() {
  const state = useQueue((s) => s.state)
  const jobs = useQueue((s) => s.jobs)
  const loaded = useQueue((s) => s.loaded)
  const job = useJob()
  const meta = useQueueMeta(jobs)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [adding, setAdding] = useState(false)
  const [multiply, setMultiply] = useState<QueuedJob[] | null>(null)
  const [drag, setDrag] = useState<{ from: number; over: number } | null>(null)

  // A changed queue clears the selection, as in Fluidd.
  useEffect(() => setSelected(new Set()), [jobs])

  const totals = queueTotals(jobs, (f) => meta[f])
  const busy = isActive(job.state)
  const current = useFileMeta(busy ? job.filename : null)
  // Fluidd's ETA: after the current print's estimate when one runs, else from now.
  const remaining = busy ? (remainingSeconds(job, current) ?? 0) : 0
  const sel = jobs.filter((j) => selected.has(j.job_id))

  const removeAll = async () => {
    if (await confirm({ title: t('history.queue.clear_title'), body: t('history.queue.clear_body'), confirm: t('history.queue.remove_all'), danger: true })) attempt(queueActions.clear)
  }
  const reorder = (from: number, to: number) => attempt(() => queueActions.post(moveJob(jobs, from, to), true))
  const onDrop = (e: DragEvent, to: number) => {
    e.preventDefault()
    if (drag && drag.from !== to) reorder(drag.from, to)
    setDrag(null)
  }

  return (
    <Card
      title={t('history.queue.title')}
      icon={<ListOrdered size={14} />}
      aside={
        <span className="inline-flex items-center gap-1.5">
          {state && <span className={`pill tone-${STATE_TONE[state]} px-2.5 py-0.5 text-[12px]`}>{t(`history.queue.state.${state}` as MessageKey)}</span>}
          <button className="iconbtn" onClick={() => attempt(loadQueue)} title={t('history.queue.refresh')} aria-label={t('history.queue.refresh')}>
            <RefreshCw size={15} />
          </button>
        </span>
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {state === 'paused' ? (
          <button className="btn btn-primary" onClick={() => attempt(queueActions.start)} disabled={!loaded}>
            <Play size={15} /> {t('history.queue.start')}
          </button>
        ) : (
          <button className="btn" onClick={() => attempt(queueActions.pause)} disabled={!loaded || !state}>
            <Pause size={15} /> {t('history.queue.pause')}
          </button>
        )}
        <button className="btn" onClick={() => setAdding(true)}>
          <Plus size={15} /> {t('history.queue.add_files')}
        </button>
        <span className="flex-1" />
        {sel.length > 0 ? (
          <>
            <span className="text-[12.5px] text-ink-3">{t('history.queue.selected', { count: sel.length })}</span>
            <button className="btn btn-sm" onClick={() => setMultiply(sel)}>
              <Copy size={14} /> {t('history.queue.multiply')}
            </button>
            <button className="btn btn-sm btn-danger" onClick={() => attempt(() => queueActions.remove(sel.map((j) => j.job_id)))}>
              <Trash2 size={14} /> {t('common.remove')}
            </button>
          </>
        ) : (
          <button className="btn btn-sm btn-ghost" disabled={!jobs.length} onClick={removeAll}>
            <Trash2 size={14} /> {t('history.queue.remove_all')}
          </button>
        )}
      </div>
      <div className="mb-3 text-[12.5px] text-ink-3">
        {state === 'paused' ? t('history.queue.paused_hint') : busy ? t('history.queue.busy_hint') : t('history.queue.ready_hint')} {t('history.queue.drag_hint')}
      </div>

      {!loaded ? (
        <Loading size={22} />
      ) : jobs.length === 0 ? (
        <Empty>{t('history.queue.empty')}</Empty>
      ) : (
        <div className="grid gap-1.5">
          {jobs.map((j, i) => {
            const m = meta[j.filename]
            const on = selected.has(j.job_id)
            return (
              <div
                key={j.job_id}
                draggable
                onDragStart={(e) => (setDrag({ from: i, over: i }), (e.dataTransfer.effectAllowed = 'move'), e.dataTransfer.setData('text/plain', j.job_id))}
                onDragOver={(e) => (e.preventDefault(), drag && drag.over !== i && setDrag({ ...drag, over: i }))}
                onDragEnd={() => setDrag(null)}
                onDrop={(e) => onDrop(e, i)}
                className={`flex items-center gap-2.5 rounded-xl p-2 ${m === null ? 'opacity-60' : ''}`}
                style={{
                  background: on ? 'var(--accent-tint)' : 'var(--inset)',
                  boxShadow: drag && drag.over === i && drag.from !== i ? 'inset 0 0 0 2px var(--accent)' : on ? 'inset 0 0 0 1px var(--accent)' : 'inset 0 0 0 1px var(--line)',
                }}
              >
                <span className="hidden cursor-grab text-ink-3 sm:block" aria-hidden>
                  <GripVertical size={16} />
                </span>
                <input
                  type="checkbox"
                  className="h-6 w-6 flex-none accent-[var(--accent)]"
                  checked={on}
                  aria-label={t('history.queue.select', { name: displayName(j.filename) })}
                  onChange={() => setSelected((s) => (s.has(j.job_id) ? new Set([...s].filter((x) => x !== j.job_id)) : new Set([...s, j.job_id])))}
                />
                <span className="num w-5 flex-none text-center text-[12px] font-bold text-ink-3">{i + 1}</span>
                <Thumb filename={j.filename} meta={m} exists={m !== null} size={40} />
                <span className="grid min-w-0 flex-1 gap-0.5">
                  <span className="truncate font-semibold" title={j.filename}>
                    {displayName(j.filename)}
                  </span>
                  <span className="flex flex-wrap gap-x-2.5 text-[12px] text-ink-3">
                    {m === null ? (
                      <span className="text-warn">{t('history.queue.file_not_found')}</span>
                    ) : (
                      <>
                        {m?.estimated_time != null && <span className="num">{fmtSpan(m.estimated_time)}</span>}
                        {m?.filament_total != null && <span className="num">{fmtLength(m.filament_total)}</span>}
                        {m?.filament_weight_total != null && <span className="num">{fmtWeight(m.filament_weight_total)}</span>}
                      </>
                    )}
                    <span className="hidden sm:inline">{t('history.queue.added', { time: fmtDateTime(j.time_added) })}</span>
                    <span className="hidden num sm:inline">· {t('history.queue.in_queue', { time: fmtSpan(j.time_in_queue) })}</span>
                  </span>
                </span>
                <MenuButton
                  button={<Ellipsis size={16} />}
                  label={t('history.jobs.job_actions_named', { name: displayName(j.filename) })}
                  items={[
                    i > 0 && { label: t('history.queue.jump'), icon: <ArrowUpToLine size={15} />, onSelect: () => attempt(() => queueActions.jump(j.job_id)) },
                    { label: t('history.queue.multiply'), icon: <Copy size={15} />, onSelect: () => setMultiply([j]) },
                    { label: t('common.remove'), icon: <Trash2 size={15} />, danger: true, onSelect: () => attempt(() => queueActions.remove([j.job_id])) },
                  ]}
                />
              </div>
            )
          })}
        </div>
      )}

      {jobs.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {totals.withoutFile > 0 && <span className="chip tone-warn">{t('history.queue.unknown_jobs', { n: totals.withoutFile })}</span>}
          <span className="chip">{t('history.queue.filament', { length: fmtLength(totals.filamentLength), weight: fmtWeight(totals.filamentWeight) })}</span>
          <span className="chip">{t('history.queue.print_time', { time: fmtSpan(totals.time) })}</span>
          <span className="chip">{t('history.queue.eta', { time: eta(remaining + totals.time) })}</span>
        </div>
      )}

      {adding && <AddFilesDialog onClose={() => setAdding(false)} />}
      {multiply && <MultiplyDialog jobs={multiply} onClose={() => setMultiply(null)} />}
    </Card>
  )
}

/** Fluidd's Multiply Job dialog: queues the jobs again, the given number of times. */
function MultiplyDialog({ jobs, onClose }: { jobs: QueuedJob[]; onClose: () => void }) {
  const [copies, setCopies] = useState('1')
  const n = Number(copies)
  const ok = Number.isInteger(n) && n >= 1 && n <= 100
  const save = async () => {
    if (!ok) return
    await attempt(() => queueActions.post(multiplyFilenames(jobs.map((j) => j.filename), n)))
    onClose()
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('history.queue.multiply_title', { count: jobs.length })}
      footer={
        <>
          <CancelButton onClick={onClose} />
          <button className="btn btn-primary" disabled={!ok} onClick={save}>
            {ok ? t('history.queue.add_n', { n: n * jobs.length }) : t('history.job.add_to_queue')}
          </button>
        </>
      }
    >
      <div className="mb-3 text-ink-2">{jobs.length > 1 ? t('history.queue.multiply_body', { count: jobs.length }) : t('history.queue.multiply_body_file', { name: displayName(jobs[0].filename) })}</div>
      <Field label={t('history.queue.copies')}>
        <input className="field num" inputMode="numeric" autoFocus value={copies} onChange={(e) => setCopies(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && save()} />
      </Field>
    </Dialog>
  )
}

/** Picks G-code files from the gcodes folder to add to the queue, in the order they're ticked. */
function AddFilesDialog({ onClose }: { onClose: () => void }) {
  const files = useFiles()
  const [query, setQuery] = useState('')
  const [picked, setPicked] = useState<string[]>([])
  const list = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (files ?? []).filter((f) => !q || f.path.toLowerCase().includes(q)).sort((a, b) => b.modified - a.modified)
  }, [files, query])
  const toggle = (p: string) => setPicked((s) => (s.includes(p) ? s.filter((x) => x !== p) : [...s, p]))
  const add = async () => {
    await attempt(async () => {
      await queueActions.post(picked)
      toast(t('history.queue.added_files', { count: picked.length }))
      onClose()
    })
  }
  return (
    <Dialog
      open
      onClose={onClose}
      size="wide"
      title={t('history.queue.add_title')}
      footer={
        <>
          <CancelButton onClick={onClose} />
          <button className="btn btn-primary" disabled={!picked.length} onClick={add}>
            <Plus size={15} /> {picked.length ? t('history.queue.add_n', { n: picked.length }) : t('history.job.add_to_queue')}
          </button>
        </>
      }
    >
      <label className="relative mb-3 block">
        <Search size={15} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
        <input className="field pl-8" placeholder={t('history.queue.search_files')} aria-label={t('history.queue.search_files')} value={query} onChange={(e) => setQuery(e.target.value)} autoFocus />
      </label>
      {!files ? (
        <Loading className="py-8" />
      ) : list.length === 0 ? (
        <Empty>{query ? t('history.queue.no_files_match') : t('history.queue.no_files')}</Empty>
      ) : (
        <div className="grid max-h-[52dvh] gap-1 overflow-auto">
          {list.map((f) => {
            const n = picked.indexOf(f.path)
            return (
              <button key={f.path} className={`flex items-center gap-3 rounded-lg border-0 px-2.5 py-2 text-left text-ink ${n >= 0 ? 'bg-accent-tint' : 'bg-transparent hover:bg-hover'}`} onClick={() => toggle(f.path)}>
                <span className={`num grid h-5 w-5 flex-none place-items-center rounded-md text-[11px] font-bold ${n >= 0 ? 'bg-accent text-on-accent' : ''}`} style={n >= 0 ? undefined : { boxShadow: 'inset 0 0 0 1.5px var(--line-strong)' }}>
                  {n >= 0 ? n + 1 : ''}
                </span>
                <FileThumb path={f.path} />
                <span className="min-w-0 flex-1 truncate font-semibold">{displayName(f.path)}</span>
                <span className="hidden text-[12px] text-ink-3 sm:inline">{fmtDateTime(f.modified)}</span>
              </button>
            )
          })}
        </div>
      )}
    </Dialog>
  )
}

function FileThumb({ path }: { path: string }) {
  const [meta, setMeta] = useState<FileMeta | null | undefined>(undefined)
  useEffect(() => {
    let live = true
    fetchMeta(path).then((m) => live && setMeta(m))
    return () => {
      live = false
    }
  }, [path])
  return <Thumb filename={path} meta={meta ?? null} size={32} />
}

import { useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Columns3, Download, Ellipsis, FileDown, History as HistoryIcon, Search, Trash2 } from 'lucide-react'
import { Card, Choices, Dialog, Empty, Loading, MenuButton, Spinner, Toggle, confirm } from '../ui'
import { attempt, toast } from '../toast'
import { FLUIDD, useDbItem } from '../../state/database'
import { deleteAllJobs, history, loadHistory, useHistoryState } from '../../state/history'
import { displayName } from '../../lib/format'
import { saveBlob } from '../../lib/browser'
import { csvHeader, exportValue, filterJobs, fmtDateTime, fmtLength, fmtSpan, groupLabel, historyColumns, isVisible, mergeColumns, sortJobs, statusGroup, STATUS_GROUPS, toggleColumn, type Column, type ConfiguredColumn, type StatusGroup } from './logic'
import { toCsv } from './csv'
import { Cell, StatusPill, Thumb, confirmDelete } from './bits'
import { JobDialog } from './JobDialog'
import { t } from '../../i18n'

const PAGE = 25

/** Fluidd's History card: the job table with its columns, search, Load all, Remove all, and CSV export. */
export function JobsCard() {
  const jobs = useHistoryState((s) => s.jobs)
  const loaded = useHistoryState((s) => s.loaded)
  const loading = useHistoryState((s) => s.loading)
  const allLoaded = useHistoryState((s) => s.allLoaded)
  const instance = location.hostname
  const [configured, saveConfigured] = useDbItem<ConfiguredColumn[] | null>(FLUIDD, 'uiSettings.tableHeaders.history', null)
  const [query, setQuery] = useState('')
  const [group, setGroup] = useState<StatusGroup | 'all'>('all')
  const [sort, setSort] = useState<{ key: string; desc: boolean }>({ key: 'start_time', desc: true })
  const [page, setPage] = useState(0)
  const [open, setOpen] = useState<string | null>(null)
  const [picker, setPicker] = useState(false)
  const [exporting, setExporting] = useState(false)

  const allColumns = useMemo(() => mergeColumns(historyColumns(jobs), configured), [jobs, configured])
  const columns = allColumns.filter(isVisible)
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: jobs.length }
    for (const j of jobs) c[statusGroup(j.status)] = (c[statusGroup(j.status)] ?? 0) + 1
    return c
  }, [jobs])
  const list = useMemo(() => sortJobs(filterJobs(jobs, query, group), sort.key, sort.desc), [jobs, query, group, sort])
  const pages = Math.max(1, Math.ceil(list.length / PAGE))
  const p = Math.min(page, pages - 1)
  const rows = list.slice(p * PAGE, p * PAGE + PAGE)
  const openJob = jobs.find((j) => j.job_id === open) ?? null

  const sortBy = (key: string) => (setSort((s) => (s.key === key ? { key, desc: !s.desc } : { key, desc: key !== 'filename' })), setPage(0))

  const removeAll = async () => {
    if (await confirm({ title: t('history.jobs.remove_all_title'), body: t('history.jobs.remove_all_body'), confirm: t('history.jobs.remove_all'), danger: true })) attempt(deleteAllJobs)
  }

  const exportCsv = async () => {
    setExporting(true)
    try {
      let all = jobs
      if (!allLoaded) {
        await loadHistory(0)
        all = history.get().jobs
      }
      // The headers stay in English: the file is read by spreadsheets and scripts.
      const cols = [{ key: 'filename', label: '', name: 'name' as const }, ...mergeColumns(historyColumns(all), configured).filter(isVisible)]
      const csv = toCsv([cols.map(csvHeader), ...all.map((j) => cols.map((c) => exportValue(j, c.key)))])
      // The BOM makes Excel read the file as UTF-8.
      saveBlob(new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' }), `print-history-${instance}.csv`)
    } catch {
      toast(t('history.jobs.export_failed'), 'error')
    }
    setExporting(false)
  }

  return (
    <Card
      title={t('history.title')}
      icon={<HistoryIcon size={14} />}
      aside={
        <span className="inline-flex items-center gap-1">
          <button className="iconbtn" onClick={() => setPicker(true)} title={t('history.jobs.columns')} aria-label={t('history.jobs.columns')}>
            <Columns3 size={16} />
          </button>
          <button className="iconbtn" onClick={exportCsv} disabled={exporting || jobs.length === 0} title={t('history.jobs.export_csv')} aria-label={t('history.jobs.export_csv')}>
            {exporting ? <Spinner size={14} /> : <FileDown size={16} />}
          </button>
          <MenuButton
            button={<Ellipsis size={16} />}
            label={t('history.jobs.more')}
            items={[
              { label: t('history.jobs.load_all'), icon: <Download size={15} />, disabled: allLoaded, onSelect: () => attempt(() => loadHistory(0)) },
              { label: t('history.jobs.remove_all'), icon: <Trash2 size={15} />, danger: true, disabled: jobs.length === 0, onSelect: removeAll },
            ]}
          />
        </span>
      }
    >
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label className="relative min-w-[200px] flex-1 sm:max-w-[320px]">
          <Search size={15} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
          <input className="field pl-8" placeholder={t('history.jobs.search')} value={query} onChange={(e) => (setQuery(e.target.value), setPage(0))} spellCheck={false} />
        </label>
        <div className="-mx-1 max-w-full overflow-x-auto px-1">
          <Choices
            value={group}
            onChange={(v) => (setGroup(v), setPage(0))}
            options={[{ value: 'all' as const, label: t('history.jobs.all') }, ...STATUS_GROUPS.map((g) => ({ value: g, label: groupLabel(g) }))].map((o) => ({
              value: o.value,
              label: (
                <>
                  {o.label}
                  <span className="text-[11.5px] font-semibold opacity-60">{counts[o.value] ?? 0}</span>
                </>
              ),
            }))}
          />
        </div>
      </div>

      {!loaded ? (
        <Loading size={22} />
      ) : list.length === 0 ? (
        <Empty>{jobs.length === 0 ? t('history.jobs.empty') : t('history.jobs.no_match')}</Empty>
      ) : (
        <>
          {/* Phones: one row per job with the essentials; the dialog has the rest. */}
          <div className="grid gap-1.5 md:hidden">
            {rows.map((j) => (
              <button key={j.job_id} className={`flex items-center gap-3 rounded-xl border-0 bg-inset p-2.5 text-left text-ink ${j.exists ? '' : 'opacity-60'}`} style={{ boxShadow: 'inset 0 0 0 1px var(--line)' }} onClick={() => setOpen(j.job_id)}>
                <Thumb filename={j.filename} meta={j.metadata} exists={j.exists} size={46} />
                <span className="grid min-w-0 flex-1 gap-1">
                  <span className="truncate font-semibold">{displayName(j.filename)}</span>
                  <span className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-ink-3">
                    <StatusPill status={j.status} />
                    <span>{fmtDateTime(j.start_time)}</span>
                    <span className="num">{fmtSpan(j.print_duration)}</span>
                    {j.filament_used > 0 && <span className="num">{fmtLength(j.filament_used)}</span>}
                  </span>
                </span>
              </button>
            ))}
          </div>

          {/* Tablets and desktops: Fluidd's table, with the columns picked in either UI. */}
          <div className="-mx-4 hidden overflow-x-auto md:block">
            <table className="table">
              <thead>
                <tr>
                  <th className="w-[56px] !pl-4" />
                  <SortHeader label={t('history.columns.name')} k="filename" sort={sort} onSort={sortBy} />
                  {columns.map((c) => (
                    <SortHeader key={c.key} label={c.label} k={c.key} sort={sort} onSort={c.kind === 'colors' ? undefined : sortBy} />
                  ))}
                  <th className="w-[44px]" />
                </tr>
              </thead>
              <tbody>
                {rows.map((j) => (
                  <tr key={j.job_id} className={`cursor-pointer ${j.exists ? '' : 'opacity-60'}`} onClick={() => setOpen(j.job_id)}>
                    <td className="!py-1.5 !pl-4">
                      <Thumb filename={j.filename} meta={j.metadata} exists={j.exists} size={36} />
                    </td>
                    <td className="max-w-[340px] min-w-[180px]">
                      <div className="truncate font-semibold" title={j.filename}>
                        {displayName(j.filename)}
                      </div>
                      {j.filename.includes('/') && <div className="truncate text-[11.5px] text-ink-3">{j.filename.slice(0, j.filename.lastIndexOf('/'))}</div>}
                    </td>
                    {columns.map((c) => (
                      <td key={c.key} className="whitespace-nowrap text-ink-2">
                        <Cell job={j} col={c} />
                      </td>
                    ))}
                    <td className="!pr-3" onClick={(e) => e.stopPropagation()}>
                      <MenuButton
                        button={<Ellipsis size={16} />}
                        label={t('history.jobs.job_actions')}
                        items={[
                          { label: t('history.jobs.details'), onSelect: () => setOpen(j.job_id) },
                          { label: t('common.delete'), icon: <Trash2 size={15} />, danger: true, onSelect: () => confirmDelete(j) },
                        ]}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2 text-[12.5px] text-ink-3">
            <span className="num">
              {t(allLoaded ? 'history.jobs.range' : 'history.jobs.range_loaded', { from: p * PAGE + 1, to: Math.min(list.length, p * PAGE + PAGE), total: list.length })}
            </span>
            {!allLoaded && (
              <button className="btn btn-sm btn-ghost" disabled={loading} onClick={() => attempt(() => loadHistory(0))} title={t('history.jobs.load_all_hint')}>
                {loading ? <Spinner size={13} /> : <Download size={14} />} {t('history.jobs.load_all')}
              </button>
            )}
            <span className="flex-1" />
            <button className="iconbtn" disabled={p === 0} onClick={() => setPage(p - 1)} aria-label={t('history.jobs.previous_page')}>
              <ChevronLeft size={17} />
            </button>
            <span className="num">{t('history.jobs.page', { page: p + 1, pages })}</span>
            <button className="iconbtn" disabled={p >= pages - 1} onClick={() => setPage(p + 1)} aria-label={t('history.jobs.next_page')}>
              <ChevronRight size={17} />
            </button>
          </div>
        </>
      )}

      {openJob && <JobDialog job={openJob} onClose={() => setOpen(null)} />}
      <ColumnsDialog open={picker} onClose={() => setPicker(false)} columns={allColumns} onToggle={(key, v) => attempt(() => saveConfigured(toggleColumn(configured, key, v)))} />
    </Card>
  )
}

function SortHeader({ label, k, sort, onSort }: { label: string; k: string; sort: { key: string; desc: boolean }; onSort?: (k: string) => void }) {
  const on = sort.key === k
  if (!onSort) return <th>{label}</th>
  return (
    <th aria-sort={on ? (sort.desc ? 'descending' : 'ascending') : undefined}>
      <button className={`inline-flex items-center gap-1 border-0 bg-transparent p-0 text-[11px] font-bold tracking-[0.06em] uppercase ${on ? 'text-accent-ink' : 'text-ink-3 hover:text-ink-2'}`} onClick={() => onSort(k)}>
        {label}
        {on && (sort.desc ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}
      </button>
    </th>
  )
}

function ColumnsDialog({ open, onClose, columns, onToggle }: { open: boolean; onClose: () => void; columns: Column[]; onToggle: (key: string, visible: boolean) => void }) {
  return (
    <Dialog open={open} onClose={onClose} title={t('history.jobs.columns')}>
      <div className="mb-2 text-[12.5px] text-ink-3">{t('history.jobs.columns_hint')}</div>
      <div className="grid gap-x-6 sm:grid-cols-2">
        {columns.map((c) => (
          <label key={c.key} className="flex items-center justify-between gap-3 border-b border-line py-2">
            <span>{c.label}</span>
            <Toggle checked={isVisible(c)} onChange={(v) => onToggle(c.key, v)} label={c.label} />
          </label>
        ))}
      </div>
    </Dialog>
  )
}

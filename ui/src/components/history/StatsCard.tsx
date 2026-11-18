import { ChartColumn, Settings2, Trash2 } from 'lucide-react'
import { Card, MenuButton, confirm } from '../ui'
import { attempt } from '../toast'
import { FLUIDD, useDbItem } from '../../state/database'
import { resetTotals, useHistoryState } from '../../state/history'
import { fmtLength, fmtSpan, rollup, statusBreakdown, type Job, type StatusGroup } from './logic'

const GROUP_COLOR: Record<StatusGroup, string> = { completed: 'var(--green)', cancelled: 'var(--ink-3)', failed: 'var(--red)', in_progress: 'var(--blue)' }

/** Fluidd's Printer Stats card: totals and averages from Moonraker's history totals. */
export function StatsCard() {
  const totals = useHistoryState((s) => s.totals)
  const jobs = useHistoryState((s) => s.jobs)
  const allLoaded = useHistoryState((s) => s.allLoaded)
  // Fluidd keeps these two in its uiSettings.history.
  const [days, setDays] = useDbItem(FLUIDD, 'uiSettings.history.timeInDays', false)
  const [km, setKm] = useDbItem(FLUIDD, 'uiSettings.history.lengthInKilometers', false)
  const r = totals ? rollup(totals) : null

  const reset = async () => {
    if (await confirm({ title: 'Reset stats?', body: 'Are you sure? This will clear all stats. The jobs stay in the history.', confirm: 'Reset stats', danger: true })) attempt(resetTotals)
  }

  return (
    <div className="grid gap-3.5 lg:grid-cols-[minmax(0,1fr)_minmax(300px,380px)]">
      <Card
        className="flex flex-col"
        title="Printer stats"
        icon={<ChartColumn size={14} />}
        aside={
          <MenuButton
            button={<Settings2 size={16} />}
            label="Stats options"
            items={[
              { label: `${days ? '✓ ' : ''}Time in days`, onSelect: () => attempt(() => setDays(!days)) },
              { label: `${km ? '✓ ' : ''}Length in kilometers`, onSelect: () => attempt(() => setKm(!km)) },
              { label: 'Reset stats', icon: <Trash2 size={15} />, danger: true, onSelect: reset },
            ]}
          />
        }
      >
        <div className="grid flex-1 grid-cols-2 gap-2.5 xl:grid-cols-4">
          <Stat label="Total print jobs" value={r ? String(Math.round(r.total_jobs)) : '—'} sub="Longest job" subValue={r ? fmtSpan(r.longest_job, days) : '—'} />
          <Stat label="Total time" value={r ? fmtSpan(r.total_time, days) : '—'} sub="Average per print" subValue={r ? fmtSpan(r.total_avg, days) : '—'} />
          <Stat label="Total print time" value={r ? fmtSpan(r.total_print_time, days) : '—'} sub="Average per print" subValue={r ? fmtSpan(r.print_avg, days) : '—'} />
          <Stat label="Total filament used" value={r ? fmtLength(r.total_filament_used, km) : '—'} sub="Average per print" subValue={r ? fmtLength(r.filament_avg, km) : '—'} />
        </div>
      </Card>
      <OutcomeCard jobs={jobs} allLoaded={allLoaded} />
    </div>
  )
}

function Stat({ label, value, sub, subValue }: { label: string; value: string; sub: string; subValue: string }) {
  return (
    <div className="tile content-start">
      <span className="section-label">{label}</span>
      <span className="v num leading-tight">{value}</span>
      <span className="mt-1 text-[12px] text-ink-3">
        {sub} <b className="num font-semibold whitespace-nowrap text-ink-2">{subValue}</b>
      </span>
    </div>
  )
}

/** How the loaded jobs ended, as one segmented bar with a labelled legend. */
function OutcomeCard({ jobs, allLoaded }: { jobs: Job[]; allLoaded: boolean }) {
  const parts = statusBreakdown(jobs)
  const total = parts.reduce((a, p) => a + p.count, 0)
  const W = 300
  const gap = 2
  const shown = parts.filter((p) => p.count > 0)
  const usable = W - gap * Math.max(0, shown.length - 1)
  let x = 0
  return (
    <Card title="How jobs ended" aside={total ? `${allLoaded ? 'all' : 'last'} ${total} job${total === 1 ? '' : 's'}` : undefined}>
      {total === 0 ? (
        <div className="text-ink-3">No jobs yet.</div>
      ) : (
        <>
          <svg viewBox={`0 0 ${W} 14`} className="block h-3.5 w-full" preserveAspectRatio="none" role="img" aria-label="Jobs by outcome">
            {shown.map((p) => {
              const w = (p.count / total) * usable
              const rect = (
                <rect key={p.group} x={x} y={0} width={Math.max(w, 1)} height={14} rx={4} fill={GROUP_COLOR[p.group]}>
                  <title>{`${p.label}: ${p.count} (${Math.round((p.count / total) * 100)}%)`}</title>
                </rect>
              )
              x += w + gap
              return rect
            })}
          </svg>
          <div className="mt-3 grid gap-0">
            {parts.map((p) => (
              <div key={p.group} className="row-k">
                <span className="k flex items-center gap-2">
                  <i className="swatch" style={{ background: GROUP_COLOR[p.group] }} />
                  {p.label}
                </span>
                <span className="v">
                  {p.count} <span className="font-medium text-ink-3">· {Math.round((p.count / total) * 100)}%</span>
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </Card>
  )
}

import type { ReactNode } from 'react'
import { Empty, PageHeader, Segmented } from '../components/ui'
import { href, useSubPath } from '../lib/router'
import { useComponent, useHistoryFeed, useQueue } from '../state/history'
import { StatsCard } from '../components/history/StatsCard'
import { JobsCard } from '../components/history/JobsCard'
import { QueueTab } from '../components/history/QueueTab'
import { TimelapseTab } from '../components/history/TimelapseTab'
import { t } from '../i18n'

type Tab = 'jobs' | 'queue' | 'timelapse'

/** Print history and stats, Moonraker's job queue, and timelapses: Fluidd's History, Jobs queue, and Timelapse pages. */
export function HistoryScreen() {
  useHistoryFeed()
  const [sub] = useSubPath()
  const hasHistory = useComponent('history')
  const hasQueue = useComponent('job_queue')
  const hasTimelapse = useComponent('timelapse')
  const queued = useQueue((s) => s.jobs.length)
  const tabs: { value: Tab; label: ReactNode; href: string }[] = [
    { value: 'jobs', label: t('history.title'), href: href('history') },
    ...(hasQueue ? [{ value: 'queue' as const, label: <>{t('history.tab_queue')}{queued > 0 && <span className="pill tone-accent px-1.5 py-0 text-[11px]">{queued}</span>}</>, href: href('history', 'queue') }] : []),
    ...(hasTimelapse ? [{ value: 'timelapse' as const, label: t('history.tab_timelapse'), href: href('history', 'timelapse') }] : []),
  ]
  const tab: Tab = sub === 'queue' && hasQueue !== false ? 'queue' : sub === 'timelapse' && hasTimelapse !== false ? 'timelapse' : 'jobs'

  return (
    <>
      <PageHeader title={t('history.title')}>{tabs.length > 1 && <Segmented value={tab} options={tabs} label={t('history.sections')} />}</PageHeader>
      {tab === 'jobs' &&
        (hasHistory === false ? (
          <Empty>{t('history.no_component')}</Empty>
        ) : (
          <>
            <StatsCard />
            <JobsCard />
          </>
        ))}
      {tab === 'queue' && hasQueue && <QueueTab />}
      {tab === 'timelapse' && hasTimelapse && <TimelapseTab />}
    </>
  )
}

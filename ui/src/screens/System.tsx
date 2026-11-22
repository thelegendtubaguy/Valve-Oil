import { PageHeader, Segmented } from '../components/ui'
import { href, useSubPath } from '../lib/router'
import { useLoadSystemInfo } from '../components/system/system-info'
import { Overview } from '../components/system/Overview'
import { Services } from '../components/system/Services'
import { Logs } from '../components/system/Logs'
import { PrinterScreen } from '../components/system/PrinterScreen'
import { Diagnostics } from '../components/system/Diagnostics'
import { t, type MessageKey } from '../i18n'

const TABS = ['overview', 'services', 'logs', 'screen', 'diagnostics'] as const
type Tab = (typeof TABS)[number]

export function SystemScreen() {
  const [sub] = useSubPath()
  const tab: Tab = TABS.some((v) => v === sub) ? (sub as Tab) : 'overview'
  useLoadSystemInfo()
  return (
    <div className="grid min-w-0 grid-cols-1 gap-3.5">
      <PageHeader title={t('system.title')}>
        <Segmented value={tab} label={t('system.title')} options={TABS.map((v) => ({ value: v, label: t(`system.tabs.${v}` as MessageKey), href: v === 'overview' ? href('system') : href('system', v) }))} />
      </PageHeader>
      {tab === 'overview' && <Overview />}
      {tab === 'services' && <Services />}
      {tab === 'logs' && <Logs />}
      {tab === 'screen' && <PrinterScreen />}
      {tab === 'diagnostics' && <Diagnostics />}
    </div>
  )
}

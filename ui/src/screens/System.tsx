import { PageHeader, Segmented } from '../components/ui'
import { href, useSubPath } from '../lib/router'
import { useLoadSystemInfo } from '../components/system/system-info'
import { Overview } from '../components/system/Overview'
import { Services } from '../components/system/Services'
import { Logs } from '../components/system/Logs'
import { PrinterScreen } from '../components/system/PrinterScreen'
import { Diagnostics } from '../components/system/Diagnostics'

const TABS = [
  { value: 'overview', label: 'Overview' },
  { value: 'services', label: 'Services' },
  { value: 'logs', label: 'Logs' },
  { value: 'screen', label: 'Printer screen' },
  { value: 'diagnostics', label: 'Diagnostics' },
] as const
type Tab = (typeof TABS)[number]['value']

export function SystemScreen() {
  const [sub] = useSubPath()
  const tab: Tab = TABS.some((t) => t.value === sub) ? (sub as Tab) : 'overview'
  useLoadSystemInfo()
  return (
    <div className="grid min-w-0 grid-cols-1 gap-3.5">
      <PageHeader title="System">
        <Segmented value={tab} label="System" options={TABS.map((t) => ({ value: t.value, label: t.label, href: t.value === 'overview' ? href('system') : href('system', t.value) }))} />
      </PageHeader>
      {tab === 'overview' && <Overview />}
      {tab === 'services' && <Services />}
      {tab === 'logs' && <Logs />}
      {tab === 'screen' && <PrinterScreen />}
      {tab === 'diagnostics' && <Diagnostics />}
    </div>
  )
}

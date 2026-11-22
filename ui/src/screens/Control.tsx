import type { ReactNode } from 'react'
import { PageHeader, Segmented } from '../components/ui'
import { href, useSubPath } from '../lib/router'
import { MoveTab, ToolsMenu } from '../components/control/Move'
import { TempsActions, TempsTab } from '../components/control/Thermals'
import { OutputsTab } from '../components/control/Outputs'
import { MacrosActions, MacrosTab } from '../components/control/Macros'
import { LimitsTab } from '../components/control/Limits'
import { t, type MessageKey } from '../i18n'

type Tab = 'move' | 'temps' | 'outputs' | 'macros' | 'limits'

const TABS: { value: Tab; labelKey: MessageKey; body: () => ReactNode; actions?: () => ReactNode }[] = [
  { value: 'move', labelKey: 'control.screen.tab_move', body: () => <MoveTab />, actions: () => <ToolsMenu /> },
  { value: 'temps', labelKey: 'control.screen.tab_temps', body: () => <TempsTab />, actions: () => <TempsActions /> },
  { value: 'outputs', labelKey: 'control.screen.tab_outputs', body: () => <OutputsTab /> },
  { value: 'macros', labelKey: 'control.screen.tab_macros', body: () => <MacrosTab />, actions: () => <MacrosActions /> },
  { value: 'limits', labelKey: 'control.screen.tab_limits', body: () => <LimitsTab /> },
]

/** Everything Fluidd's dashboard controls outside the Box, the console, the bed mesh, and history, as tabs. */
export function ControlScreen() {
  const [sub] = useSubPath()
  const tab = TABS.find((x) => x.value === sub) ?? TABS[0]
  return (
    // min-w-0 keeps the wide tab bar from stretching the page on a phone; it scrolls instead.
    <div className="grid min-w-0 grid-cols-1 gap-3.5">
      <PageHeader title={t('control.screen.title')}>
        <Segmented label={t('control.screen.title')} value={tab.value} options={TABS.map((x) => ({ value: x.value, label: t(x.labelKey), href: href('control', x.value) }))} />
        {tab.actions && <div className="flex flex-wrap gap-1.5">{tab.actions()}</div>}
      </PageHeader>
      {tab.body()}
    </div>
  )
}

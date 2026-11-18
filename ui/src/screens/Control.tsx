import type { ReactNode } from 'react'
import { PageHeader, Segmented } from '../components/ui'
import { href, useSubPath } from '../lib/router'
import { MoveTab, ToolsMenu } from '../components/control/Move'
import { TempsActions, TempsTab } from '../components/control/Thermals'
import { OutputsTab } from '../components/control/Outputs'
import { MacrosActions, MacrosTab } from '../components/control/Macros'
import { LimitsTab } from '../components/control/Limits'

type Tab = 'move' | 'temps' | 'outputs' | 'macros' | 'limits'

const TABS: { value: Tab; label: string; body: () => ReactNode; actions?: () => ReactNode }[] = [
  { value: 'move', label: 'Move', body: () => <MoveTab />, actions: () => <ToolsMenu /> },
  { value: 'temps', label: 'Temperatures', body: () => <TempsTab />, actions: () => <TempsActions /> },
  { value: 'outputs', label: 'Fans and lights', body: () => <OutputsTab /> },
  { value: 'macros', label: 'Macros', body: () => <MacrosTab />, actions: () => <MacrosActions /> },
  { value: 'limits', label: 'Limits', body: () => <LimitsTab /> },
]

/** Everything Fluidd's dashboard controls outside the Box, the console, the bed mesh, and history, as tabs. */
export function ControlScreen() {
  const [sub] = useSubPath()
  const tab = TABS.find((t) => t.value === sub) ?? TABS[0]
  return (
    // min-w-0 keeps the wide tab bar from stretching the page on a phone; it scrolls instead.
    <div className="grid min-w-0 grid-cols-1 gap-3.5">
      <PageHeader title="Control">
        <Segmented label="Control" value={tab.value} options={TABS.map((t) => ({ value: t.value, label: t.label, href: href('control', t.value) }))} />
        {tab.actions && <div className="flex flex-wrap gap-1.5">{tab.actions()}</div>}
      </PageHeader>
      {tab.body()}
    </div>
  )
}

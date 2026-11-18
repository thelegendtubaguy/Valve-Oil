import { Moon, RotateCcw, SlidersHorizontal, Sun, TriangleAlert } from 'lucide-react'
import { confirm, Segmented } from '../ui'
import { attempt } from '../toast'
import { setTheme, useTheme } from '../../lib/theme'
import { deleteItem, getItem, VALVE_OIL_UI } from '../../state/database'
import { Row, SettingsCard, TextField, ToggleRow } from './common'
import { useUiSetting } from './fluidd'

export function GeneralSettings() {
  const [theme] = useTheme()
  const [name, setName] = useUiSetting('general.instanceName')
  const [confirmEstop, setConfirmEstop] = useUiSetting('general.confirmOnEstop')
  const [cpu, setCpu] = useUiSetting('warnings.warnOnCpuThrottled')
  const [drivers, setDrivers] = useUiSetting('warnings.warnOnStepperDriverOverheating')

  return (
    <>
      <SettingsCard title="General" icon={<SlidersHorizontal size={14} />}>
        <Row title="Printer name" sub="The name Fluidd shows for this printer." wide>
          <TextField value={name} onSave={setName} placeholder="fluidd" label="Printer name" />
        </Row>
        <Row title="Theme" sub="Kept in this browser.">
          <Segmented
            label="Theme"
            value={theme}
            onChange={setTheme}
            options={[
              { value: 'dark', label: <><Moon size={14} /> Dark</> },
              { value: 'light', label: <><Sun size={14} /> Light</> },
            ]}
          />
        </Row>
        <ToggleRow title="Require confirm on Emergency Stop" checked={confirmEstop} onChange={setConfirmEstop} />
      </SettingsCard>

      <SettingsCard title="Warnings" icon={<TriangleAlert size={14} />} footer="Shared with Fluidd, which shows these warnings.">
        <ToggleRow title="Warn if CPU throttling is detected" sub="CPU throttling can cause prints to fail." checked={cpu} onChange={setCpu} />
        <ToggleRow title="Warn if stepper driver is over-heating" sub="Trinamic drivers only." checked={drivers} onChange={setDrivers} />
      </SettingsCard>

      <SettingsCard title="Valve Oil UI" icon={<RotateCcw size={14} />}>
        <Row title="Reset Valve Oil UI preferences" sub="Puts this app's own preferences back to their defaults: the theme and other choices kept in this browser, and those kept on the printer. Fluidd's settings, cameras, and presets stay as they are.">
          <button className="btn btn-sm btn-danger" onClick={resetPreferences}>
            <RotateCcw size={13} /> Reset
          </button>
        </Row>
      </SettingsCard>
    </>
  )
}

// localStorage keys that aren't preferences: the dev server's Moonraker address.
const KEEP = new Set(['vo-moonraker'])

async function resetPreferences() {
  const ok = await confirm({
    title: 'Reset Valve Oil UI preferences',
    body: "Put Valve Oil UI's preferences back to their defaults, in this browser and on the printer? Fluidd's settings aren't changed.",
    confirm: 'Reset',
    danger: true,
  })
  if (!ok) return
  await attempt(async () => {
    const stored = await getItem<Record<string, unknown>>(VALVE_OIL_UI)
    for (const key of Object.keys(stored ?? {})) await deleteItem(VALVE_OIL_UI, key)
    try {
      for (const key of Object.keys(localStorage)) if (key.startsWith('vo-') && !KEEP.has(key)) localStorage.removeItem(key)
    } catch {
      // Private browsing can refuse storage; there's nothing kept there then.
    }
    // Screens keep what they read when the page loaded, so start again from the defaults.
    location.reload()
  })
}

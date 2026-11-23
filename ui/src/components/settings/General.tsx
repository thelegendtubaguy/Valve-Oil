import { Moon, RotateCcw, SlidersHorizontal, Sun, TriangleAlert } from 'lucide-react'
import { confirm, Segmented } from '../ui'
import { attempt } from '../toast'
import { setTheme, useTheme } from '../../lib/theme'
import { deleteItem, getItem, VALVE_OIL_UI } from '../../state/database'
import { Row, SettingsCard, TextField, UiToggleRow } from './common'
import { useUiSetting } from './fluidd'
import { browserLanguage, LANGUAGES, languageStore, setLanguage, t, type LangChoice } from '../../i18n'
import { useStore } from '../../lib/store'

export function GeneralSettings() {
  const [theme] = useTheme()
  const [name, setName] = useUiSetting('general.instanceName')

  return (
    <>
      <SettingsCard title={t('settings.general.title')} icon={<SlidersHorizontal size={14} />}>
        <Row title={t('settings.general.printer_name')} sub={t('settings.general.printer_name_sub')} wide>
          {/* i18n-ignore: Fluidd's own default name */}
          <TextField value={name} onSave={setName} placeholder="fluidd" label={t('settings.general.printer_name')} />
        </Row>
        <LanguageRow />
        <Row title={t('settings.general.theme')} sub={t('settings.general.theme_sub')}>
          <Segmented
            label={t('settings.general.theme')}
            value={theme}
            onChange={setTheme}
            options={[
              { value: 'dark', label: <><Moon size={14} /> {t('settings.general.theme_dark')}</> },
              { value: 'light', label: <><Sun size={14} /> {t('settings.general.theme_light')}</> },
            ]}
          />
        </Row>
        <UiToggleRow path="general.confirmOnEstop" title={t('settings.general.confirm_estop')} />
      </SettingsCard>

      <SettingsCard title={t('settings.general.warnings')} icon={<TriangleAlert size={14} />} footer={t('settings.general.warnings_footer')}>
        <UiToggleRow path="warnings.warnOnCpuThrottled" title={t('settings.general.warn_cpu')} sub={t('settings.general.warn_cpu_sub')} />
        <UiToggleRow path="warnings.warnOnStepperDriverOverheating" title={t('settings.general.warn_drivers')} sub={t('settings.general.warn_drivers_sub')} />
      </SettingsCard>

      <SettingsCard title="Valve Oil UI" icon={<RotateCcw size={14} />}>
        <Row title={t('settings.general.reset')} sub={t('settings.general.reset_sub')}>
          <button className="btn btn-sm btn-danger" onClick={resetPreferences}>
            <RotateCcw size={13} /> {t('settings.general.reset_button')}
          </button>
        </Row>
      </SettingsCard>
    </>
  )
}

/** The language picker. Kept in this browser; Fluidd's language is its own. */
function LanguageRow() {
  const choice = useStore(languageStore, (s) => s.choice)
  const auto = LANGUAGES.find((l) => l.code === browserLanguage())!.name
  return (
    <Row title={t('settings.general.language')} sub={t('settings.general.language_sub')}>
      <select className="field w-auto" aria-label={t('settings.general.language')} value={choice} onChange={(e) => void setLanguage(e.target.value as LangChoice)}>
        <option value="auto">{t('settings.general.language_auto', { name: auto })}</option>
        {LANGUAGES.map((l) => (
          <option key={l.code} value={l.code}>
            {l.name}
          </option>
        ))}
      </select>
    </Row>
  )
}

// localStorage keys that aren't preferences: the dev server's Moonraker address.
const KEEP = new Set(['vo-moonraker'])

async function resetPreferences() {
  const ok = await confirm({
    title: t('settings.general.reset'),
    body: t('settings.general.reset_confirm'),
    confirm: t('settings.general.reset_button'),
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

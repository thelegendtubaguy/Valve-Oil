import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import { App } from './App'
import { startPrinterConnection } from './state/printer'
import { startUpdateCheck } from './lib/update-check'
import { startValveOilStatus } from './state/valve-oil'
import { initLanguage, languageStore } from './i18n'
import { useStore } from './lib/store'

startPrinterConnection()
startUpdateCheck()
startValveOilStatus()

/** Remounts the app when the language changes, so every message is looked up again. Printer state lives in stores. */
function Root() {
  const lang = useStore(languageStore, (s) => s.lang)
  return <App key={lang} />
}

void initLanguage().then(() =>
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <Root />
    </StrictMode>,
  ),
)

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import { App } from './App'
import { startPrinterConnection } from './state/printer'
import { startUpdateCheck } from './lib/update-check'
import { startValveOilStatus } from './state/valve-oil'

startPrinterConnection()
startUpdateCheck()
startValveOilStatus()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)

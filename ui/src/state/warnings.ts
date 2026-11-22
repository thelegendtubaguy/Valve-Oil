import { usePrinter } from './printer'
import voPrinterWarnings, { type VoPrinterWarning } from '../qidi/vo-printer-warnings'

export type { VoPrinterWarning }

/** Setup problems Valve Oil reports, shown until the printer stops reporting them. */
export function useWarnings(): VoPrinterWarning[] {
  const raw = usePrinter((s) => (s.conn === 'open' && s.klippy === 'ready' ? s.status.vo_printer_warnings : undefined))
  return voPrinterWarnings(raw)
}

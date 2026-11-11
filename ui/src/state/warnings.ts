import { usePrinter } from './printer'
import tltgPrinterWarnings, { type TltgPrinterWarning } from '../qidi/tltg-printer-warnings'

export type { TltgPrinterWarning }

/** Setup problems the optimized package reports, shown until the printer stops reporting them. */
export function useWarnings(): TltgPrinterWarning[] {
  const raw = usePrinter((s) => (s.conn === 'open' && s.klippy === 'ready' ? s.status.tltg_printer_warnings : undefined))
  return tltgPrinterWarnings(raw)
}

import { usePrinter } from './printer'
import voPrinterWarnings, { type VoPrinterWarning } from '../qidi/vo-printer-warnings'

export type { VoPrinterWarning }

/**
 * Setup problems Valve Oil reports, shown until the printer stops reporting them. While a page opens, the ones the
 * last page saw show until Klipper's own arrive, so the banner doesn't push the screen down once it's ready.
 */
export function useWarnings(): VoPrinterWarning[] {
  const raw = usePrinter((s) => {
    const opening = s.klippy === 'unknown' && s.objects.length > 0 && !s.connLost && s.conn !== 'closed'
    return (s.conn === 'open' && s.klippy === 'ready') || opening ? s.status.vo_printer_warnings : undefined
  })
  return voPrinterWarnings(raw)
}

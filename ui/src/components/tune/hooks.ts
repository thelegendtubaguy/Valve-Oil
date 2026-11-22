import { useEffect, useState } from 'react'
import { usePrinter } from '../../state/printer'

/** What the Tune tools need to know before they move the printer, as Fluidd decides it. */
export function usePrinterFlags() {
  const state = usePrinter((s) => s.status.print_stats?.state as string | undefined)
  const idle = usePrinter((s) => s.status.idle_timeout?.state as string | undefined)
  const homedAxes = usePrinter((s) => (s.status.toolhead?.homed_axes as string | undefined) ?? '')
  const printing = state === 'printing' || state === 'paused'
  return {
    printing,
    /** A print, or commands still running (idle_timeout reports Printing while G-code executes). */
    busy: printing || idle === 'Printing',
    homed: ['x', 'y', 'z'].every((a) => homedAxes.includes(a)),
  }
}

/** Whether a G-code command exists on this printer. */
export function useHasCommand() {
  const commands = usePrinter((s) => s.commands)
  return (c: string) => commands.includes(c)
}

export function useMedia(query: string) {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const m = window.matchMedia(query)
    const on = () => setMatch(m.matches)
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [query])
  return match
}

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Info, TriangleAlert } from 'lucide-react'
import { usePrinter } from '../../state/printer'
import { useJobActive } from '../../state/job'
import { useHomedAxes } from '../control/common'

/** What the Tune tools need to know before they move the printer, as Fluidd decides it. */
export function usePrinterFlags() {
  const printing = useJobActive()
  const idle = usePrinter((s) => s.status.idle_timeout?.state as string | undefined)
  const homedAxes = useHomedAxes()
  return {
    printing,
    /** A print, or commands still running (idle_timeout reports Printing while G-code executes). */
    busy: printing || idle === 'Printing',
    homed: ['x', 'y', 'z'].every((a) => homedAxes.includes(a)),
  }
}

/** An element's width, following resizes; 600 until measured. `now` measures it at once rather than at the first resize report. */
export function useWidth<T extends HTMLElement>(now = true) {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(600)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(() => setWidth(el.clientWidth || 600))
    ro.observe(el)
    if (now) setWidth(el.clientWidth || 600)
    return () => ro.disconnect()
  }, [now])
  return [ref, width] as const
}

/** A warning (or, with tone info, a note) in a Tune card; small ones sit under a control. */
export function Banner({ tone = 'warn', small, className = 'mt-3', children }: { tone?: 'warn' | 'info'; small?: boolean; className?: string; children: ReactNode }) {
  const Icon = tone === 'warn' ? TriangleAlert : Info
  return (
    <div className={`tone-${tone} ${className} flex items-start gap-2 rounded-lg px-3 ${small ? 'py-2 text-[12.5px]' : 'py-2.5'}`}>
      <Icon size={small ? 14 : 15} className="mt-0.5 flex-none" />
      <span className="text-ink">{children}</span>
    </div>
  )
}

/** A measured value in a tile, such as a mesh's range. */
export function Stat({ label, value, unit, hot }: { label: string; value: string; unit: string; hot?: boolean }) {
  return (
    <div className={`tile ${hot ? 'hot' : ''}`}>
      <span className="section-label">{label}</span>
      <span className="v num">
        {value} <small>{unit}</small>
      </span>
    </div>
  )
}

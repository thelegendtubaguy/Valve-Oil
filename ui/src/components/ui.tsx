import { useEffect, useRef, useState, type ReactNode } from 'react'
import { X } from 'lucide-react'

export function Card({ title, icon, aside, children, className = '' }: { title?: ReactNode; icon?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {title && (
        <h3 className="card-title">
          {icon}
          {title}
          {aside && <span className="aside">{aside}</span>}
        </h3>
      )}
      {children}
    </section>
  )
}

/**
 * A button that runs its action only after being held, for actions that are hard to undo.
 * Works with mouse, touch, and keyboard (hold Space or Enter).
 */
export function HoldButton({ onHold, children, holdMs = 1200, className = '', disabled }: { onHold: () => void; children: ReactNode; holdMs?: number; className?: string; disabled?: boolean }) {
  const [holding, setHolding] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const start = () => {
    if (disabled || timer.current) return
    setHolding(true)
    timer.current = setTimeout(() => {
      timer.current = null
      setHolding(false)
      onHold()
    }, holdMs)
  }
  const stop = () => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    setHolding(false)
  }
  useEffect(() => stop, [])
  return (
    <button
      type="button"
      disabled={disabled}
      className={`btn hold btn-danger ${holding ? 'holding' : ''} ${className}`}
      style={{ ['--hold-ms' as string]: `${holdMs}ms` }}
      onPointerDown={start}
      onPointerUp={stop}
      onPointerLeave={stop}
      onPointerCancel={stop}
      onContextMenu={(e) => e.preventDefault()}
      onKeyDown={(e) => (e.key === ' ' || e.key === 'Enter') && !e.repeat && (e.preventDefault(), start())}
      onKeyUp={(e) => (e.key === ' ' || e.key === 'Enter') && stop()}
    >
      <span className="hold-fill" />
      <span>{children}</span>
    </button>
  )
}

export function Dialog({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true">
        <div className="flex items-center gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0 flex-1 text-[15px] font-semibold">{title}</div>
          <button className="iconbtn" onClick={onClose} aria-label="Close">
            <X size={17} />
          </button>
        </div>
        <div className="px-5 py-4">{children}</div>
        {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </div>
  )
}

export function Sparkline({ values, target, width = 80, height = 24, color = 'var(--orange)' }: { values: number[]; target?: number; width?: number; height?: number; color?: string }) {
  const pts = values.slice(-180)
  if (pts.length < 2) return <svg width={width} height={height} />
  let lo = Math.min(...pts, target ?? Infinity)
  let hi = Math.max(...pts, target ?? -Infinity)
  if (hi - lo < 4) {
    const mid = (hi + lo) / 2
    lo = mid - 2
    hi = mid + 2
  }
  const y = (v: number) => height - 2 - ((v - lo) / (hi - lo)) * (height - 4)
  const d = pts.map((v, i) => `${((i / (pts.length - 1)) * width).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  return (
    <svg width={width} height={height} aria-hidden>
      {target != null && target > 0 && <line x1={0} x2={width} y1={y(target)} y2={y(target)} stroke={color} strokeOpacity={0.35} strokeDasharray="3 3" />}
      <polyline points={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" />
    </svg>
  )
}

export function Toggle({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="relative inline-block h-5 w-[34px] flex-none rounded-full border-0 transition-colors disabled:opacity-45"
      style={{ background: checked ? 'var(--accent)' : 'var(--line-strong)' }}
    >
      <i className="absolute top-0.5 h-4 w-4 rounded-full bg-white transition-all" style={{ left: checked ? 16 : 2 }} />
    </button>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line-strong p-5 text-center text-ink-3">{children}</div>
}

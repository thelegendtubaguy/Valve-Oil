import { useEffect, useRef, useState, type ReactNode } from 'react'

/** A button with a small panel under it that stays open while you tick things in it. */
export function Popover({ button, label, children, align = 'right', className = 'btn btn-sm' }: { button: ReactNode; label: string; children: ReactNode; align?: 'left' | 'right'; className?: string }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: Event) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', esc)
    return () => (window.removeEventListener('pointerdown', close), window.removeEventListener('keydown', esc))
  }, [open])
  return (
    <div className="relative inline-flex" ref={ref}>
      <button type="button" className={`${className} ${open ? 'on' : ''}`} aria-haspopup="true" aria-expanded={open} aria-label={label} title={label} onClick={() => setOpen((o) => !o)}>
        {button}
      </button>
      {open && (
        <div className="menu top-[calc(100%+4px)] max-h-[70dvh] w-max max-w-[min(320px,calc(100vw-24px))] overflow-auto" style={align === 'right' ? { right: 0 } : { left: 0 }}>
          {children}
        </div>
      )}
    </div>
  )
}

/** A checkbox line inside a Popover. */
export function Check({ checked, onChange, children }: { checked: boolean; onChange: (v: boolean) => void; children: ReactNode }) {
  return (
    <label className="flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-1.5 font-medium hover:bg-hover">
      <input type="checkbox" className="h-4 w-4 flex-none accent-[var(--accent)]" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="min-w-0 flex-1">{children}</span>
    </label>
  )
}

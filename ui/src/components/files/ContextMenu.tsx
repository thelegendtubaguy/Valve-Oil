import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

export type MenuItem = { label: ReactNode; icon?: ReactNode; onSelect: () => void; danger?: boolean; disabled?: boolean } | 'sep' | null | false

/** A menu at a point: the file menu, from a right-click, a tap on a row, or its ⋯ button. Kept inside the window. */
export function ContextMenu({ x, y, items, header, onClose }: { x: number; y: number; items: MenuItem[]; header?: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState({ left: x, top: y })
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const { width, height } = el.getBoundingClientRect()
    setPos({ left: Math.max(8, Math.min(x, innerWidth - width - 8)), top: Math.max(8, y + height > innerHeight - 8 ? y - height : y) })
  }, [x, y])
  useEffect(() => {
    const close = (e: Event) => ref.current && !ref.current.contains(e.target as Node) && onClose()
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('pointerdown', close, true)
    window.addEventListener('keydown', esc)
    window.addEventListener('resize', onClose)
    return () => (window.removeEventListener('pointerdown', close, true), window.removeEventListener('keydown', esc), window.removeEventListener('resize', onClose))
  }, [onClose])
  const shown = items.filter(Boolean).filter((it, i, a) => it !== 'sep' || (i > 0 && i < a.length - 1 && a[i - 1] !== 'sep'))
  return createPortal(
    <div ref={ref} className="menu fixed max-h-[80dvh] overflow-auto" style={{ ...pos, position: 'fixed' }} role="menu" onContextMenu={(e) => e.preventDefault()}>
      {header && <div className="max-w-[260px] truncate px-2.5 pt-1.5 pb-1 text-[12px] font-semibold text-ink-3">{header}</div>}
      {shown.map((it, i) => {
        if (it === 'sep') return <div key={i} className="sep my-1" />
        const item = it as Exclude<MenuItem, 'sep' | null | false>
        return (
          <button key={i} type="button" role="menuitem" disabled={item.disabled} className={item.danger ? 'danger' : ''} onClick={() => (onClose(), item.onSelect())}>
            {item.icon}
            {item.label}
          </button>
        )
      })}
    </div>,
    document.body,
  )
}

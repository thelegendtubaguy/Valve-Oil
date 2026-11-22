// A card that folds to its title. The choice is kept in the browser, per card.
import { useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'

export function useFold(key: string, openByDefault = false) {
  const [open, setOpen] = useState(() => {
    const v = localStorage.getItem(key)
    return v == null ? openByDefault : v === '1'
  })
  const toggle = () =>
    setOpen((o) => {
      try {
        localStorage.setItem(key, o ? '0' : '1')
      } catch {}
      return !o
    })
  return [open, toggle] as const
}

/** The card's title as the button that folds it. */
export function FoldTitle({ open, onToggle, children }: { open: boolean; onToggle: () => void; children: ReactNode }) {
  return (
    <button type="button" className="card-fold" aria-expanded={open} onClick={onToggle}>
      <ChevronRight size={14} className={`chev ${open ? 'open' : ''}`} />
      {children}
    </button>
  )
}

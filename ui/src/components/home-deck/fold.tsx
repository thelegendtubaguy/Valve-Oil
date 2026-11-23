// A card that folds to its title. The choice is kept in the browser, per card.
import type { ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { useStored } from '../../lib/browser'

export function useFold(key: string, openByDefault = false) {
  const [v, set] = useStored(key, openByDefault ? '1' : '0')
  return [v === '1', () => set(v === '1' ? '0' : '1')] as const
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

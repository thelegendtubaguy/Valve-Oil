// Chooses which filaments of an imported list to add or replace. New filaments start selected; changed ones don't,
// as after a firmware update the change may be QIDI's.
import { useState } from 'react'
import { Dialog } from '../../ui'
import type { QidiFilamentImportItem } from '../../../qidi/qidi-filament-config'

const statusText = (item: QidiFilamentImportItem) =>
  item.status === 'new' ? 'Not in the current list' : item.status === 'changed' ? 'Differs from the current list' : `Its ID ${item.id} is already used by ${item.conflictKey}`

export function FilamentImportDialog({ items, busy, onImport, onClose }: { items: QidiFilamentImportItem[]; busy: boolean; onImport: (keys: string[]) => void; onClose: () => void }) {
  const [selected, setSelected] = useState(() => items.filter((i) => i.status === 'new').map((i) => i.key))
  const toggle = (key: string, on: boolean) => setSelected((s) => (on ? [...s, key] : s.filter((k) => k !== key)))
  return (
    <Dialog
      open
      onClose={onClose}
      title="Import filaments"
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={selected.length === 0 || busy} onClick={() => onImport(selected)}>
            Import
          </button>
        </>
      }
    >
      {items.length ? (
        <>
          <p className="mt-0 mb-3 text-ink-2">Choose the filaments to add or replace.</p>
          <div className="grid gap-1">
            {items.map((item) => (
              <label key={item.key} className={`flex items-start gap-2.5 rounded-lg px-2 py-1.5 ${item.status === 'conflict' ? 'opacity-60' : 'hover:bg-hover'}`}>
                <input type="checkbox" className="mt-1 accent-[var(--accent)]" disabled={item.status === 'conflict'} checked={selected.includes(item.key)} onChange={(e) => toggle(item.key, e.target.checked)} />
                <span className="min-w-0">
                  <span className="font-semibold">{item.key}</span> <span className="code text-ink-3">{item.id}</span>
                  <span className={`block text-[12px] ${item.status === 'conflict' ? 'text-bad' : 'text-ink-3'}`}>{statusText(item)}</span>
                </span>
              </label>
            ))}
          </div>
        </>
      ) : (
        <p className="m-0 text-ink-2">Every filament in this file is already in the list.</p>
      )}
    </Dialog>
  )
}

// Which slot each tool loads, as the fork's tool mapping dialog sets it. Saves only the tools that changed, in the
// format the print dialog uses.
import { useEffect, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { Dialog, MenuButton } from '../ui'
import { attempt } from '../toast'
import { boxCommands, QIDI_BOX_EXTERNAL_SLOT, type BoxState, type QidiBoxSlot } from '../../state/box'
import { runGcode } from '../../state/printer'

const slotText = (s: QidiBoxSlot) => `${s.label} · ${s.present ? s.name || 'Unknown filament' : 'Empty'}`

function Dot({ color }: { color: string | null }) {
  return <span className="inline-block h-3.5 w-3.5 flex-none rounded-full" style={{ background: color ?? 'transparent', boxShadow: 'inset 0 0 0 1px rgb(128 128 128 / 0.6)' }} />
}

export function ToolMapDialog({ box, open, onClose }: { box: BoxState; open: boolean; onClose: () => void }) {
  const boxSlots = box.slots.filter((s) => s.index !== QIDI_BOX_EXTERNAL_SLOT).length
  // Tools for every Box slot, plus any mapped beyond them.
  const mapped = Object.keys(box.toolMap).reduce((most, t) => Math.max(most, Number(t) + 1), 0)
  const count = Math.min(16, Math.max(boxSlots, mapped, 1))
  const [draft, setDraft] = useState<Record<number, number>>({})
  useEffect(() => {
    if (open) setDraft({ ...box.toolMap })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  const changed = Object.entries(draft).filter(([t, s]) => box.toolMap[Number(t)] !== s && box.slots.some((x) => x.index === s))
  const save = async () => {
    if (box.mappingLocked) return
    if (changed.length) await attempt(() => runGcode(changed.map(([t, s]) => boxCommands.mapTool(Number(t), s)).join('\n')))
    onClose()
  }
  // T0 → 1A, T1 → 1B, and so on for every Box slot.
  const setDefault = () => setDraft((d) => ({ ...d, ...Object.fromEntries(Array.from({ length: Math.min(16, boxSlots) }, (_, i) => [i, i])) }))
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Tool mapping"
      footer={
        <>
          <button className="btn btn-ghost mr-auto" onClick={setDefault}>
            Default
          </button>
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={box.mappingLocked} onClick={save}>
            Save
          </button>
        </>
      }
    >
      <p className="mt-0 mb-3.5 text-ink-2">Choose the slot each tool loads, for tool changes and prints that do not set their own mapping. The print dialog sets the mapping again for the print it starts.</p>
      {box.mappingLocked && <div className="tone-warn mb-3 rounded-lg px-3 py-2 text-ink">Tool mapping can be changed while the printer is idle.</div>}
      <div className="grid gap-2 sm:grid-cols-2">
        {Array.from({ length: count }, (_, t) => {
          const slot = box.slots.find((s) => s.index === draft[t])
          return (
            <div key={t} className="flex items-center gap-2.5">
              <span className="w-8 flex-none text-right font-bold">T{t}</span>
              <div className="min-w-0 flex-1 [&>div]:w-full">
                <MenuButton
                  align="left"
                  label={`Slot for T${t}`}
                  className="field flex items-center gap-2 text-left disabled:opacity-50"
                  button={
                    <>
                      {slot ? <Dot color={slot.present ? slot.color : null} /> : null}
                      <span className={`min-w-0 flex-1 truncate ${slot ? '' : 'text-ink-3'}`}>{slot ? slotText(slot) : 'Not mapped'}</span>
                      <ChevronDown size={14} className="flex-none text-ink-3" />
                    </>
                  }
                  items={box.slots.map((s) => ({ label: slotText(s), icon: <Dot color={s.present ? s.color : null} />, onSelect: () => setDraft((d) => ({ ...d, [t]: s.index })) }))}
                />
              </div>
            </div>
          )
        })}
      </div>
    </Dialog>
  )
}

// Which slot each tool loads, as the fork's tool mapping dialog sets it. Saves only the tools that changed, in the
// format the print dialog uses.
import { useEffect, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { CancelButton, Dialog, MenuButton } from '../ui'
import { attempt } from '../toast'
import { boxCommands, QIDI_BOX_EXTERNAL_SLOT, type BoxState } from '../../state/box'
import { runGcode } from '../../state/printer'
import { slotText } from './BoxTools'
import { t } from '../../i18n'

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
      title={t('box.screen.tool_mapping')}
      footer={
        <>
          <button className="btn btn-ghost mr-auto" onClick={setDefault}>
            {t('box.tool_map.default')}
          </button>
          <CancelButton onClick={onClose} />
          <button className="btn btn-primary" disabled={box.mappingLocked} onClick={save}>
            {t('common.save')}
          </button>
        </>
      }
    >
      <p className="mt-0 mb-3.5 text-ink-2">{t('box.tool_map.intro')}</p>
      {box.mappingLocked && <div className="tone-warn mb-3 rounded-lg px-3 py-2 text-ink">{t('box.tool_map.locked')}</div>}
      <div className="grid gap-2 sm:grid-cols-2">
        {Array.from({ length: count }, (_, tool) => {
          const slot = box.slots.find((s) => s.index === draft[tool])
          return (
            <div key={tool} className="flex items-center gap-2.5">
              <span className="w-8 flex-none text-right font-bold">T{tool}</span>
              <div className="min-w-0 flex-1 [&>div]:w-full">
                <MenuButton
                  align="left"
                  label={t('box.tool_map.slot_for', { tool: `T${tool}` })}
                  className="field flex items-center gap-2 text-left disabled:opacity-50"
                  button={
                    <>
                      {slot ? <Dot color={slot.present ? slot.color : null} /> : null}
                      <span className={`min-w-0 flex-1 truncate ${slot ? '' : 'text-ink-3'}`}>{slot ? slotText(slot) : t('box.tool_map.not_mapped')}</span>
                      <ChevronDown size={14} className="flex-none text-ink-3" />
                    </>
                  }
                  items={box.slots.map((s) => ({ label: slotText(s), icon: <Dot color={s.present ? s.color : null} />, onSelect: () => setDraft((d) => ({ ...d, [tool]: s.index })) }))}
                />
              </div>
            </div>
          )
        })}
      </div>
    </Dialog>
  )
}

// The toolhead's tool buttons on a QIDI printer, as the fork's ToolChangeCommands shows them: only the tools a Box
// can load, each in its slot's color, with the tool feeding the extruder marked. QIDI defines T0–T15 whatever Boxes
// are connected; T_MANAGER pauses with QDE_004_027 for a tool mapped to the external spool or past the connected
// Boxes, so those aren't offered.
import { Card } from '../ui'
import { attempt } from '../toast'
import { boxCommands, useBox, type BoxState, type QidiBoxSlot } from '../../state/box'
import { runGcode, usePrinter } from '../../state/printer'
import { t } from '../../i18n'

/** A slot's label and what it holds. */
export const slotText = (s: QidiBoxSlot) => `${s.label} · ${s.present ? s.name || t('box.unknown_filament') : t('box.empty')}`

function useToolButtons(box: BoxState) {
  const commands = usePrinter((s) => s.commands)
  return box.managesTools ? box.toolButtons.filter((b) => commands.includes(`T${b.tool}`)) : []
}

/** The tool buttons, or nothing when no Box tool can be loaded (no Box, or the Box is off). */
export function BoxTools({ box: given }: { box?: BoxState }) {
  const own = useBox()
  const box = given ?? own
  const ready = usePrinter((s) => s.klippy === 'ready')
  const buttons = useToolButtons(box)
  if (buttons.length === 0) return null
  const disabled = !ready || box.printState === 'printing' || box.busy
  // Up to six to a row, in even rows.
  const cols = Math.ceil(buttons.length / Math.ceil(buttons.length / 6))
  return (
    <div className="grid gap-1.5" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
      {buttons.map(({ tool, slot, active }) => (
        <button
          key={tool}
          type="button"
          disabled={disabled}
          aria-pressed={active}
          title={slotText(slot)}
          onClick={() => attempt(() => runGcode(boxCommands.tool(tool)))}
          className={`btn px-1.5! ${active ? 'tool-on disabled:opacity-100!' : ''}`}
        >
          {slot.present && slot.color && <span className="h-3.5 w-3.5 flex-none rounded-full" style={{ background: slot.color, boxShadow: 'inset 0 0 0 1px rgb(128 128 128 / 0.6)' }} />}
          T{tool}
        </button>
      ))}
    </div>
  )
}

/** BoxTools in a card titled Tools, for the Control screen. Renders nothing when there are no tools. */
export function BoxToolsCard() {
  const box = useBox()
  if (useToolButtons(box).length === 0) return null
  return (
    <Card title={t('box.tools.title')} aside={box.printState === 'printing' ? t('box.tools.unavailable_printing') : undefined}>
      <BoxTools box={box} />
    </Card>
  )
}

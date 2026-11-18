import { useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, Pencil, Plus, Thermometer, Trash2 } from 'lucide-react'
import { confirm, Dialog, Empty } from '../ui'
import { attempt, toast } from '../toast'
import { prettyName } from '../../lib/format'
import { usePrinter } from '../../state/printer'
import { usePresets, type PresetValue, type TemperaturePreset } from '../../state/presets'
import { SettingsCard } from './common'

type Target = { key: string; label: string; type: PresetValue['type'] }

/** A heater's or temperature fan's name as people know it: Nozzle, Bed, Chamber, Box 1 heater. */
function targetLabel(key: string) {
  if (key === 'extruder') return 'Nozzle'
  if (key === 'heater_bed') return 'Bed'
  const box = /^heater_box(\d+)$/.exec(key)
  return box ? `Box ${box[1]} heater` : prettyName(key)
}

/**
 * What a preset can set: the printer's heaters, then its temperature fans. Keyed by the short name, as Fluidd keys
 * preset values ("extruder", "chamber", "heater_box1").
 */
function useTargets(): Target[] {
  // A joined string, so status updates that don't change the list don't re-render.
  const keys = usePrinter((s) => {
    // Klipper's heaters object lists them in config order; without it, the heater objects the printer reports.
    const heaters: string[] = s.status.heaters?.available_heaters ?? Object.keys(s.status).filter((k) => k === 'extruder' || k === 'heater_bed' || k.startsWith('heater_generic '))
    return [...heaters, ...Object.keys(s.status).filter((k) => k.startsWith('temperature_fan '))].join('|')
  })
  return useMemo(
    () =>
      keys
        .split('|')
        .filter(Boolean)
        .map((k) => {
          const key = k.split(' ').pop()!
          return { key, label: targetLabel(key), type: k.startsWith('temperature_fan ') ? ('fan' as const) : ('heater' as const) }
        }),
    [keys],
  )
}

const newId = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`)

export function PresetSettings() {
  const [presets, save] = usePresets()
  const targets = useTargets()
  const [editing, setEditing] = useState<TemperaturePreset | null>(null)
  const store = (next: TemperaturePreset[]) => attempt(() => save(next))
  const label = (key: string) => targets.find((t) => t.key === key)?.label ?? targetLabel(key)

  const add = () => {
    const values: TemperaturePreset['values'] = {}
    for (const t of targets) values[t.key] = { value: 0, type: t.type, active: true }
    setEditing({ id: -1, name: '', values })
  }
  const move = (i: number, by: number) => {
    const next = [...presets]
    const [p] = next.splice(i, 1)
    next.splice(i + by, 0, p)
    store(next)
  }
  const remove = async (p: TemperaturePreset) => {
    if (await confirm({ title: `Remove ${p.name}`, body: `Are you sure you want to remove the preset ${p.name}?`, confirm: 'Remove', danger: true })) store(presets.filter((x) => x.id !== p.id))
  }

  return (
    <SettingsCard
      title="Temperature presets"
      icon={<Thermometer size={14} />}
      aside={
        <button className="btn btn-sm" onClick={add}>
          <Plus size={13} /> Add preset
        </button>
      }
      footer="Shared with Fluidd. The Control screen's presets set these targets, then send the preset's G-code."
    >
      {presets.length === 0 ? (
        <Empty>No presets yet. Add one for each material you print, such as PLA or PETG.</Empty>
      ) : (
        presets.map((p, i) => {
          const active = Object.entries(p.values).filter(([, v]) => v.active)
          return (
            <div key={String(p.id)} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3 first:pt-0 last:pb-0">
              <div className="min-w-[180px] flex-1">
                <div className="font-semibold">{p.name}</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {active.length === 0 && !p.gcode && <span className="text-[12.5px] text-ink-3">Sets nothing</span>}
                  {active.map(([k, v]) => (
                    <span key={k} className="chip num">
                      {label(k)} <b className="text-ink">{v.value}°</b>
                    </span>
                  ))}
                  {p.gcode?.trim() && <span className="chip code">G-code</span>}
                </div>
              </div>
              <div className="ml-auto flex gap-0.5">
                <button className="iconbtn" disabled={i === 0} onClick={() => move(i, -1)} aria-label={`Move ${p.name} up`} title="Move up">
                  <ArrowUp size={16} />
                </button>
                <button className="iconbtn" disabled={i === presets.length - 1} onClick={() => move(i, 1)} aria-label={`Move ${p.name} down`} title="Move down">
                  <ArrowDown size={16} />
                </button>
                <button className="iconbtn" onClick={() => setEditing(p)} aria-label={`Edit ${p.name}`} title="Edit preset">
                  <Pencil size={16} />
                </button>
                <button className="iconbtn" onClick={() => remove(p)} aria-label={`Remove ${p.name}`} title="Remove preset">
                  <Trash2 size={16} />
                </button>
              </div>
            </div>
          )
        })
      )}
      {editing && (
        <PresetDialog
          preset={editing}
          targets={targets}
          onClose={() => setEditing(null)}
          onSave={async (p) => {
            const next = p.id === -1 ? [...presets, { ...p, id: newId() }] : presets.map((x) => (x.id === p.id ? p : x))
            try {
              await save(next)
              setEditing(null)
            } catch (e) {
              toast(e instanceof Error ? e.message : String(e), 'error')
            }
          }}
        />
      )}
    </SettingsCard>
  )
}

function PresetDialog({ preset, targets, onClose, onSave }: { preset: TemperaturePreset; targets: Target[]; onClose: () => void; onSave: (p: TemperaturePreset) => void }) {
  // Heaters added since the preset was made start off, so editing it doesn't change what it sets.
  const [p, setP] = useState<TemperaturePreset>(() => {
    const values = { ...preset.values }
    for (const t of targets) values[t.key] ??= { value: 0, type: t.type, active: false }
    return { ...preset, values }
  })
  const setValue = (key: string, v: Partial<PresetValue>) => setP((x) => ({ ...x, values: { ...x.values, [key]: { ...x.values[key], ...v } } }))
  const shown = [...targets.map((t) => t.key), ...Object.keys(p.values).filter((k) => !targets.some((t) => t.key === k))]
  const invalid = !p.name.trim() || shown.some((k) => p.values[k].active && !(p.values[k].value > 0))

  return (
    <Dialog
      open
      onClose={onClose}
      title={preset.id === -1 ? 'Add preset' : 'Edit preset'}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={invalid} onClick={() => onSave({ ...p, name: p.name.trim(), gcode: p.gcode?.trim() ? p.gcode : undefined })}>
            {preset.id === -1 ? 'Add' : 'Save'}
          </button>
        </>
      }
    >
      <div className="grid gap-4">
        <label className="grid gap-1.5">
          <span className="lbl">Preset name</span>
          <input className="field" value={p.name} autoFocus={preset.id === -1} placeholder="PLA" onChange={(e) => setP((x) => ({ ...x, name: e.target.value }))} />
        </label>
        <div className="grid gap-1.5">
          <span className="lbl">Tick each target this preset sets</span>
          <div className="grid divide-y divide-line rounded-[10px] px-3 shadow-[inset_0_0_0_1px_var(--line)]">
            {shown.map((key) => {
              const v = p.values[key]
              const t = targets.find((x) => x.key === key)
              return (
                <div key={key} className="flex items-center gap-3 py-2">
                  <label className="flex min-w-0 flex-1 items-center gap-2.5">
                    <input type="checkbox" className="h-4 w-4 accent-[var(--accent)]" checked={v.active} onChange={(e) => setValue(key, { active: e.target.checked })} />
                    <span className="truncate font-medium">{t?.label ?? targetLabel(key)}</span>
                    {!t && <span className="text-[12px] text-ink-3">Not on this printer</span>}
                  </label>
                  <div className="relative w-[120px] flex-none">
                    <input
                      className="field num pr-9 text-right"
                      inputMode="decimal"
                      aria-label={`${t?.label ?? key} target`}
                      disabled={!v.active}
                      value={v.value || ''}
                      placeholder="0"
                      onChange={(e) => setValue(key, { value: Number(e.target.value) || 0 })}
                    />
                    <span className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-[12px] font-semibold text-ink-3">°C</span>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
        <label className="grid gap-1.5">
          <span className="lbl">G-code, sent after the targets (optional)</span>
          <textarea className="field code min-h-[70px]" rows={3} spellCheck={false} value={p.gcode ?? ''} onChange={(e) => setP((x) => ({ ...x, gcode: e.target.value }))} />
        </label>
      </div>
    </Dialog>
  )
}

import { useMemo, useState } from 'react'
import { ArrowDown, ArrowUp, Pencil, Plus, Thermometer, Trash2 } from 'lucide-react'
import { CancelButton, confirm, Dialog, Empty, Field } from '../ui'
import { attempt } from '../toast'
import { uuid } from '../../lib/browser'
import { prettyName } from '../../lib/format'
import { usePrinter } from '../../state/printer'
import { usePresets, type PresetValue, type TemperaturePreset } from '../../state/presets'
import { SettingsCard } from './common'
import { t } from '../../i18n'

type Target = { key: string; label: string; type: PresetValue['type'] }

/** A heater's or temperature fan's name as people know it: Nozzle, Bed, Chamber, Box 1 heater. */
function targetLabel(key: string) {
  if (key === 'extruder') return t('settings.presets.nozzle')
  if (key === 'heater_bed') return t('settings.presets.bed')
  const box = /^heater_box(\d+)$/.exec(key)
  return box ? t('settings.presets.box_heater', { n: box[1] }) : prettyName(key)
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
    if (await confirm({ title: t('settings.common.remove_named', { name: p.name }), body: t('settings.presets.remove_confirm', { name: p.name }), confirm: t('common.remove'), danger: true })) store(presets.filter((x) => x.id !== p.id))
  }

  return (
    <SettingsCard
      title={t('settings.presets.title')}
      icon={<Thermometer size={14} />}
      aside={
        <button className="btn btn-sm" onClick={add}>
          <Plus size={13} /> {t('settings.presets.add')}
        </button>
      }
      footer={t('settings.presets.footer')}
    >
      {presets.length === 0 ? (
        <Empty>{t('settings.presets.empty')}</Empty>
      ) : (
        presets.map((p, i) => {
          const active = Object.entries(p.values).filter(([, v]) => v.active)
          return (
            <div key={String(p.id)} className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3 first:pt-0 last:pb-0">
              <div className="min-w-[180px] flex-1">
                <div className="font-semibold">{p.name}</div>
                <div className="mt-1 flex flex-wrap gap-1.5">
                  {active.length === 0 && !p.gcode && <span className="text-[12.5px] text-ink-3">{t('settings.presets.sets_nothing')}</span>}
                  {active.map(([k, v]) => (
                    <span key={k} className="chip num">
                      {label(k)} <b className="text-ink">{v.value}°</b>
                    </span>
                  ))}
                  {p.gcode?.trim() && <span className="chip code">G-code</span>}
                </div>
              </div>
              <div className="ml-auto flex gap-0.5">
                <button className="iconbtn" disabled={i === 0} onClick={() => move(i, -1)} aria-label={t('settings.presets.move_up_named', { name: p.name })} title={t('settings.presets.move_up')}>
                  <ArrowUp size={16} />
                </button>
                <button className="iconbtn" disabled={i === presets.length - 1} onClick={() => move(i, 1)} aria-label={t('settings.presets.move_down_named', { name: p.name })} title={t('settings.presets.move_down')}>
                  <ArrowDown size={16} />
                </button>
                <button className="iconbtn" onClick={() => setEditing(p)} aria-label={t('settings.common.edit_named', { name: p.name })} title={t('settings.presets.edit')}>
                  <Pencil size={16} />
                </button>
                <button className="iconbtn" onClick={() => remove(p)} aria-label={t('settings.common.remove_named', { name: p.name })} title={t('settings.presets.remove')}>
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
          onSave={(p) =>
            attempt(async () => {
              await save(p.id === -1 ? [...presets, { ...p, id: uuid() }] : presets.map((x) => (x.id === p.id ? p : x)))
              setEditing(null)
            })
          }
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
      title={preset.id === -1 ? t('settings.presets.add') : t('settings.presets.edit')}
      footer={
        <>
          <CancelButton onClick={onClose} />
          <button className="btn btn-primary" disabled={invalid} onClick={() => onSave({ ...p, name: p.name.trim(), gcode: p.gcode?.trim() ? p.gcode : undefined })}>
            {preset.id === -1 ? t('common.add') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 gap-4">
        <Field label={t('settings.presets.name')} className="grid-cols-1 gap-1.5">
          {/* i18n-ignore: the placeholder is a material's name */}
          <input className="field" value={p.name} autoFocus={preset.id === -1} placeholder="PLA" onChange={(e) => setP((x) => ({ ...x, name: e.target.value }))} />
        </Field>
        <div className="grid grid-cols-1 gap-1.5">
          <span className="lbl">{t('settings.presets.targets')}</span>
          <div className="grid grid-cols-1 divide-y divide-line rounded-[10px] px-3 shadow-[inset_0_0_0_1px_var(--line)]">
            {shown.map((key) => {
              const v = p.values[key]
              const target = targets.find((x) => x.key === key)
              return (
                <div key={key} className="flex flex-wrap items-center gap-3 py-2">
                  <label className="flex min-w-0 flex-1 items-center gap-2.5">
                    <input type="checkbox" className="h-4 w-4 accent-[var(--accent)]" checked={v.active} onChange={(e) => setValue(key, { active: e.target.checked })} />
                    <span className="min-w-0 font-medium break-words">{target?.label ?? targetLabel(key)}</span>
                    {!target && <span className="text-[12px] text-ink-3">{t('settings.presets.not_on_printer')}</span>}
                  </label>
                  <div className="relative w-[120px] flex-none">
                    <input
                      className="field num pr-9 text-right"
                      inputMode="decimal"
                      aria-label={t('settings.presets.target_named', { name: target?.label ?? key })}
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
        <Field label={t('settings.presets.gcode')} className="grid-cols-1 gap-1.5">
          <textarea className="field code min-h-[70px]" rows={3} spellCheck={false} value={p.gcode ?? ''} onChange={(e) => setP((x) => ({ ...x, gcode: e.target.value }))} />
        </Field>
      </div>
    </Dialog>
  )
}

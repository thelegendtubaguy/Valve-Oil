// Valve Oil's Box load and unload motion: reads vo_box_feed_motion and edits only through VO_BOX_MOTION, as the
// fork's QidiBoxMotion does. Each printer publishes its own settings (the Plus 5 has no per-slot overrides).
import { useState } from 'react'
import { confirm, SectionLabel } from '../ui'
import { qidiBoxSlotLabel, qidiBoxSlotNumber } from '../../qidi/qidi-box'
import { runGcode, usePrinter } from '../../state/printer'
import { gcode } from '../control/gcode'
import { t, type MessageKey } from '../../i18n'
import { tr } from '../../i18n/rich'
import { useJobActive } from '../../state/job'
import { errorText } from '../../lib/browser'

// Fallback for module versions that don't publish their fields.
const FIELDS = ['fast_length', 'fast_speed_1', 'fast_speed_2', 'approach_speed', 'load_accel', 'unload_speed_1', 'unload_speed_2', 'unload_accel', 'unload_extruder_length']
// Settings that apply to every slot and have no per-slot override.
const GLOBAL_ONLY = ['unload_extruder_length']

// The fields with a label; any other the module publishes shows its own name.
const LABELS: Record<string, MessageKey> = {
  approach_speed: 'box.motion.labels.approach_speed',
  hub_speed: 'box.motion.labels.hub_speed',
  fast_length: 'box.motion.labels.fast_length',
  fast_speed: 'box.motion.labels.fast_speed',
  fast_speed_1: 'box.motion.labels.fast_speed_1',
  fast_speed_2: 'box.motion.labels.fast_speed_2',
  load_accel: 'box.motion.labels.load_accel',
  park_speed: 'box.motion.labels.park_speed',
  unload_accel: 'box.motion.labels.unload_accel',
  unload_extruder_length: 'box.motion.labels.unload_extruder_length',
  unload_speed: 'box.motion.labels.unload_speed',
  unload_speed_1: 'box.motion.labels.unload_speed_1',
  unload_speed_2: 'box.motion.labels.unload_speed_2',
}

type Values = Record<string, unknown>
type Limit = { min: number; max: number; unit?: string }
type Part = Record<string, unknown> & { state?: string; error?: string | null }
type Motion = {
  active?: boolean
  status?: string
  fields?: string[]
  global_only?: string[]
  limits: Record<string, Limit>
  vendor: Record<string, number>
  settings: Values
  problems?: string[]
  vendor_keys?: string[]
  slots: Record<string, { overrides?: Values; load?: Part; unload?: Part } | null>
}

const isSet = (v: unknown) => v !== null && v !== undefined

export function BoxMotion() {
  const raw = usePrinter((s) => s.status.vo_box_feed_motion)
  const printing = useJobActive()
  const ready = usePrinter((s) => s.klippy === 'ready')
  const [open, setOpen] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, Record<string, string>>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [pending, setPending] = useState(false)

  const motion = raw != null && typeof raw === 'object' ? (raw as Motion) : null
  const active = !!(motion?.active && motion.settings && motion.vendor && motion.limits)
  const locked = pending || printing || !ready

  if (!motion) return null

  const notes = [...(motion.problems ?? []), ...(motion.vendor_keys?.length ? [t('box.motion.vendor_keys', { keys: motion.vendor_keys.join(', ') })] : [])]
  const slots = motion.slots ?? {}
  // A printer whose settings all apply to every slot, such as the Plus 5, publishes no slots.
  const slotKeys = Object.keys(slots)
    .filter((k) => qidiBoxSlotNumber(k) != null && slots[k])
    .sort((a, b) => (qidiBoxSlotNumber(a) ?? 0) - (qidiBoxSlotNumber(b) ?? 0))

  const fieldsFor = (scope: string) => {
    const fields = motion.fields ?? FIELDS
    const globalOnly = motion.global_only ?? GLOBAL_ONLY
    return fields.filter((f) => motion.limits?.[f] && (scope === 'global' || !globalOnly.includes(f)))
  }
  const limitText = (f: string) => {
    const l = motion.limits?.[f]
    return l ? `${l.min}–${l.max} ${l.unit ?? ''}`.trim() : ''
  }
  const invalid = (f: string, rawValue: unknown) => {
    const v = String(isSet(rawValue) ? rawValue : '').trim()
    const l = motion.limits?.[f]
    if (v === '' || !l) return false
    const n = Number(v)
    return !/^\d+$/.test(v) || n < l.min || n > l.max
  }
  // Status publishes an unusable saved value as its repr string, or as an out-of-range number.
  const badSaved = (f: string, v: unknown) => isSet(v) && (typeof v !== 'number' || invalid(f, v))
  const values = (scope: string): Values => (scope === 'global' ? motion.settings : slots[scope]?.overrides) ?? {}
  const placeholder = (scope: string, f: string) => {
    if (scope !== 'global') {
      const g = motion.settings?.[f]
      if (isSet(g) && !badSaved(f, g)) return String(g)
    }
    return String(motion.vendor?.[f] ?? '')
  }

  // Changed parameters only; a cleared field, or an untouched unusable saved value, is unset with the scope's clear
  // word. Null while any field is invalid.
  const changes = (scope: string): string[] | null => {
    const d = drafts[scope] ?? {}
    const cur = values(scope)
    const clear = scope === 'global' ? 'vendor' : 'global'
    const params: string[] = []
    for (const f of fieldsFor(scope)) {
      if (!(f in d)) {
        if (badSaved(f, cur[f])) params.push(`${f.toUpperCase()}=${clear}`)
        continue
      }
      const v = String(d[f] ?? '').trim()
      if (invalid(f, v)) return null
      if (v === '') {
        if (isSet(cur[f])) params.push(`${f.toUpperCase()}=${clear}`)
      } else if (Number(v) !== cur[f]) params.push(gcode`${f.toUpperCase()}=${Number(v)}`)
    }
    return params
  }
  const hasChanges = (scope: string) => (changes(scope)?.length ?? 0) > 0

  // The refused command's reason comes back with the request, so it's shown beside the fields.
  const run = async (scope: string, script: string, ask?: string) => {
    if (locked) return
    if (ask && !(await confirm({ title: t('box.motion.title'), body: ask, confirm: t('box.motion.reset'), danger: true }))) return
    setPending(true)
    setErrors(({ [scope]: _, ...rest }) => rest)
    try {
      await runGcode(script)
      setDrafts(({ [scope]: _, ...rest }) => rest)
    } catch (e) {
      setErrors((x) => ({ ...x, [scope]: errorText(e) }))
    }
    setPending(false)
  }
  const apply = (scope: string) => {
    const params = changes(scope)
    if (!params?.length) return
    run(scope, `VO_BOX_MOTION${scope === 'global' ? '' : gcode` SLOT=${qidiBoxSlotNumber(scope)}`} ${params.join(' ')}`)
  }
  const toggle = (key: string) => {
    if (open) {
      setDrafts(({ [open]: _, ...rest }) => rest)
      setErrors(({ [open]: _, ...rest }) => rest)
    }
    setOpen(open === key ? null : key)
  }

  // Called as a function, not a component, so the inputs keep focus across renders.
  const fields = (scope: string) => {
    const d = drafts[scope] ?? {}
    const saved = values(scope)
    return (
      <div className="grid grid-cols-2 gap-x-2.5 gap-y-2 sm:grid-cols-3 md:grid-cols-4">
        {fieldsFor(scope).map((f) => {
          const bad = !(f in d) && badSaved(f, saved[f])
          const value = f in d ? d[f] : isSet(saved[f]) && !bad ? String(saved[f]) : ''
          const wrong = f in d && invalid(f, d[f])
          return (
            <label key={f} className="grid min-w-0 content-start gap-1">
              <span className="lbl truncate">{LABELS[f] ? t(LABELS[f]) : f}</span>
              <input
                className="field num"
                style={wrong || bad ? { boxShadow: 'inset 0 0 0 1.5px var(--crimson)' } : undefined}
                type="number"
                inputMode="numeric"
                disabled={locked}
                placeholder={placeholder(scope, f)}
                value={value}
                onChange={(e) => setDrafts((x) => ({ ...x, [scope]: { ...x[scope], [f]: e.target.value } }))}
              />
              <span className={`text-[11px] ${wrong ? 'text-bad' : 'text-ink-3'}`}>
                {bad ? t('box.motion.saved_invalid', { value: String(saved[f]) }) : wrong ? t('box.motion.whole_number', { range: limitText(f) }) : limitText(f)}
              </span>
            </label>
          )
        })}
      </div>
    )
  }

  const summary = (key: string) => {
    const slot = slots[key]
    const o = slot?.overrides ?? {}
    const load = slot?.load ?? {}
    const unload = slot?.unload ?? {}
    const v = (part: unknown, setting: string) => <b className={isSet(o[setting]) ? 'text-accent-ink' : 'font-normal'} title={isSet(o[setting]) ? t('box.motion.slot_override') : undefined}>{isSet(part) ? String(part) : '?'}</b>
    return tr('box.motion.summary', {
      fast_length: v(load.fast_length, 'fast_length'),
      fast_speed_1: v(load.fast_speed_1, 'fast_speed_1'),
      fast_speed_2: v(load.fast_speed_2, 'fast_speed_2'),
      approach_speed: v(load.approach_speed, 'approach_speed'),
      load_accel: v(load.accel, 'load_accel'),
      unload_speed_1: v(unload.speed_1, 'unload_speed_1'),
      unload_speed_2: v(unload.speed_2, 'unload_speed_2'),
      unload_accel: v(unload.accel, 'unload_accel'),
    })
  }
  const slotErrors = (key: string) =>
    ([['box.motion.load_error', slots[key]?.load], ['box.motion.unload_error', slots[key]?.unload]] as const).filter(([, p]) => p?.state === 'error' && p.error).map(([k, p]) => t(k, { error: String(p?.error) }))
  // A scope's fields, the reason its last change was refused, and its Reset and Apply.
  const editor = (scope: string, hint: string, reset: string, resetScript: string, ask: string) => (
    <>
      <div className="text-[12.5px] text-ink-3">{hint}</div>
      {fields(scope)}
      {errors[scope] && (
        <div className="tone-bad rounded-lg px-3 py-2 text-ink" role="alert">
          {errors[scope]}
        </div>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <button className="btn btn-sm btn-ghost" disabled={locked} onClick={() => run(scope, resetScript, ask)}>
          {reset}
        </button>
        <button className="btn btn-sm btn-primary" disabled={locked || !hasChanges(scope)} onClick={() => apply(scope)}>
          {t('common.apply')}
        </button>
      </div>
    </>
  )

  return (
    <div className="grid gap-2.5">
      <SectionLabel>{t('box.motion.title')}</SectionLabel>
      {!active ? (
        <div className="text-ink-3">{motion.status || t('box.motion.unavailable')}</div>
      ) : (
        <>
          {notes.map((n) => (
            <div key={n} className="text-[12.5px] text-warn">
              {n}
            </div>
          ))}
          {locked && !pending && <div className="text-[12.5px] text-ink-3">{t('box.motion.locked')}</div>}
          {editor('global', t('box.motion.global_hint'), t('box.motion.reset'), 'VO_BOX_MOTION RESET=1', t('box.motion.reset_all_confirm'))}
          {slotKeys.length > 0 && (
            <div className="grid">
              {slotKeys.map((key) => (
                <div key={key} className="border-t border-line py-2.5">
                  <div className="flex items-center gap-2">
                    <b>{qidiBoxSlotLabel(qidiBoxSlotNumber(key) ?? 0)}</b>
                    <span className="min-w-0 flex-1 text-[12.5px] text-ink-2">{summary(key)}</span>
                    <button className="btn btn-sm btn-ghost" onClick={() => toggle(key)}>
                      {open === key ? t('common.close') : t('common.edit')}
                    </button>
                  </div>
                  {slotErrors(key).map((e) => (
                    <div key={e} className="text-[12px] text-bad">
                      {e}
                    </div>
                  ))}
                  {open === key && (
                    <div className="mt-2.5 grid gap-2.5">{editor(key, t('box.motion.slot_hint'), t('box.motion.reset_slot'), `VO_BOX_MOTION SLOT=${qidiBoxSlotNumber(key)} RESET=1`, t('box.motion.reset_slot_confirm', { slot: qidiBoxSlotLabel(qidiBoxSlotNumber(key) ?? 0) }))}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}

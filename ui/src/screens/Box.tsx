import { useEffect, useMemo, useState } from 'react'
import { useStore } from '../lib/store'
import { ArrowDown, ArrowUp, Box as BoxIcon, Check, CircleAlert, Flame, LoaderCircle, Nfc, Pencil, Scissors, Settings2, SlidersHorizontal, Waypoints } from 'lucide-react'
import { Card, Dialog, HoldButton, Toggle } from '../components/ui'
import { FilamentPath } from '../components/FilamentPath'
import { BoxStrip } from '../components/BoxStrip'
import { Spool, slotTitle } from '../components/Spool'
import { attempt, toast } from '../components/toast'
import { QIDI_BOX_EXTERNAL_SLOT, boxCommands, boxHeaterMax, filamentLists, qidiBoxSlotLabel, slotActions, slotByIndex, useBox, type BoxState, type QidiBoxSlot } from '../state/box'
import { runGcode, usePrinter } from '../state/printer'
import { fluiddUrl } from '../lib/links'

export function BoxScreen() {
  const box = useBox()
  const nozzle = usePrinter((s) => (s.status.extruder?.temperature as number | undefined) ?? null)
  const [selected, setSelected] = useState<number | null>(null)
  const [mapOpen, setMapOpen] = useState(false)

  // Select the stranded slot when filament gets stuck; otherwise default to the loaded slot.
  useEffect(() => {
    if (box.strandedSlot != null) setSelected(box.strandedSlot)
  }, [box.strandedSlot])
  const current = selected ?? box.loadedSlot ?? box.slots.find((s) => s.present && s.index !== QIDI_BOX_EXTERNAL_SLOT)?.index ?? box.slots[0]?.index ?? null
  const slot = slotByIndex(box, current)

  const selectFromTool = (s: number) => setSelected(s)

  return (
    <>
      <div className="grid gap-3.5 xl:grid-cols-[1fr_360px]">
        <Card
          title="Filament path"
          icon={<Waypoints size={14} />}
          aside={
            <button className="btn btn-sm" onClick={() => (box.mappingLocked ? toast('Tool mapping can be changed while the printer is idle.', 'error') : setMapOpen(true))}>
              Tool mapping
            </button>
          }
        >
          <div className="hidden md:block">
            <FilamentPath box={box} selected={current} onSelect={selectFromTool} nozzleTemp={nozzle} />
          </div>
          <div className="md:hidden">
            <BoxStrip box={box} selected={current} onSelect={selectFromTool} size={40} />
          </div>
          {box.warnings.length > 0 && (
            <div className="mt-3 grid gap-1.5">
              {box.warnings.map((w) => (
                <div key={w} className="tone-warn flex items-center gap-2 rounded-lg px-3 py-2">
                  <CircleAlert size={15} /> <span className="text-ink">{w}</span>
                </div>
              ))}
            </div>
          )}
        </Card>
        <div className="grid content-start gap-3.5">
          {slot && <SlotPanel box={box} slot={slot} />}
          <Operation box={box} />
        </div>
      </div>
      <div className="grid gap-3.5 md:grid-cols-2 xl:grid-cols-3">
        {box.climates.map((c) => (
          <HeaterCard key={c.box} box={box} boxNumber={c.box} />
        ))}
        <SettingsCard box={box} />
        <MotionCard />
      </div>
      <ToolMapDialog box={box} open={mapOpen} onClose={() => setMapOpen(false)} />
    </>
  )
}

/** Load, Unload, Eject, Cut, and In/Out for a slot, enabled exactly as the Fluidd fork's card enables them. */
export function SlotActionButtons({ box, slot, className = '', withTagButtons = false }: { box: BoxState; slot: QidiBoxSlot; className?: string; withTagButtons?: boolean }) {
  const a = slotActions(box, slot)
  const [editOpen, setEditOpen] = useState(false)
  const lists = useStore(filamentLists, (l) => l)
  const run = (g: string) => attempt(() => runGcode(g))
  const canEdit = a.edit && (box.hasController ? !!lists.database?.length : !!lists.legacy)
  const isBoxSlot = slot.index !== QIDI_BOX_EXTERNAL_SLOT
  return (
    <div className={className}>
      {isBoxSlot && (
        <>
          <div className="flex flex-wrap gap-1.5">
            <button className="btn btn-sm btn-primary" disabled={!a.load} onClick={() => run(boxCommands.load(box, slot.index))}>
              Load
            </button>
            <button className="btn btn-sm" disabled={!a.unload} onClick={() => run(boxCommands.unload(box, slot.index))}>
              Unload
            </button>
            <button className="btn btn-sm" disabled={!a.eject} onClick={() => run(boxCommands.eject(box, slot.index))}>
              Eject
            </button>
            {box.canCut && (
              <button className="btn btn-sm" disabled={!a.cut} onClick={() => run(boxCommands.cut())} title="Cut the filament at the toolhead without heating">
                <Scissors size={13} /> Cut
              </button>
            )}
            {box.canJog && (
              <>
                <button className="btn btn-sm btn-ghost" disabled={!a.jog} onClick={() => run(boxCommands.jog(slot.index, 10))} title="Move this slot's filament 10 mm toward the toolhead">
                  <ArrowUp size={13} /> In
                </button>
                <button className="btn btn-sm btn-ghost" disabled={!a.jog} onClick={() => run(boxCommands.jog(slot.index, -10))} title="Move this slot's filament 10 mm back toward the spool">
                  <ArrowDown size={13} /> Out
                </button>
              </>
            )}
            {withTagButtons && (
              <>
                <button className="btn btn-sm btn-ghost px-2!" disabled={!a.rfid} title="Read RFID tag" aria-label="Read RFID tag" onClick={() => run(boxCommands.rfid(box, slot.index))}>
                  <Nfc size={14} />
                </button>
                <button className="btn btn-sm btn-ghost px-2!" disabled={!canEdit} title="Edit filament" aria-label="Edit filament" onClick={() => setEditOpen(true)}>
                  <Pencil size={13} />
                </button>
              </>
            )}
          </div>
          {a.hint && <div className="tone-warn mt-2.5 rounded-lg px-3 py-2 text-ink">{a.hint}</div>}
          {box.printState === 'printing' && <div className="mt-2 text-[12.5px] text-ink-3">Loading and unloading are unavailable while printing.</div>}
        </>
      )}
      {!isBoxSlot && <div className="text-ink-3">The external spool feeds the toolhead directly. Load it by hand, or use the touchscreen.</div>}
      {editOpen && <FilamentDialog box={box} slot={slot} onClose={() => setEditOpen(false)} />}
    </div>
  )
}

function SlotPanel({ box, slot }: { box: BoxState; slot: QidiBoxSlot }) {
  const a = slotActions(box, slot)
  const [editOpen, setEditOpen] = useState(false)
  const lists = useStore(filamentLists, (l) => l)
  const tools = Object.entries(box.toolMap).filter(([, s]) => s === slot.index).map(([t]) => `T${t}`)
  const group = box.reloadGroups[slot.index]
  const run = (g: string) => attempt(() => runGcode(g))
  const canEdit = a.edit && (box.hasController ? !!lists.database?.length : !!lists.legacy)
  const isBoxSlot = slot.index !== QIDI_BOX_EXTERNAL_SLOT
  return (
    <Card>
      <div className="flex items-center gap-3">
        <Spool slot={slot} size={60} />
        <div className="min-w-0 flex-1">
          <div className="text-[15px] font-semibold">
            {slot.label} · {slotTitle(slot)}
          </div>
          <div className="text-ink-3">
            {[slot.type, slot.minTemp != null && slot.maxTemp ? `${slot.minTemp}–${slot.maxTemp} °C` : null, slot.boxMaxTemp != null && slot.maxTemp ? (slot.boxMaxTemp === 0 ? 'no Box heating' : `Box max ${slot.boxMaxTemp} °C`) : null]
              .filter(Boolean)
              .join(' · ')}
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {tools.map((t) => (
              <span key={t} className="chip">
                {t}
              </span>
            ))}
            {box.loadedSlot === slot.index && <span className="chip text-accent-ink!">Loaded</span>}
            {group && <span className="chip text-warn!">Reload group {group.letter}</span>}
          </div>
        </div>
        {isBoxSlot && (
          <div className="flex flex-col gap-1">
            <button className="iconbtn" disabled={!a.rfid} title="Read RFID tag" onClick={() => run(boxCommands.rfid(box, slot.index))}>
              <Nfc size={16} />
            </button>
            <button className="iconbtn" disabled={!canEdit} title="Edit filament" onClick={() => setEditOpen(true)}>
              <Pencil size={15} />
            </button>
          </div>
        )}
      </div>
      <SlotActionButtons box={box} slot={slot} className="mt-3.5" />
      {editOpen && <FilamentDialog box={box} slot={slot} onClose={() => setEditOpen(false)} />}
    </Card>
  )
}

export function Operation({ box, bare = false }: { box: BoxState; bare?: boolean }) {
  const [pending, setPending] = useState(false)
  if (!box.task) return null
  const send = async (g: string) => {
    setPending(true)
    await attempt(() => runGcode(g))
    setPending(false)
  }
  if (box.task.paused) {
    return (
      <Frame bare={bare} title="Box step failed" icon={<CircleAlert size={14} className="text-bad" />}>
        <div className="task fail">
          <span className="ic">
            <CircleAlert size={15} />
          </span>
          <span>
            <b>{box.taskText}</b>
            {box.lastError && (
              <>
                <br />
                <span className="text-ink-2">
                  {box.lastError.message}
                  {box.lastError.code && ` (${box.lastError.code})`}
                </span>
              </>
            )}
          </span>
          <span />
        </div>
        <div className="my-2.5 text-ink-3">QIDI paused the load or unload. Fix the cause, then retry the step or stop.</div>
        <div className="flex gap-2">
          <button className="btn btn-sm btn-primary" disabled={pending} onClick={() => send(boxCommands.retry())}>
            Retry
          </button>
          <button className="btn btn-sm" disabled={pending} onClick={() => send(boxCommands.stop())}>
            Stop
          </button>
        </div>
      </Frame>
    )
  }
  return (
    <Frame bare={bare} title="Box is working" icon={<BoxIcon size={14} />}>
      <div className="task run">
        <span className="ic">
          <LoaderCircle size={15} className="spin" />
        </span>
        <span className="shimmer">{box.taskText}</span>
        <span />
      </div>
    </Frame>
  )
}

function Frame({ bare, title, icon, children }: { bare: boolean; title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return bare ? <div>{children}</div> : <Card title={title} icon={icon}>{children}</Card>
}

function HeaterCard({ box, boxNumber }: { box: BoxState; boxNumber: number }) {
  const c = box.climates.find((x) => x.box === boxNumber)!
  return (
    <Card title={`Box ${boxNumber} heater`} icon={<Flame size={14} />} aside={c.humidity != null ? `${c.humidity}% RH` : undefined}>
      <HeaterControls box={box} boxNumber={boxNumber} />
    </Card>
  )
}

export function HeaterDialog({ box, boxNumber, onClose }: { box: BoxState; boxNumber: number; onClose: () => void }) {
  const c = box.climates.find((x) => x.box === boxNumber)
  return (
    <Dialog open onClose={onClose} title={`Box ${boxNumber} heater${c?.humidity != null ? ` · ${c.humidity}% RH` : ''}`}>
      <HeaterControls box={box} boxNumber={boxNumber} />
    </Dialog>
  )
}

function HeaterControls({ box, boxNumber }: { box: BoxState; boxNumber: number }) {
  const c = box.climates.find((x) => x.box === boxNumber)!
  const cfg = usePrinter((s) => (c.heater ? s.heaterSettings[c.heater] : undefined))
  const max = boxHeaterMax(box, boxNumber, cfg?.target_max_temp ?? cfg?.max_temp)
  const [value, setValue] = useState('')
  const locked = max === 0 && !c.target
  const set = (t: number) => c.heater && attempt(() => runGcode(boxCommands.heater(c.heater!, t)))
  const pct = c.temperature != null ? Math.min(1, c.temperature / Math.max(max, 1)) : 0
  const circ = 2 * Math.PI * 38
  return (
      <div className="flex items-center gap-4">
        <svg width={92} height={92} viewBox="0 0 92 92" className="flex-none">
          <circle cx={46} cy={46} r={38} fill="none" stroke="var(--inset)" strokeWidth={8} />
          <circle cx={46} cy={46} r={38} fill="none" stroke={c.target ? 'var(--orange)' : 'var(--ink-3)'} strokeWidth={8} strokeLinecap="round" strokeDasharray={`${circ * pct} ${circ}`} transform="rotate(-90 46 46)" />
          <text x={46} y={50} textAnchor="middle" fontSize={20} fontWeight={650} fill="var(--ink)">
            {c.temperature != null ? `${Math.round(c.temperature)}°` : '—'}
          </text>
          <text x={46} y={66} textAnchor="middle" fontSize={10} fill="var(--ink-3)">
            {c.target ? `→ ${c.target} °C` : 'off'}
          </text>
        </svg>
        <div className="grid min-w-0 gap-2">
          <div className="text-ink-3">{locked ? 'A loaded filament must not be heated in the Box.' : `Up to ${max} °C for the filaments loaded.`}</div>
          <div className="flex flex-wrap gap-1.5">
            <button className="btn btn-sm" disabled={!c.heater || !c.target} onClick={() => set(0)}>
              Off
            </button>
            {[45, 55, 65]
              .filter((t) => t <= max)
              .map((t) => (
                <button key={t} className={`btn btn-sm ${c.target === t ? 'btn-primary' : ''}`} disabled={!c.heater || locked} onClick={() => set(t)}>
                  {t}
                </button>
              ))}
          </div>
          <form
            className="flex gap-1.5"
            onSubmit={(e) => {
              e.preventDefault()
              const t = Number(value)
              if (!Number.isFinite(t) || t < 0 || t > max) return toast(`Enter 0–${max} °C.`, 'error')
              set(Math.round(t))
              setValue('')
            }}
          >
            <input className="field w-20" inputMode="numeric" placeholder="°C" value={value} onChange={(e) => setValue(e.target.value)} disabled={!c.heater || locked} />
            <button className="btn btn-sm" disabled={!c.heater || locked || !value}>
              Set
            </button>
          </form>
        </div>
      </div>
  )
}

function SettingsCard({ box }: { box: BoxState }) {
  const vars = usePrinter((s) => s.status.save_variables?.variables ?? {})
  const rows: { key: 'auto_init_detect' | 'auto_read_rfid' | 'auto_reload_detect'; label: string; detail: string }[] = [
    { key: 'auto_init_detect', label: 'Detect on startup', detail: 'Read every slot when the printer starts.' },
    { key: 'auto_read_rfid', label: 'Detect on insertion', detail: 'Read a spool’s RFID tag when it’s inserted.' },
    { key: 'auto_reload_detect', label: 'Automatic reload', detail: 'Switch to a matching slot when one runs out.' },
  ]
  return (
    <Card title="Box settings" icon={<Settings2 size={14} />}>
      <div className="grid gap-3">
        {rows.map((r) => (
          <label key={r.key} className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <div className="font-medium">{r.label}</div>
              <div className="text-[12.5px] text-ink-3">{r.detail}</div>
            </div>
            <Toggle checked={Number(vars[r.key] ?? 0) === 1} label={r.label} onChange={(v) => attempt(() => runGcode(boxCommands.setting(r.key, v)))} />
          </label>
        ))}
        {box.autoReload && Object.keys(box.reloadGroups).length === 0 && <div className="text-[12.5px] text-ink-3">No slots share the same filament, vendor, and color, so none can stand in for another.</div>}
      </div>
    </Card>
  )
}

// ---- load and unload motion (Qidi-Max-4-Optimized's TLTG_BOX_MOTION) ----
const MOTION_FIELDS: { key: string; label: string }[] = [
  { key: 'hub_speed', label: 'Hub push speed' },
  { key: 'fast_length', label: 'Fast push' },
  { key: 'fast_speed', label: 'Fast speed' },
  { key: 'fast_speed_1', label: 'Fast speed 1' },
  { key: 'fast_speed_2', label: 'Fast speed 2' },
  { key: 'approach_speed', label: 'Approach speed' },
  { key: 'load_accel', label: 'Load acceleration' },
  { key: 'unload_speed', label: 'Unload speed' },
  { key: 'unload_speed_1', label: 'Unload speed 1' },
  { key: 'unload_speed_2', label: 'Unload speed 2' },
  { key: 'unload_accel', label: 'Unload acceleration' },
  { key: 'unload_extruder_length', label: 'Extruder pull-back' },
  { key: 'park_speed', label: 'Park speed' },
]

function MotionCard() {
  const m = usePrinter((s) => s.status.tltg_box_feed_motion as Record<string, any> | undefined) // eslint-disable-line @typescript-eslint/no-explicit-any
  const locked = usePrinter((s) => s.klippy !== 'ready' || ['printing', 'paused'].includes(s.status.print_stats?.state))
  const [draft, setDraft] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  if (!m) return null
  const limits = (m.limits ?? {}) as Record<string, { min: number; max: number; unit?: string }>
  const vendor = (m.vendor ?? {}) as Record<string, number>
  const settings = (m.settings ?? {}) as Record<string, unknown>
  // The printer's own order and set of settings when it publishes them; the Plus 5 has no per-slot settings.
  const order = Array.isArray(m.fields) ? (m.fields as string[]) : MOTION_FIELDS.map((f) => f.key)
  const fields = order.map((key) => MOTION_FIELDS.find((f) => f.key === key)).filter((f): f is { key: string; label: string } => !!f && !!limits[f.key])
  const perSlot = !Array.isArray(m.global_only) || fields.some((f) => !(m.global_only as string[]).includes(f.key))
  const changed = Object.entries(draft).filter(([k, v]) => v.trim() !== '' && String(settings[k] ?? '') !== v.trim())

  const save = async () => {
    setError(null)
    const parts: string[] = []
    for (const [k, v] of changed) {
      const n = Number(v)
      const lim = limits[k]
      if (!Number.isInteger(n) || n < lim.min || n > lim.max) return setError(`${MOTION_FIELDS.find((f) => f.key === k)?.label} must be a whole number from ${lim.min} to ${lim.max}.`)
      parts.push(`${k.toUpperCase()}=${n}`)
    }
    setBusy(true)
    try {
      await runGcode(`TLTG_BOX_MOTION ${parts.join(' ')}`)
      setDraft({})
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
    setBusy(false)
  }

  return (
    <Card title="Load and unload motion" icon={<SlidersHorizontal size={14} />} aside={m.active === false ? 'QIDI’s motion' : undefined} className="md:col-span-2 xl:col-span-1">
      {m.active === false && m.status && <div className="tone-warn mb-3 rounded-lg px-3 py-2 text-ink">{String(m.status)}</div>}
      <div className="grid gap-1">
        {fields.map((f) => {
          const v = settings[f.key]
          const unit = limits[f.key].unit ?? ''
          return (
            <div key={f.key} className="flex items-center gap-2 border-b border-line py-1">
              <span className="min-w-0 flex-1">{f.label}</span>
              <input
                className="field num w-[72px] py-1 text-right"
                inputMode="numeric"
                disabled={locked || busy}
                placeholder={v == null ? String(vendor[f.key] ?? '') : String(v)}
                value={draft[f.key] ?? ''}
                onChange={(e) => setDraft((d) => ({ ...d, [f.key]: e.target.value }))}
                aria-label={f.label}
              />
              <span className="w-10 text-ink-3">{unit}</span>
              <span className="num w-[68px] text-right text-ink-3" title="QIDI's value">
                QIDI {vendor[f.key] ?? '—'}
              </span>
            </div>
          )
        })}
      </div>
      {error && <div className="tone-bad mt-2.5 rounded-lg px-3 py-2 text-ink">{error}</div>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button className="btn btn-sm btn-primary" disabled={locked || busy || changed.length === 0} onClick={save}>
          Save
        </button>
        <HoldButton className="btn-sm" disabled={locked || busy} onHold={() => attempt(() => runGcode('TLTG_BOX_MOTION RESET=1'))}>
          Hold to reset to QIDI
        </HoldButton>
        {perSlot && (
          <a className="btn btn-sm btn-ghost ml-auto" href={fluiddUrl()}>
            Per-slot settings in Fluidd
          </a>
        )}
      </div>
      {locked && <div className="mt-2 text-[12.5px] text-ink-3">Motion settings can be changed while the printer isn’t printing.</div>}
    </Card>
  )
}

export function ToolMapDialog({ box, open, onClose }: { box: BoxState; open: boolean; onClose: () => void }) {
  const boxSlots = box.boxCount * 4
  const highest = Math.max(-1, ...Object.keys(box.toolMap).map(Number))
  const rows = Math.min(16, Math.max(boxSlots, highest + 1, 1))
  const [draft, setDraft] = useState<Record<number, number>>({})
  useEffect(() => {
    if (open) setDraft({ ...box.toolMap })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  const choices = useMemo(() => box.slots.map((s) => s.index), [box.slots])
  const changed = Object.entries(draft).filter(([t, s]) => box.toolMap[Number(t)] !== s)
  const save = async () => {
    if (changed.length) await attempt(() => runGcode(changed.map(([t, s]) => boxCommands.mapTool(Number(t), s)).join('\n')))
    onClose()
  }
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Tool mapping"
      footer={
        <>
          <button
            className="btn btn-ghost mr-auto"
            onClick={() => setDraft((d) => ({ ...d, ...Object.fromEntries(Array.from({ length: Math.min(16, boxSlots) }, (_, i) => [i, i])) }))}
          >
            Default
          </button>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={box.mappingLocked || changed.length === 0} onClick={save}>
            Save
          </button>
        </>
      }
    >
      <div className="mb-3 text-ink-2">Each tool in a sliced file loads the slot chosen here. A normal print end restores the default.</div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {Array.from({ length: rows }, (_, t) => (
          <label key={t} className="grid gap-1">
            <span className="lbl">T{t}</span>
            <select className="field" value={draft[t] ?? ''} onChange={(e) => setDraft((d) => ({ ...d, [t]: Number(e.target.value) }))}>
              {draft[t] == null && <option value="">—</option>}
              {choices.map((c) => (
                <option key={c} value={c}>
                  {qidiBoxSlotLabel(c)}
                  {slotByIndex(box, c)?.present && c !== QIDI_BOX_EXTERNAL_SLOT ? ` · ${slotByIndex(box, c)?.type ?? ''}` : ''}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
    </Dialog>
  )
}

function FilamentDialog({ box, slot, onClose }: { box: BoxState; slot: QidiBoxSlot; onClose: () => void }) {
  const lists = useStore(filamentLists, (l) => l)
  if (!box.hasController || !lists.database) {
    return (
      <Dialog open onClose={onClose} title={`Edit ${slot.label}`} footer={<a className="btn btn-primary" href={fluiddUrl()}>Open Fluidd</a>}>
        Editing filaments on this firmware is available in Fluidd’s QIDI Box card.
      </Dialog>
    )
  }
  return <FilamentEditor box={box} slot={slot} onClose={onClose} database={lists.database} />
}

function FilamentEditor({ slot, onClose, database }: { box: BoxState; slot: QidiBoxSlot; onClose: () => void; database: { id: string; name: string; type: string; vendor: string }[] }) {
  const vendors = useMemo(() => [...new Set(database.map((f) => f.vendor))].sort((a, b) => a.localeCompare(b)), [database])
  const initial = database.find((f) => f.id === slot.filamentId)
  const [vendor, setVendor] = useState(initial?.vendor ?? vendors[0] ?? '')
  const [id, setId] = useState(initial?.id ?? '')
  const [color, setColor] = useState(slot.color ?? '#FFFFFF')
  const options = database.filter((f) => f.vendor === vendor)
  const pickVendor = (v: string) => {
    setVendor(v)
    const cur = database.find((f) => f.id === id)
    const same = database.find((f) => f.vendor === v && f.type === cur?.type)
    setId((same ?? database.find((f) => f.vendor === v))?.id ?? '')
  }
  const save = async () => {
    const lines: string[] = []
    if (id && id !== slot.filamentId) lines.push(`SAVE_VARIABLE VARIABLE=filament_slot${slot.index} VALUE='"${id}"'`)
    const hex = color.replace('#', '').toUpperCase()
    if (`#${hex}` !== slot.color) lines.push(`SAVE_VARIABLE VARIABLE=color_slot${slot.index} VALUE="['${hex}']"`)
    if (lines.length) await attempt(() => runGcode(lines.join('\n')))
    onClose()
  }
  const PALETTE = ['#FFFFFF', '#000000', '#8A8F98', '#E5484D', '#FF7A1A', '#F5D90A', '#30A46C', '#2F6BFF', '#8E4EC6', '#D6409F', '#A0522D', '#F2EFE8']
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Edit ${slot.label}`}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={save}>
            <Check size={14} /> Save
          </button>
        </>
      }
    >
      <div className="grid gap-3">
        <label className="grid gap-1">
          <span className="lbl">Vendor</span>
          <select className="field" value={vendor} onChange={(e) => pickVendor(e.target.value)}>
            {vendors.map((v) => (
              <option key={v}>{v}</option>
            ))}
          </select>
        </label>
        <label className="grid gap-1">
          <span className="lbl">Filament</span>
          <select className="field" value={id} onChange={(e) => setId(e.target.value)}>
            {options.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name.startsWith(`${f.vendor} `) ? f.name.slice(f.vendor.length + 1) : f.name}
              </option>
            ))}
          </select>
        </label>
        <div className="grid gap-1">
          <span className="lbl">Color</span>
          <div className="flex flex-wrap items-center gap-1.5">
            {PALETTE.map((c) => (
              <button
                key={c}
                type="button"
                aria-label={c}
                onClick={() => setColor(c)}
                className="h-7 w-7 rounded-md border-0"
                style={{ background: c, boxShadow: color.toUpperCase() === c ? '0 0 0 2px var(--surface), 0 0 0 4px var(--accent)' : 'inset 0 0 0 1px oklch(0 0 0 / .25)' }}
              />
            ))}
            <input type="color" value={color} onChange={(e) => setColor(e.target.value.toUpperCase())} className="h-7 w-10 cursor-pointer rounded-md border-0 bg-transparent p-0" aria-label="Custom color" />
          </div>
        </div>
      </div>
    </Dialog>
  )
}

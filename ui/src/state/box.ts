// QIDI Box state for the UI, derived with the logic ported from the Fluidd fork (src/qidi/).
import { createStore, useStore } from '../lib/store'
import { client, onKlippyReady, printer, usePrinter, type PrinterState } from './printer'
import {
  QIDI_BOX_EXTERNAL_SLOT,
  parseQidiFilamentDatabase,
  parseQidiFilamentList,
  qidiBoxReloadGroups,
  qidiBoxSlotLabel,
  qidiBoxSlotNumber,
  qidiBoxSlots,
  qidiBoxToolButtons,
  type QidiBoxReloadGroups,
  type QidiBoxToolButton,
  type QidiBoxSlot,
  type QidiFilament,
  type QidiFilamentList,
} from '../qidi/qidi-box'
import {
  qidiBoxHubSensor,
  qidiBoxHubSlot,
  qidiBoxLastError,
  qidiBoxLoadedSlot,
  qidiBoxStrandedRecovery,
  qidiBoxTask,
  qidiBoxTaskText,
  qidiBoxWarnings,
  QIDI_BOX_STEP_UNKNOWN,
  type QidiBoxError,
  type QidiBoxStepText,
  type QidiBoxTask,
} from '../qidi/qidi-box-task'

export { QIDI_BOX_EXTERNAL_SLOT, qidiBoxSlotLabel }
export type { QidiBoxSlot, QidiFilament, QidiBoxToolButton }

// ---- QIDI's filament lists, read through Moonraker's file API ----
export const LEGACY_LIST_PATH = 'officiall_filas_list.cfg'
export const DATABASE_PATH = 'qidi_filament_config/filament-0.4.json'

type Lists = { legacy: QidiFilamentList | null; database: QidiFilament[] | null; databaseError: boolean }
export const filamentLists = createStore<Lists>({ legacy: null, database: null, databaseError: false })

async function fetchText(path: string): Promise<string | null> {
  try {
    const r = await fetch(client.fileUrl('config', path), { cache: 'no-store' })
    return r.ok ? await r.text() : null
  } catch {
    return null
  }
}

export async function loadFilamentLists() {
  const hasController = printer.get().status.multi_color_controller != null
  const legacyText = await fetchText(LEGACY_LIST_PATH)
  const legacy = legacyText ? parseQidiFilamentList(legacyText) : null
  let database: QidiFilament[] | null = null
  let databaseError = false
  if (hasController) {
    const text = await fetchText(DATABASE_PATH)
    try {
      database = text ? parseQidiFilamentDatabase(JSON.parse(text)) : null
    } catch {
      database = null
    }
    databaseError = !database?.length
  }
  filamentLists.set({ legacy, database, databaseError })
}
// Klipper and the touchscreen read the filament files when they start, so they're read again after every Klipper start.
onKlippyReady(() => {
  const st = printer.get().status
  if (Number(st.save_variables?.variables?.box_count ?? 0) > 0 || st.multi_color_controller != null) loadFilamentLists()
})

// ---- derived state ----
export type BoxClimate = { box: number; humidity: number | null; temperature: number | null; target: number | null; heater: string | null }

/** Valve Oil's turn spools while heating (tltg_box_spool_roll): on, and the slot it's turning now. */
export type SpoolRoll = { enabled: boolean; rolling: string | null }

export type BoxState = {
  boxCount: number
  hasController: boolean
  enabled: boolean
  autoReload: boolean
  slots: QidiBoxSlot[]
  reloadGroups: QidiBoxReloadGroups
  toolMap: Record<number, number>
  loadedSlot: number | null
  lastLoadSlot: number | null
  hubSensor: number | null
  /** Slot whose filament is at the hub, as drawn on the path. */
  hubSlot: number | null
  /** Slot whose filament reaches the toolhead. */
  toolheadSlot: number | null
  toolheadFilament: boolean | null
  task: QidiBoxTask | null
  taskText: string | null
  lastError: QidiBoxError | null
  warnings: string[]
  busy: boolean
  mappingLocked: boolean
  strandedRecovery: boolean
  strandedSlot: number | null
  climates: BoxClimate[]
  printState: string
  canCold: boolean
  canJog: boolean
  canCut: boolean
  /** Valve Oil can write spool tags (VO_BOX_RFID_WRITE), with the firmware filament database whose IDs tags carry. */
  canWriteTag: boolean
  /** Slots whose filament the Box set from the spool's RFID tag. */
  fromTag: Record<number, boolean>
  /** Tool names (T0…) mapped to each slot. */
  toolsBySlot: Record<number, string[]>
  /** QIDI defines T0–T15 whatever Boxes are connected, so tool buttons follow the Box instead. */
  managesTools: boolean
  /** The tools T_MANAGER can load, with the active one; empty while the Box is off. */
  toolButtons: QidiBoxToolButton[]
  /** The slot's filament can be edited: the firmware database on 01.01.07.01, QIDI's list before it. */
  canEditFilament: boolean
  /** 01.01.07.01's filament database was read but lists no filaments. */
  filamentDatabaseInvalid: boolean
  flowPaused: boolean
  /** The running step, in words; null while paused or idle. */
  taskStep: string | null
  /** The failed step's console error, then QIDI's Box warnings. */
  problems: string[]
  spoolRoll: SpoolRoll | null
}

const num = (v: unknown) => (typeof v === 'number' && isFinite(v) ? v : null)

export function deriveBox(s: PrinterState, lists: Lists): BoxState {
  const st = s.status
  const variables = st.save_variables?.variables ?? {}
  const boxCount = Math.max(0, Math.min(4, Number(variables.box_count ?? 0) || 0))
  const hasController = st.multi_color_controller != null
  const slots = qidiBoxSlots(st, lists.legacy)
  const printState = (st.print_stats?.state ?? 'standby') as string
  const task = qidiBoxTask(st)
  const ready = s.klippy === 'ready'
  const busy = !ready || printState === 'printing' || !!st.box_extras?.box_button_state || task != null
  const loadedSlot = qidiBoxLoadedSlot(st, slots)
  const hubSensor = qidiBoxHubSensor(st)
  const toolheadFilament = st['filament_switch_sensor filament_switch_sensor']?.filament_detected ?? null
  const hubPick = qidiBoxHubSlot(st, slots)
  const hubSlot = hubSensor !== 0 ? null : loadedSlot != null && loadedSlot !== QIDI_BOX_EXTERNAL_SLOT ? loadedSlot : hubPick
  const strandedRecovery = ready && hasController && qidiBoxStrandedRecovery(st)

  const toolMap: Record<number, number> = {}
  for (let t = 0; t < 16; t++) {
    const slot = qidiBoxSlotNumber(variables[`value_t${t}`])
    if (slot != null) toolMap[t] = slot
  }

  const climates: BoxClimate[] = []
  for (let b = 1; b <= boxCount; b++) {
    const aht = st[`aht20_f heater_box${b}`]
    const heaterName = `heater_generic heater_box${b}`
    const heater = st[heaterName]
    climates.push({
      box: b,
      humidity: num(aht?.humidity) != null ? Math.round(aht.humidity) : null,
      temperature: num(aht?.temperature) ?? num(heater?.temperature),
      target: num(heater?.target),
      heater: heater ? heaterName : null,
    })
  }

  const text: QidiBoxStepText | null = qidiBoxTaskText(st)
  const commands = s.commands
  const flowPaused = task?.paused === true
  const lastError = flowPaused ? qidiBoxLastError(s.console.filter((l) => l.kind !== 'command').map((l) => l.text)) : null
  const warnings = qidiBoxWarnings(st.multi_color_controller?.box_warning_info)
  const toolsBySlot: Record<number, string[]> = {}
  for (const [tool, slot] of Object.entries(toolMap)) (toolsBySlot[slot] ??= []).push(`T${tool}`)
  const enabled = Number(variables.enable_box ?? 0) === 1
  const currentTool = Number(variables.extrude_state)
  const roll = st.tltg_box_spool_roll
  const fromTag: Record<number, boolean> = {}
  for (const slot of slots) {
    const infos = slot.index === QIDI_BOX_EXTERNAL_SLOT ? [st.multi_color_controller?.extra?.filament_info] : st.box_extras?.boxes?.[Math.floor(slot.index / 4)]?.filament_info
    const info = Array.isArray(infos) ? (infos.find((i) => i?.index === slot.index % 4) ?? infos[slot.index % 4]) : undefined
    if (slot.present && Number(info?.info_from_rfid) === 1) fromTag[slot.index] = true
  }
  return {
    boxCount,
    hasController,
    enabled,
    autoReload: Number(variables.auto_reload_detect ?? 0) === 1,
    slots,
    reloadGroups: Number(variables.auto_reload_detect ?? 0) === 1 ? qidiBoxReloadGroups(slots) : {},
    toolMap,
    loadedSlot,
    lastLoadSlot: qidiBoxSlotNumber(variables.last_load_slot),
    hubSensor,
    hubSlot,
    toolheadSlot: toolheadFilament ? loadedSlot : null,
    toolheadFilament,
    task,
    taskText: text ? stepText(text, task?.paused ?? false) : null,
    lastError,
    warnings,
    busy,
    mappingLocked: busy || printState === 'paused',
    strandedRecovery,
    strandedSlot: strandedRecovery ? hubPick : null,
    climates,
    printState,
    canCold: commands.includes('TLTG_CUT_FILAMENT'),
    canJog: commands.includes('TLTG_BOX_SLOT_JOG'),
    canCut: commands.includes('TLTG_CUT_FILAMENT'),
    canWriteTag: commands.includes('VO_BOX_RFID_WRITE') && hasController && !!lists.database?.length,
    fromTag,
    toolsBySlot,
    managesTools: variables.box_count != null || hasController,
    // T_MANAGER does nothing while the Box is off.
    toolButtons: enabled ? qidiBoxToolButtons(toolMap, slots, loadedSlot, Number.isInteger(currentTool) ? currentTool : null) : [],
    canEditFilament: hasController ? !!lists.database?.length : lists.legacy != null,
    filamentDatabaseInvalid: hasController && lists.databaseError,
    flowPaused,
    taskStep: text && !flowPaused ? stepText(text, false) : null,
    problems: [...new Set([...(lastError ? [lastError.code ? `${lastError.message} (${lastError.code})` : lastError.message] : []), ...warnings])],
    spoolRoll: roll ? { enabled: roll.enabled === true, rolling: typeof roll.rolling === 'string' ? roll.rolling : null } : null,
  }
}

const STEP_TEXT: Record<string, string> = {
  heat: 'Heating nozzle',
  heat_slot: 'Heating nozzle for {slot}',
  heat_external: 'Heating nozzle for the external spool',
  heat_temp: 'Heating nozzle to {temp} °C',
  heat_temp_slot: 'Heating nozzle to {temp} °C for {slot}',
  heat_temp_external: 'Heating nozzle to {temp} °C for the external spool',
  cut: 'Cutting filament',
  wipe: 'Wiping nozzle',
  bite: "Gripping the external spool's filament",
  load: 'Loading filament',
  load_slot: 'Loading {slot}',
  load_external: 'Loading the external spool',
  unload: 'Unloading filament',
  unload_slot: 'Unloading {slot}',
  unload_external: 'Unloading the external spool',
  eject: 'Ejecting filament',
  eject_slot: 'Ejecting {slot}',
  working: 'Working…',
}

function stepText(t: QidiBoxStepText, failed: boolean): string {
  const text = (STEP_TEXT[t.key] ?? STEP_TEXT.working).replace(/\{(\w+)\}/g, (_, k) => String(t.params[k] ?? ''))
  if (!failed) return text
  return t.key === QIDI_BOX_STEP_UNKNOWN ? 'Box step failed' : `${text} failed`
}

export function useBox(): BoxState {
  const s = usePrinter((x) => x)
  const lists = useStore(filamentLists, (l) => l)
  return deriveBox(s, lists)
}

export function useBoxPresent() {
  return usePrinter((s) => Number(s.status.save_variables?.variables?.box_count ?? 0) > 0)
}

export function useBoxBusy() {
  return usePrinter((s) => {
    if (s.klippy !== 'ready') return false
    const st = s.status
    return !!st.box_extras?.box_button_state || qidiBoxTask(st) != null
  })
}

// ---- actions: the commands the Fluidd fork sends ----

export type SlotActions = { load: boolean; unload: boolean; eject: boolean; rfid: boolean; edit: boolean; writeTag: boolean; jog: boolean; cut: boolean; hint: string | null }

/** Which actions the selected slot allows right now, matching the Fluidd fork's QIDI Box card. */
export function slotActions(box: BoxState, slot: QidiBoxSlot): SlotActions {
  const isBox = slot.index !== QIDI_BOX_EXTERNAL_SLOT
  const base = !box.busy && isBox && slot.present
  const atHubLoaded = box.hubSensor === 0 && box.lastLoadSlot === slot.index
  let unload = base && atHubLoaded
  let eject = base
  // Filament stranded between the hub and the toolhead can be unloaded or ejected even while a failed flow is paused,
  // from the slot at the hub when that's known, as Valve Oil refuses any other.
  if (box.strandedRecovery && isBox && slot.present) {
    const ok = box.strandedSlot == null || box.strandedSlot === slot.index
    unload = ok
    eject = ok
  }
  return {
    load: base && !atHubLoaded,
    unload,
    eject,
    rfid: base,
    edit: !box.busy && slot.present && box.canEditFilament,
    // A spool on an empty slot's rollers can be tagged before its filament is fed.
    writeTag: box.canWriteTag && isBox && !box.busy,
    jog: box.canJog && isBox && !box.mappingLocked,
    cut: box.canCut && !box.mappingLocked,
    hint: box.strandedRecovery ? strandedHint(box, slot) : null,
  }
}

function strandedHint(box: BoxState, slot: QidiBoxSlot) {
  const at = box.strandedSlot
  if (at == null) return 'Filament is stopped between the hub and the toolhead. Unload pulls it back into the Box; Eject removes it from the Box.'
  const label = qidiBoxSlotLabel(at)
  return at === slot.index
    ? `${label}'s filament is stopped between the hub and the toolhead. Unload pulls it back into the Box; Eject removes it from the Box.`
    : `${label}'s filament is stopped between the hub and the toolhead. Unload or eject ${label} first.`
}

/** What a slot holds, as the Box card's overline: the slot, its tools, and whether it's loaded. */
export function slotOverline(box: BoxState, slot: QidiBoxSlot) {
  const tools = box.toolsBySlot[slot.index] ?? []
  return [slot.index === QIDI_BOX_EXTERNAL_SLOT ? 'External spool' : `Slot ${slot.label}`, tools.length ? tools.join(', ') : 'no tool', box.loadedSlot === slot.index ? 'loaded' : null].filter(Boolean).join(' · ')
}

/** Automatic reload for a slot, as the fork's card says it. */
export function reloadText(box: BoxState, slot: QidiBoxSlot) {
  if (!box.autoReload) return 'Automatic reload is off'
  const group = box.reloadGroups[slot.index]
  if (!group) return 'No auto-reload backup'
  return `Auto-reload with ${group.members.filter((m) => m !== slot.index).map(qidiBoxSlotLabel).join(', ')}`
}

/** A slot tile's state: loaded to the toolhead, parked at the hub, ready at its inlet, or empty. */
export function slotStatus(box: BoxState, slot: QidiBoxSlot): 'loaded' | 'hub' | 'ready' | 'empty' | 'reading' {
  if (slot.reading) return 'reading'
  if (box.loadedSlot === slot.index) return 'loaded'
  if (box.hubSensor === 0 && box.hubSlot === slot.index) return 'hub'
  return slot.present ? 'ready' : 'empty'
}

// QIDI's Box settings are saved variables its Box modules read; vo_box_spool_roll is Valve Oil's turn spools while heating.
export type BoxSetting = 'auto_read_rfid' | 'auto_init_detect' | 'auto_reload_detect' | 'enable_box' | 'vo_box_spool_roll'

export const boxCommands = {
  load: (box: BoxState, slot: number) => (box.hasController ? `LOAD_FILAMENT_CONTROL SLOT=${slot}` : `E_LOAD slot=${slot}`),
  unload: (box: BoxState, slot: number) =>
    box.hasController ? `UNLOAD_FILAMENT_CONTROL SLOT=${slot}${box.canCold && box.printState !== 'paused' ? ' COLD=1' : ''}` : `E_UNLOAD slot=${slot}`,
  eject: (box: BoxState, slot: number) =>
    box.hasController ? `EJECT_FILAMENT_CONTROL SLOT=${slot}${box.canCold && box.printState !== 'paused' ? ' COLD=1' : ''}` : `E_BOX slot=${slot}`,
  rfid: (box: BoxState, slot: number) => (box.hasController ? `READ_RFID SLOT=${slot}` : `RFID_READ SLOT=slot${slot}`),
  writeTag: (slot: number, filamentId: string, colors: string[]) => `VO_BOX_RFID_WRITE SLOT=${slot} FILAMENT=${filamentId} COLORS=${colors.map((c) => c.replace('#', '').toUpperCase()).join(',')}`,
  cut: () => 'TLTG_CUT_FILAMENT',
  jog: (slot: number, mm: number) => `TLTG_BOX_SLOT_JOG SLOT=${slot} DISTANCE=${mm}`,
  retry: () => 'FLOW_RETRY',
  stop: () => 'FLOW_STOP',
  mapTool: (tool: number, slot: number) => `SAVE_VARIABLE VARIABLE=value_t${tool} VALUE="'slot${slot}'"`,
  setting: (name: BoxSetting, on: boolean) => `SAVE_VARIABLE VARIABLE=${name} VALUE=${on ? 1 : 0}`,
  tool: (tool: number) => `T${tool}`,
  heater: (heater: string, target: number) => `SET_HEATER_TEMPERATURE HEATER=${heater.replace(/^heater_generic /, '')} TARGET=${target}`,
}

/** Highest Box heater target: the lowest Box limit among loaded filaments, capped by the heater's config. */
export function boxHeaterMax(box: BoxState, boxNumber: number, configMax: number | undefined): number {
  const limits = box.slots
    .filter((sl) => sl.index !== QIDI_BOX_EXTERNAL_SLOT && Math.floor(sl.index / 4) === boxNumber - 1 && sl.present)
    .map((sl) => sl.boxMaxTemp)
    .filter((x): x is number => x != null)
  const limit = limits.length ? Math.min(...limits) : null
  const cfg = configMax ?? 65
  return limit == null ? cfg : Math.min(limit, cfg)
}

export function slotByIndex(box: BoxState, index: number | null | undefined) {
  return index == null ? undefined : box.slots.find((s) => s.index === index)
}

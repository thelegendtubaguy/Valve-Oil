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
  type QidiBoxReloadGroups,
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
  type QidiBoxError,
  type QidiBoxStepText,
  type QidiBoxTask,
} from '../qidi/qidi-box-task'

export { QIDI_BOX_EXTERNAL_SLOT, qidiBoxSlotLabel }
export type { QidiBoxSlot, QidiFilament }

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
onKlippyReady(() => {
  if ((printer.get().status.save_variables?.variables?.box_count ?? 0) > 0) loadFilamentLists()
})

// ---- derived state ----
export type BoxClimate = { box: number; humidity: number | null; temperature: number | null; target: number | null; heater: string | null }

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
  return {
    boxCount,
    hasController,
    enabled: Number(variables.enable_box ?? 0) === 1,
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
    lastError: task?.paused ? qidiBoxLastError(s.console.filter((l) => l.kind !== 'command').map((l) => l.text)) : null,
    warnings: qidiBoxWarnings(st.multi_color_controller?.box_warning_info),
    busy,
    mappingLocked: busy || printState === 'paused',
    strandedRecovery,
    strandedSlot: strandedRecovery ? hubPick : null,
    climates,
    printState,
    canCold: commands.includes('TLTG_CUT_FILAMENT'),
    canJog: commands.includes('TLTG_BOX_SLOT_JOG'),
    canCut: commands.includes('TLTG_CUT_FILAMENT'),
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
  return t.key === 'working' ? 'Box step failed' : `${text} failed`
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

export type SlotActions = { load: boolean; unload: boolean; eject: boolean; rfid: boolean; edit: boolean; jog: boolean; cut: boolean; hint: string | null }

/** Which actions the selected slot allows right now, matching the Fluidd fork's QIDI Box card. */
export function slotActions(box: BoxState, slot: QidiBoxSlot): SlotActions {
  const isBox = slot.index !== QIDI_BOX_EXTERNAL_SLOT
  const base = !box.busy && isBox && slot.present
  const atHubLoaded = box.hubSensor === 0 && box.lastLoadSlot === slot.index
  let unload = base && atHubLoaded
  let eject = base
  let hint: string | null = null
  if (box.strandedRecovery && isBox && slot.present) {
    const ok = box.strandedSlot == null || box.strandedSlot === slot.index
    unload = ok
    eject = ok
    hint =
      box.strandedSlot == null
        ? "Filament is stuck between the hub and the toolhead, and it isn't clear which slot it's from. Unload or eject the slot it came from."
        : ok
          ? `Filament from ${slot.label} is stuck between the hub and the toolhead. Unload pulls it back into the Box; Eject removes it.`
          : `Filament from ${qidiBoxSlotLabel(box.strandedSlot)} is stuck between the hub and the toolhead. Unload or eject ${qidiBoxSlotLabel(box.strandedSlot)} first.`
  }
  return {
    load: base && !atHubLoaded,
    unload,
    eject,
    rfid: base,
    edit: !box.busy && slot.present,
    jog: box.canJog && isBox && !box.mappingLocked,
    cut: box.canCut && !box.mappingLocked,
    hint,
  }
}

export const boxCommands = {
  load: (box: BoxState, slot: number) => (box.hasController ? `LOAD_FILAMENT_CONTROL SLOT=${slot}` : `E_LOAD slot=${slot}`),
  unload: (box: BoxState, slot: number) =>
    box.hasController ? `UNLOAD_FILAMENT_CONTROL SLOT=${slot}${box.canCold && box.printState !== 'paused' ? ' COLD=1' : ''}` : `E_UNLOAD slot=${slot}`,
  eject: (box: BoxState, slot: number) =>
    box.hasController ? `EJECT_FILAMENT_CONTROL SLOT=${slot}${box.canCold && box.printState !== 'paused' ? ' COLD=1' : ''}` : `E_BOX slot=${slot}`,
  rfid: (box: BoxState, slot: number) => (box.hasController ? `READ_RFID SLOT=${slot}` : `RFID_READ SLOT=slot${slot}`),
  cut: () => 'TLTG_CUT_FILAMENT',
  jog: (slot: number, mm: number) => `TLTG_BOX_SLOT_JOG SLOT=${slot} DISTANCE=${mm}`,
  retry: () => 'FLOW_RETRY',
  stop: () => 'FLOW_STOP',
  mapTool: (tool: number, slot: number) => `SAVE_VARIABLE VARIABLE=value_t${tool} VALUE="'slot${slot}'"`,
  setting: (name: 'auto_read_rfid' | 'auto_init_detect' | 'auto_reload_detect' | 'enable_box', on: boolean) => `SAVE_VARIABLE VARIABLE=${name} VALUE=${on ? 1 : 0}`,
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

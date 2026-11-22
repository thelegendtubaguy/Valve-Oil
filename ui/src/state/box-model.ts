// QIDI Box state and the commands the Box card sends, derived with the logic ported from the Fluidd fork
// (src/qidi/). Nothing here touches Moonraker or the browser, so tests import it directly; box.ts adds the stores and
// hooks and re-exports everything here.
import type { PrinterState } from './printer'
import { gcode } from '../components/control/gcode'
import { locale, t, type MessageKey } from '../i18n'
import {
  QIDI_BOX_EXTERNAL_SLOT,
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

/** QIDI's filament lists as read from the printer (box.ts's filamentLists store). */
export type Lists = { legacy: QidiFilamentList | null; database: QidiFilament[] | null; databaseError: boolean }

// ---- derived state ----
/** A Box's climate. drying is QIDI's timed drying (BOX_START_DRYING); a target without it is a plain heater setting. */
export type BoxClimate = { box: number; humidity: number | null; temperature: number | null; target: number | null; heater: string | null; drying: boolean; dryMinutesLeft: number | null }

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
  /** QIDI's timed Box drying (BOX_START_DRYING), on firmware that has it. */
  canDry: boolean
  /** QIDI's drying turns each slot's motor, and so its spool, every 5 minutes (box_drying_roll_enable, on by default). */
  dryRolls: boolean
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
    const dry = st.box_extras?.boxes?.[b - 1]
    climates.push({
      box: b,
      humidity: num(aht?.humidity) != null ? Math.round(aht.humidity) : null,
      temperature: num(aht?.temperature) ?? num(heater?.temperature),
      target: num(heater?.target),
      heater: heater ? heaterName : null,
      drying: Number(dry?.dry_state) === 1,
      dryMinutesLeft: Number(dry?.dry_state) === 1 ? num(dry?.end_time) : null,
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
    canCold: commands.includes('VO_CUT_FILAMENT'),
    canDry: commands.includes('BOX_START_DRYING') && commands.includes('BOX_STOP_DRYING'),
    dryRolls: Number(variables.box_drying_roll_enable ?? 1) !== 0,
    canJog: commands.includes('VO_BOX_SLOT_JOG'),
    canCut: commands.includes('VO_CUT_FILAMENT'),
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

// Every step key qidiBoxStepText gives, each with a whole message for the running and the failed step.
const STEP_KEYS = new Set([
  'heat',
  'heat_slot',
  'heat_external',
  'heat_temp',
  'heat_temp_slot',
  'heat_temp_external',
  'cut',
  'wipe',
  'bite',
  'load',
  'load_slot',
  'load_external',
  'unload',
  'unload_slot',
  'unload_external',
  'eject',
  'eject_slot',
  'working',
])

function stepText(s: QidiBoxStepText, failed: boolean): string {
  const key = STEP_KEYS.has(s.key) ? s.key : 'working'
  const vars = { slot: String(s.params.slot ?? ''), temp: String(s.params.temp ?? '') }
  if (!failed) return t(`box.step.${key}` as MessageKey, vars)
  return s.key === QIDI_BOX_STEP_UNKNOWN ? t('box.step.unknown_failed') : t(`box.step.${key}_failed` as MessageKey, vars)
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
  if (at == null) return t('box.slot.stranded')
  const label = qidiBoxSlotLabel(at)
  return at === slot.index ? t('box.slot.stranded_here', { slot: label }) : t('box.slot.stranded_elsewhere', { slot: label })
}

/** What a slot holds, as the Box card's overline: the slot, its tools, and whether it's loaded. */
export function slotOverline(box: BoxState, slot: QidiBoxSlot) {
  const tools = box.toolsBySlot[slot.index] ?? []
  const where = slot.index === QIDI_BOX_EXTERNAL_SLOT ? [t('box.external_spool')] : [t('box.box_n', { n: Math.floor(slot.index / 4) + 1 }), slot.label]
  return [...where, ...tools, box.loadedSlot === slot.index ? t('box.slot.loaded') : null].filter(Boolean).join(' · ')
}

/** Automatic reload for a slot, as the fork's card says it. */
export function reloadText(box: BoxState, slot: QidiBoxSlot) {
  if (!box.autoReload) return t('box.slot.reload_off')
  const group = box.reloadGroups[slot.index]
  if (!group) return t('box.slot.reload_none')
  return t('box.slot.reload_with', { slots: group.members.filter((m) => m !== slot.index).map(qidiBoxSlotLabel).join(', ') })
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
  load: (box: BoxState, slot: number) => (box.hasController ? gcode`LOAD_FILAMENT_CONTROL SLOT=${slot}` : gcode`E_LOAD slot=${slot}`),
  unload: (box: BoxState, slot: number) =>
    box.hasController ? gcode`UNLOAD_FILAMENT_CONTROL SLOT=${slot}${box.canCold && box.printState !== 'paused' ? ' COLD=1' : ''}` : gcode`E_UNLOAD slot=${slot}`,
  eject: (box: BoxState, slot: number) =>
    box.hasController ? gcode`EJECT_FILAMENT_CONTROL SLOT=${slot}${box.canCold && box.printState !== 'paused' ? ' COLD=1' : ''}` : gcode`E_BOX slot=${slot}`,
  rfid: (box: BoxState, slot: number) => (box.hasController ? gcode`READ_RFID SLOT=${slot}` : gcode`RFID_READ SLOT=slot${slot}`),
  writeTag: (slot: number, filamentId: string, colors: string[]) => gcode`VO_BOX_RFID_WRITE SLOT=${slot} FILAMENT=${filamentId} COLORS=${colors.map((c) => c.replace('#', '').toUpperCase()).join(',')}`,
  cut: () => 'VO_CUT_FILAMENT',
  jog: (slot: number, mm: number) => gcode`VO_BOX_SLOT_JOG SLOT=${slot} DISTANCE=${mm}`,
  retry: () => 'FLOW_RETRY',
  stop: () => 'FLOW_STOP',
  mapTool: (tool: number, slot: number) => gcode`SAVE_VARIABLE VARIABLE=value_t${tool} VALUE="'slot${slot}'"`,
  setting: (name: BoxSetting, on: boolean) => gcode`SAVE_VARIABLE VARIABLE=${name} VALUE=${on ? 1 : 0}`,
  tool: (tool: number) => gcode`T${tool}`,
  heater: (heater: string, target: number) => gcode`SET_HEATER_TEMPERATURE HEATER=${heater.replace(/^heater_generic /, '')} TARGET=${target}`,
  // TIME is whole hours.
  dry: (boxNumber: number, temp: number, hours: number) => gcode`BOX_START_DRYING BOX=${boxNumber} TEMP=${temp} TIME=${hours}`,
  stopDry: (boxNumber: number) => gcode`BOX_STOP_DRYING BOX=${boxNumber}`,
}

/** Box slot indexes of a Box: Box 1 holds slots 0-3. */
const boxSlotIndexes = (boxNumber: number) => [0, 1, 2, 3].map((i) => (boxNumber - 1) * 4 + i)

/**
 * Why a Box can't start drying now, or null. Drying turns every slot's motor, which would drive any filament fed into
 * the Box, so every slot of the Box must be empty: nothing at its inlet sensor (filament parked between the slot and the
 * hub, READY), and nothing loaded through the hub to the toolhead. It never starts during a print.
 */
export function dryBlocker(box: BoxState, boxNumber: number): string | null {
  if (box.printState === 'printing' || box.printState === 'paused') return t('box.dry_blocked.printing')
  if (box.task != null) return t('box.dry_blocked.busy')
  const inBox = boxSlotIndexes(boxNumber)
  const fed = new Set(box.slots.filter((sl) => inBox.includes(sl.index) && sl.present).map((sl) => sl.index))
  for (const i of [box.loadedSlot, box.hubSlot]) if (i != null && inBox.includes(i)) fed.add(i)
  if (!fed.size) return null
  const labels = [...fed].sort((x, y) => x - y).map((i) => slotByIndex(box, i)?.label ?? String(i))
  return t('box.dry_blocked.fed', { slots: listAnd(labels) })
}

/** Names as one list in the language shown: "1A, 1B, and 1C". */
export function listAnd(items: string[]): string {
  return new Intl.ListFormat(locale(), { type: 'conjunction' }).format(items)
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

/** The present slots of a Box whose filament sets its heater limit, the lowest Box limit among them. */
export function boxHeatLimiters(box: BoxState, boxNumber: number): QidiBoxSlot[] {
  const inBox = box.slots.filter((sl) => sl.index !== QIDI_BOX_EXTERNAL_SLOT && Math.floor(sl.index / 4) === boxNumber - 1 && sl.present && sl.boxMaxTemp != null)
  const low = Math.min(...inBox.map((sl) => sl.boxMaxTemp!))
  return inBox.filter((sl) => sl.boxMaxTemp === low)
}

export function slotByIndex(box: BoxState, index: number | null | undefined) {
  return index == null ? undefined : box.slots.find((s) => s.index === index)
}

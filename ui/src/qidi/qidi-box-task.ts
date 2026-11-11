/* eslint-disable @typescript-eslint/no-explicit-any */
// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/util/qidi-box-task.ts at
// github.com/thelegendtubaguy/fluidd qidi@1ad1676. Keep behavior in step with the fork.
import { QIDI_BOX_EXTERNAL_SLOT, qidiBoxSlotLabel, qidiBoxSlotNumber, type QidiBoxSlot } from './qidi-box'

// QIDI's Box flows (multi_color_controller.task) run a queue of steps. A failed step leaves the task paused on it
// until FLOW_RETRY reruns it or FLOW_STOP ends the flow.

export interface QidiBoxTask {
  step: string | null;
  paused: boolean;
}

// What a step is doing, as an app.qidi_box.step key and its parameters.
export interface QidiBoxStepText {
  key: string;
  params: Record<string, string | number>;
}

// The text for a step the card doesn't know.
export const QIDI_BOX_STEP_UNKNOWN = 'working'

// code is null for an error without a QIDI code.
export interface QidiBoxError {
  code: string | null;
  message: string;
}

// The slot whose filament a step moves: BOX_ steps act on a Box slot, EXT_ steps on the external spool.
// BOX_CUT and BOX_WIPE act on whatever is at the toolhead, so their text names no slot.
const STEPS: Record<string, { action: string, slot: 'active' | 'unloading' | 'external' | null }> = {
  BOX_HEAT: { action: 'heat', slot: 'active' },
  BOX_CUT: { action: 'cut', slot: null },
  BOX_UNLOAD: { action: 'unload', slot: 'unloading' },
  BOX_LOAD: { action: 'load', slot: 'active' },
  BOX_WIPE: { action: 'wipe', slot: null },
  BOX_EJECT: { action: 'eject', slot: 'active' },
  EXT_HEAT: { action: 'heat', slot: 'external' },
  EXT_CUT: { action: 'cut', slot: null },
  EXT_UNLOAD: { action: 'unload', slot: 'external' },
  EXT_BITE: { action: 'bite', slot: null },
  EXT_LOAD: { action: 'load', slot: 'external' },
  EXT_WIPE: { action: 'wipe', slot: null }
}

// Steps whose text can name their slot, and the heat step, which can also give the nozzle target.
const SLOT_ACTIONS = ['heat', 'unload', 'load', 'eject']

const boxSlot = (value: unknown): number | null => {
  const slot = qidiBoxSlotNumber(value)

  return slot != null && slot !== QIDI_BOX_EXTERNAL_SLOT ? slot : null
}

// A finished flow resets current_queue and current_step but never publishes flow_id going back to null, so judge
// activity from the fields that do reset.
export const qidiBoxTask = (printer: Record<string, any>): QidiBoxTask | null => {
  const task = printer.multi_color_controller?.task

  if (!(task?.current_queue?.length || task?.current_step || task?.is_paused)) return null

  return {
    step: typeof task.current_step === 'string' && task.current_step !== '' ? task.current_step : null,
    paused: task.is_paused === true
  }
}

// operation.active_slot is the flow's target slot. A load with another slot's filament at the hub first unloads
// that slot, last_load_slot.
export const qidiBoxStepSlot = (printer: Record<string, any>, step: string | null): number | null => {
  const kind = step != null ? STEPS[step]?.slot : null
  const controller = printer.multi_color_controller

  switch (kind) {
    case 'external':
      return QIDI_BOX_EXTERNAL_SLOT
    case 'unloading':
      return boxSlot(controller?.box?.last_load_slot ?? printer.save_variables?.variables?.last_load_slot) ??
        boxSlot(controller?.operation?.active_slot)
    case 'active':
      return boxSlot(controller?.operation?.active_slot)
    default:
      return null
  }
}

export const qidiBoxStepText = (step: string | null, slot: string | null, temp: number | null): QidiBoxStepText => {
  const action = step != null ? STEPS[step]?.action : null

  if (action == null) return { key: QIDI_BOX_STEP_UNKNOWN, params: {} }
  if (!SLOT_ACTIONS.includes(action)) return { key: action, params: {} }

  const params: Record<string, string | number> = {}
  let key = action

  if (action === 'heat' && temp != null) {
    key += '_temp'
    params.temp = temp
  }

  if (step?.startsWith('EXT_')) {
    key += '_external'
  } else if (slot != null) {
    key += '_slot'
    params.slot = slot
  }

  return { key, params }
}

// The running or failed step's text, naming its slot and, while heating, the nozzle target.
export const qidiBoxTaskText = (printer: Record<string, any>): QidiBoxStepText | null => {
  const task = qidiBoxTask(printer)

  if (task == null) return null

  const slot = qidiBoxStepSlot(printer, task.step)
  const label = slot != null ? qidiBoxSlotLabel(slot) : null
  const target = Number(printer.extruder?.target)

  return qidiBoxStepText(task.step, label, Number.isFinite(target) && target > 0 ? Math.round(target) : null)
}

const ERROR = /Code:\s*(QDE_\d+_\d+)\s*;\s*Message:(.*)$/
const FAILED_STEP = /\bStep \S+ failed\. Pausing flow\b/

const parseError = (line: string): QidiBoxError | null => {
  const match = ERROR.exec(line.replace(/<br \/>/g, ' ').trim())

  return match ? { code: match[1], message: match[2].trim() } : null
}

// A step that raises an error instead of failing is paused by Qidi-Max-4-Optimized with QIDI's
// `Step BOX_LOAD failed. Pausing flow.`, and Klipper then reports the error, such as
// `!! Error during homing e_endstop: Communication timeout during homing`.
const parseRaised = (line: string | undefined): QidiBoxError | null => {
  if (line == null || !line.startsWith('!!')) return null

  const message = line.replace(/^!!\s*/, '').replace(/<br \/>/g, ' ').trim()

  return message && !ERROR.test(message) ? { code: null, message } : null
}

// QIDI reports a Box error as `!! Code:QDE_004_006; Message:Extruder loading failure.`, then the flow's
// `Step BOX_LOAD failed. Pausing flow.` The current failure's error is the latest one since the flow before it
// failed, so an earlier failure's error is not shown for a step that failed without one. Without a QIDI error,
// the error Klipper reported right after the failure is used.
export const qidiBoxLastError = (messages: readonly string[]): QidiBoxError | null => {
  let failures = 0
  let raised: QidiBoxError | null = null

  for (let i = messages.length - 1; i >= 0; i--) {
    const line = messages[i].replace(/^(?:!!|\/\/)\s*/, '')

    if (FAILED_STEP.test(line)) {
      if (++failures > 1) return raised

      raised = parseRaised(messages[i + 1])
    }

    const error = parseError(line)

    if (error) return error
  }

  return raised
}

// multi_color_controller.box_warning_info lists QIDI's Box heater and sensor warnings.
export const qidiBoxWarnings = (value: unknown): string[] => {
  if (!Array.isArray(value)) return []

  const warnings = value.flatMap(warning => {
    if (warning == null || warning === '') return []
    if (typeof warning === 'string') {
      const error = parseError(warning)

      return [error ? `${error.message} (${error.code})` : warning]
    }
    if (typeof warning === 'object') {
      const { message, msg, code } = warning
      const text = message ?? msg

      if (text != null) return [code != null ? `${text} (${code})` : String(text)]
    }

    return [JSON.stringify(warning)]
  })

  return [...new Set(warnings)]
}

export const qidiBoxHubSensor = (printer: Record<string, any>): number | null => {
  return printer.box_extras?.b_endstop_state ?? printer.multi_color_controller?.box?.b_endstop_state ?? null
}

// Filament stopped between the hub and the toolhead, outside a print: the hub sensor sees it and the toolhead
// sensor doesn't. Qidi-Max-4-Optimized's Unload and Eject then pull back the slot at the hub, even while a failed
// flow is paused.
export const qidiBoxStranded = (printer: Record<string, any>): boolean => {
  return printer['filament_switch_sensor filament_switch_sensor']?.filament_detected === false &&
    qidiBoxHubSensor(printer) === 0 &&
    !['printing', 'paused'].includes(printer.print_stats?.state)
}

// Stranded filament can be unloaded or ejected while no flow runs or a failed flow is paused, unless the Box's
// button is busy.
export const qidiBoxStrandedRecovery = (printer: Record<string, any>): boolean => {
  const task = qidiBoxTask(printer)

  return !printer.box_extras?.box_button_state && (task == null || task.paused) && qidiBoxStranded(printer)
}

// The slot whose filament is at the hub, picked like Qidi-Max-4-Optimized's Unload and Eject: the slot it recorded
// at the hub or the last-loaded slot, each only while its inlet has filament, then the only slot with filament at
// its inlet. Two different named slots can't be told apart.
export const qidiBoxHubSlot = (printer: Record<string, any>, slots: Pick<QidiBoxSlot, 'index' | 'present'>[]): number | null => {
  const variables = printer.save_variables?.variables ?? {}
  const present = slots.filter(slot => slot.index !== QIDI_BOX_EXTERNAL_SLOT && slot.present).map(slot => slot.index)
  const named = [...new Set([boxSlot(variables.tltg_box_hub_slot), boxSlot(variables.last_load_slot)])]
    .filter((slot): slot is number => slot != null && present.includes(slot))

  if (named.length) return named.length === 1 ? named[0] : null

  return present.length === 1 ? present[0] : null
}

// The slot feeding the extruder. QIDI's slot_sync names the Box slot synced to the extruder, but QIDI leaves it at
// 'slot-1' while a Box slot is printing or unloading. With no slot synced and filament at the toolhead, filament at
// the hub is the Box slot's, picked as for the hub; otherwise it's the external spool's, which doesn't pass the hub.
export const qidiBoxLoadedSlot = (printer: Record<string, any>, slots: Pick<QidiBoxSlot, 'index' | 'present'>[]): number | null => {
  const sync = printer.save_variables?.variables?.slot_sync
  const slot = qidiBoxSlotNumber(sync)

  if (slot != null) return slot
  if (sync !== 'slot-1' || printer['filament_switch_sensor filament_switch_sensor']?.filament_detected !== true) return null

  return qidiBoxHubSensor(printer) === 0 ? qidiBoxHubSlot(printer, slots) : QIDI_BOX_EXTERNAL_SLOT
}

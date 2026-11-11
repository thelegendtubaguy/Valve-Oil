import { describe, it, expect } from 'vitest'
import { qidiBoxSlots } from '../qidi-box'
import {
  qidiBoxHubSlot,
  qidiBoxLastError,
  qidiBoxLoadedSlot,
  qidiBoxStranded,
  qidiBoxStrandedRecovery,
  qidiBoxTask,
  qidiBoxTaskText,
  qidiBoxWarnings
} from '../qidi-box-task'

// A Max 4 with one Box, slot 1B at its inlet, during a Box flow.
const printer = (overrides: {
  task?: Record<string, unknown>,
  activeSlot?: string,
  lastLoadSlot?: string,
  hubSlot?: string,
  slotSync?: string,
  target?: number,
  hub?: number,
  toolhead?: boolean,
  printState?: string,
  inlets?: number[]
} = {}) => ({
  save_variables: {
    variables: {
      box_count: 1,
      last_load_slot: overrides.lastLoadSlot ?? 'slot16',
      tltg_box_hub_slot: overrides.hubSlot ?? '',
      slot_sync: overrides.slotSync ?? 'slot-1'
    }
  },
  multi_color_controller: {
    task: { flow_id: 3, current_queue: [], current_step: '', is_paused: false, ...overrides.task },
    operation: { active_slot: overrides.activeSlot ?? 'slot16' },
    box: { b_endstop_state: overrides.hub ?? 1 }
  },
  extruder: { target: overrides.target ?? 0 },
  print_stats: { state: overrides.printState ?? 'standby' },
  'filament_switch_sensor filament_switch_sensor': { filament_detected: overrides.toolhead ?? false },
  ...Object.fromEntries([0, 1, 2, 3].map(index => [
    `box_stepper slot${index}`,
    { runout_button: (overrides.inlets ?? [1]).includes(index) ? 0 : 1 }
  ]))
})

const taskText = (state: ReturnType<typeof printer>) => qidiBoxTaskText(state)

describe('qidiBoxTask', () => {
  it('is idle once the flow resets its queue and step, though flow_id stays', () => {
    expect(qidiBoxTask(printer())).toBeNull()
  })

  it('reports the running step', () => {
    expect(qidiBoxTask(printer({ task: { current_queue: ['BOX_HEAT', 'BOX_LOAD'], current_step: 'BOX_HEAT' } })))
      .toEqual({ step: 'BOX_HEAT', paused: false })
  })

  it('reports a step that failed and paused the flow', () => {
    expect(qidiBoxTask(printer({ task: { current_queue: ['BOX_LOAD'], current_step: 'BOX_LOAD', is_paused: true } })))
      .toEqual({ step: 'BOX_LOAD', paused: true })
  })
})

describe('qidiBoxTaskText', () => {
  const running = (step: string, overrides: Parameters<typeof printer>[0] = {}) =>
    taskText(printer({ task: { current_queue: [step], current_step: step }, ...overrides }))

  it('names the slot and nozzle target while heating for a Box load', () => {
    expect(running('BOX_HEAT', { activeSlot: 'slot1', target: 239.6 }))
      .toEqual({ key: 'heat_temp_slot', params: { temp: 240, slot: '1B' } })
  })

  it('leaves out a nozzle target that is off', () => {
    expect(running('BOX_HEAT', { activeSlot: 'slot5' })).toEqual({ key: 'heat_slot', params: { slot: '2B' } })
  })

  it('names the slot being loaded', () => {
    expect(running('BOX_LOAD', { activeSlot: 'slot1', lastLoadSlot: 'slot2' })).toEqual({ key: 'load_slot', params: { slot: '1B' } })
  })

  it('names the last-loaded slot while unloading it for another slot', () => {
    expect(running('BOX_UNLOAD', { activeSlot: 'slot1', lastLoadSlot: 'slot2' })).toEqual({ key: 'unload_slot', params: { slot: '1C' } })
  })

  it('names the target slot while unloading when no Box slot was loaded', () => {
    expect(running('BOX_UNLOAD', { activeSlot: 'slot1' })).toEqual({ key: 'unload_slot', params: { slot: '1B' } })
  })

  it('names the external spool for its steps', () => {
    expect(running('EXT_HEAT', { target: 220 })).toEqual({ key: 'heat_temp_external', params: { temp: 220 } })
    expect(running('EXT_LOAD')).toEqual({ key: 'load_external', params: {} })
    expect(running('EXT_BITE')).toEqual({ key: 'bite', params: {} })
  })

  it.each([
    ['BOX_CUT', 'cut'],
    ['EXT_CUT', 'cut'],
    ['BOX_WIPE', 'wipe'],
    ['EXT_WIPE', 'wipe']
  ])('describes %s without a slot', (step, key) => {
    expect(running(step, { activeSlot: 'slot1' })).toEqual({ key, params: {} })
  })

  it('falls back to generic text without a target slot', () => {
    expect(running('BOX_LOAD')).toEqual({ key: 'load', params: {} })
    expect(running('BOX_EJECT')).toEqual({ key: 'eject', params: {} })
  })

  it('falls back to working text for an unknown step', () => {
    expect(running('BOX_FUTURE_STEP', { activeSlot: 'slot1' })).toEqual({ key: 'working', params: {} })
  })

  it('describes a failed step the same way', () => {
    expect(taskText(printer({ task: { current_queue: ['BOX_LOAD'], current_step: 'BOX_LOAD', is_paused: true }, activeSlot: 'slot1' })))
      .toEqual({ key: 'load_slot', params: { slot: '1B' } })
  })

  it('has no text while idle', () => {
    expect(taskText(printer())).toBeNull()
  })
})

describe('qidiBoxLastError', () => {
  it('reads the latest Box error code and message', () => {
    expect(qidiBoxLastError([
      '!! Code:QDE_004_001; Message:Old failure.',
      'Step BOX_LOAD failed. Pausing flow.',
      '$ FLOW_STOP',
      '$ LOAD_FILAMENT_CONTROL SLOT=1',
      '!! Code:QDE_004_006; Message:Extruder loading failure.',
      '// Step BOX_LOAD failed. Pausing flow.'
    ])).toEqual({ code: 'QDE_004_006', message: 'Extruder loading failure.' })
  })

  it('reads an error without a QIDI code reported right after the failure', () => {
    expect(qidiBoxLastError([
      '!! Code:QDE_004_001; Message:Old failure.',
      'Step BOX_LOAD failed. Pausing flow.',
      '$ LOAD_FILAMENT_CONTROL SLOT=1',
      '// Step BOX_LOAD failed. Pausing flow.',
      '!! Error during homing e_endstop: Communication timeout during homing'
    ])).toEqual({ code: null, message: 'Error during homing e_endstop: Communication timeout during homing' })
  })

  it('prefers QIDI\'s coded error and ignores unrelated console errors', () => {
    expect(qidiBoxLastError([
      '!! Code:QDE_004_006; Message:Extruder loading failure.',
      '// Step BOX_LOAD failed. Pausing flow.',
      '!! Must home axis first'
    ])).toEqual({ code: 'QDE_004_006', message: 'Extruder loading failure.' })
    expect(qidiBoxLastError([
      '!! Must home axis first',
      '$ LOAD_FILAMENT_CONTROL SLOT=1',
      '// Step BOX_LOAD failed. Pausing flow.'
    ])).toBeNull()
  })

  it('ignores an earlier failure\'s error when the current one reported none', () => {
    expect(qidiBoxLastError([
      '!! Code:QDE_004_001; Message:Old failure.',
      'Step BOX_LOAD failed. Pausing flow.',
      'Step BOX_UNLOAD failed. Pausing flow.'
    ])).toBeNull()
  })
})

describe('qidiBoxWarnings', () => {
  it('lists QIDI\'s Box warnings once each', () => {
    expect(qidiBoxWarnings(['Code:QDE_002_001; Message:Box heater fault.', 'Humidity sensor', 'Humidity sensor', ''])).toEqual([
      'Box heater fault. (QDE_002_001)',
      'Humidity sensor'
    ])
    expect(qidiBoxWarnings(undefined)).toEqual([])
  })
})

describe('qidiBoxStranded', () => {
  it('is stranded with filament at the hub but not the toolhead', () => {
    expect(qidiBoxStranded(printer({ hub: 0 }))).toBe(true)
  })

  it.each([
    ['the hub is empty', { hub: 1 }],
    ['the toolhead sees filament', { hub: 0, toolhead: true }],
    ['printing', { hub: 0, printState: 'printing' }],
    ['a print is paused', { hub: 0, printState: 'paused' }]
  ])('is not stranded when %s', (_, overrides) => {
    expect(qidiBoxStranded(printer(overrides))).toBe(false)
  })
})

describe('qidiBoxStrandedRecovery', () => {
  it('allows Unload and Eject with no flow, or with a failed flow paused', () => {
    expect(qidiBoxStrandedRecovery(printer({ hub: 0 }))).toBe(true)
    expect(qidiBoxStrandedRecovery(printer({ hub: 0, task: { current_queue: ['BOX_LOAD'], current_step: 'BOX_LOAD', is_paused: true } }))).toBe(true)
  })

  it('blocks them while a flow runs, as during a normal load', () => {
    expect(qidiBoxStrandedRecovery(printer({ hub: 0, task: { current_queue: ['BOX_LOAD'], current_step: 'BOX_LOAD' } }))).toBe(false)
  })

  it('blocks them during a paused print', () => {
    expect(qidiBoxStrandedRecovery(printer({ hub: 0, printState: 'paused', task: { current_step: 'BOX_LOAD', is_paused: true } }))).toBe(false)
  })
})

describe('qidiBoxHubSlot', () => {
  const hubSlot = (overrides: Parameters<typeof printer>[0]) => {
    const state = printer({ hub: 0, ...overrides })

    return qidiBoxHubSlot(state, qidiBoxSlots(state, null))
  }

  it('takes the slot recorded at the hub or the last-loaded slot while its inlet has filament', () => {
    expect(hubSlot({ hubSlot: 'slot1', lastLoadSlot: 'slot1', inlets: [1, 2] })).toBe(1)
    expect(hubSlot({ lastLoadSlot: 'slot2', inlets: [1, 2] })).toBe(2)
    expect(hubSlot({ hubSlot: 'slot1', lastLoadSlot: 'slot2', inlets: [2, 3] })).toBe(2)
  })

  it('can\'t tell two different named slots apart', () => {
    expect(hubSlot({ hubSlot: 'slot1', lastLoadSlot: 'slot2', inlets: [1, 2] })).toBeNull()
  })

  it('falls back to the only slot with filament at its inlet', () => {
    expect(hubSlot({ inlets: [3] })).toBe(3)
    expect(hubSlot({ inlets: [1, 3] })).toBeNull()
  })
})

describe('qidiBoxLoadedSlot', () => {
  const loadedSlot = (overrides: Parameters<typeof printer>[0]) => {
    const state = printer(overrides)

    return qidiBoxLoadedSlot(state, qidiBoxSlots(state, null))
  }

  it('takes the Box slot synced to the extruder', () => {
    expect(loadedSlot({ slotSync: 'slot1', hub: 0, toolhead: true })).toBe(1)
  })

  it('takes the slot at the hub when no slot is synced and the toolhead sees filament, as while printing', () => {
    expect(loadedSlot({ hub: 0, toolhead: true, lastLoadSlot: 'slot1' })).toBe(1)
    expect(loadedSlot({ hub: 0, toolhead: true, inlets: [1, 2] })).toBeNull()
  })

  it('takes the external spool when the toolhead sees filament that isn\'t at the hub', () => {
    expect(loadedSlot({ hub: 1, toolhead: true, lastLoadSlot: 'slot1' })).toBe(16)
  })

  it('has nothing loaded with the toolhead empty', () => {
    expect(loadedSlot({ hub: 0, lastLoadSlot: 'slot1' })).toBeNull()
    expect(loadedSlot({ hub: 1 })).toBeNull()
  })
})

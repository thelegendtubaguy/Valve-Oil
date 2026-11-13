#!/usr/bin/env node
// A fake Moonraker for developing Valve Oil UI without a printer.
//
//   node dev/fake-moonraker.mjs [--port 7125] [--scenario printing|idle|box-busy|box-failed|stranded|no-box]
//                               [--updates none|release-off|firmware-printing|boards|running|failed|interrupted|refused|
//                                          firmware-failed|firmware-rescue|closed-loop|thr-waiting|reinstall|absent]
//
// Serves the Moonraker websocket and file endpoints the UI uses, plus ui/dist at /valve-oil/ (same origin, like the
// printer's nginx). Run `npm run build` first to use /valve-oil/, or point `npm run dev` at it with
// http://localhost:5173/?moonraker=localhost:7125. The data is shaped like a QIDI Max 4 on firmware 01.01.07.01 with
// two Boxes; switch scenarios at runtime from the console with `VO_SCENARIO NAME=<scenario>`, and Valve Oil's update
// status (/tltg-status/api) with `VO_UPDATES NAME=<scenario>`.
import { createServer } from 'node:http'
import { readFile, stat } from 'node:fs/promises'
import { extname, join, normalize } from 'node:path'
import { fileURLToPath } from 'node:url'
import { WebSocketServer } from 'ws'

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => (a.startsWith('--') ? [...acc, [a.slice(2), all[i + 1]]] : acc), []))
const PORT = Number(args.port ?? 7125)
const DIST = fileURLToPath(new URL('../dist/', import.meta.url))
// With only --updates given, the printer starts idle so Update now is available.
let scenario = args.scenario ?? (args.updates ? 'idle' : 'printing')

// ---------- printer model ----------
const FILAMENTS = {
  'QIDI PLA Basic': { filament_id: 'QFA01', type: 'PLA', filament_vendor: 'QIDI', nozzle_temperature: [190, 230], box_temperature_range_high: ['50'] },
  'QIDI PLA Matte': { filament_id: 'QFA02', type: 'PLA', filament_vendor: 'QIDI', nozzle_temperature: [190, 230], box_temperature_range_high: ['50'] },
  'QIDI PETG Tough': { filament_id: 'QFB01', type: 'PETG', filament_vendor: 'QIDI', nozzle_temperature: [230, 260], box_temperature_range_high: ['60'] },
  'Polymaker PETG': { filament_id: 'QFB12', type: 'PETG', filament_vendor: 'Polymaker', nozzle_temperature: [230, 260], box_temperature_range_high: ['60'] },
  'Bambu ABS': { filament_id: 'QFC20', type: 'ABS', filament_vendor: 'Bambu', nozzle_temperature: [240, 270], box_temperature_range_high: ['65'] },
  'Overture PLA Silk': { filament_id: 'QFA40', type: 'PLA', filament_vendor: 'Overture', nozzle_temperature: [200, 230], box_temperature_range_high: ['50'] },
  'Generic TPU 95A': { filament_id: 'QFD01', type: 'TPU', filament_vendor: 'Generic', nozzle_temperature: [210, 240], box_temperature_range_high: ['0'] },
}
const fi = (index, key, color) => {
  if (!key) return { index, filament_id: 'None', filament_str: '', filament_type: '', vendor_str: '', color_rgb_str: [''], min_temp: 0, max_temp: 0, box_max_temp: 0 }
  const f = FILAMENTS[key]
  return { index, filament_id: f.filament_id, filament_str: key.slice(f.filament_vendor.length + 1), filament_type: f.type, vendor_str: f.filament_vendor, color_rgb_str: [color], min_temp: f.nozzle_temperature[0], max_temp: f.nozzle_temperature[1], box_max_temp: Number(f.box_temperature_range_high[0]) }
}
const BOX_SLOTS = [
  ['QIDI PLA Basic', '1D1D1F'],
  ['QIDI PLA Matte', 'F2EFE8'],
  ['Polymaker PETG', 'FF7A1A'],
  [null, ''],
  ['Bambu ABS', '8A8F98'],
  ['Overture PLA Silk', '2F6BFF'],
  ['Generic TPU 95A', 'E5484D'],
  ['Polymaker PETG', 'FF7A1A'],
]

const FILES = [
  { path: 'planter_ribbed.gcode.3mf', est: 7260, types: 'PLA;PLA', colors: ['#F2EFE8', '#2F6BFF'], used: [0, 1], hue: 40 },
  { path: 'cable_clip_x12.gcode', est: 2280, types: 'PETG', colors: ['#FF7A1A'], used: [0], hue: 25 },
  { path: 'gear_m2_32t.gcode.3mf', est: 4320, types: 'ABS', colors: ['#8A8F98'], used: [0], hue: 210 },
  { path: 'wall_hook.gcode', est: 1320, types: 'PLA', colors: ['#1D1D1F'], used: [0], hue: 140 },
  { path: 'enclosure_lid.gcode.3mf', est: 16800, types: 'PETG', colors: ['#FF7A1A'], used: [0], hue: 260 },
  { path: 'spool_holder.gcode', est: 6600, types: 'PLA', colors: ['#F2EFE8'], used: [0], hue: 45 },
  { path: 'phone_stand.gcode.3mf', est: 3480, types: 'PLA;PLA;PLA', colors: ['#1D1D1F', '#F2EFE8', '#2F6BFF'], used: [0, 1, 2], hue: 330 },
  { path: 'calibration/flow_cube.gcode', est: 900, types: 'PLA', colors: ['#2F6BFF'], used: [0], hue: 200 },
]
const now = () => Date.now() / 1000
const fileEntries = FILES.map((f, i) => ({ path: f.path, modified: now() - 3600 * (i * 9 + 2), size: 400000 + i * 233000, permissions: 'rw' }))

function metadata(path) {
  const f = FILES.find((x) => x.path === path)
  if (!f) return null
  const e = fileEntries.find((x) => x.path === path)
  const thumb = `.thumbs/${path.split('/').pop().replace(/\.gcode(\.3mf)?$/, '')}-300x300.png`
  return {
    filename: path,
    size: e?.size,
    modified: e?.modified,
    slicer: 'OrcaSlicer',
    slicer_version: '2.3.1',
    estimated_time: f.est,
    layer_height: 0.2,
    first_layer_height: 0.2,
    object_height: 48,
    filament_total: 12000,
    filament_weight_total: 36,
    filament_type: f.types,
    filament_colors: f.colors,
    filament_name: f.types.split(';').map((t, i) => (i === 0 ? `QIDI ${t} Matte` : `Overture ${t} Silk`)).join(';'),
    filament_weights: f.colors.map((_, i) => (i === 0 ? 30 : 6)),
    used_extruders: f.used,
    thumbnails: [
      { width: 32, height: 32, size: 900, relative_path: thumb.replace('300x300', '32x32') },
      { width: 300, height: 300, size: 30000, relative_path: thumb },
    ],
  }
}

function thumbSvg(name) {
  const f = FILES.find((x) => x.path.split('/').pop().replace(/\.gcode(\.3mf)?$/, '') === name)
  const hue = f?.hue ?? 200
  const c = f?.colors?.[0] ?? `hsl(${hue} 60% 55%)`
  return `<svg xmlns="http://www.w3.org/2000/svg" width="300" height="300" viewBox="0 0 300 300">
  <defs><linearGradient id="g" x1="0" x2="1"><stop offset="0" stop-color="${c}" stop-opacity=".75"/><stop offset=".5" stop-color="${c}"/><stop offset="1" stop-color="${c}" stop-opacity=".6"/></linearGradient></defs>
  <ellipse cx="150" cy="250" rx="90" ry="14" fill="#0003"/>
  <path d="M70 70 L230 70 L210 245 L90 245Z" fill="url(#g)" stroke="#0002"/>
  <ellipse cx="150" cy="70" rx="80" ry="16" fill="${c}" stroke="#0003"/>
  ${f?.colors?.[1] ? `<rect x="84" y="190" width="132" height="14" fill="${f.colors[1]}"/>` : ''}
</svg>`
}

function camSvg() {
  const p = state.status.virtual_sdcard.progress
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <defs><radialGradient id="r" cx=".5" cy=".1" r=".9"><stop offset="0" stop-color="#4a505c"/><stop offset=".7" stop-color="#15171c"/></radialGradient></defs>
  <rect width="1280" height="720" fill="url(#r)"/>
  <path d="M140 680 L1140 680 L980 470 L300 470Z" fill="#2a2e36"/>
  <rect x="560" y="${470 - 160 * p}" width="160" height="${160 * p}" fill="#ece8df"/>
  <rect x="580" y="140" width="120" height="70" rx="8" fill="#1b1e24"/><rect x="636" y="210" width="8" height="${Math.max(10, 250 - 160 * p - 210)}" fill="#999"/>
  <text x="30" y="700" fill="#fff8" font-family="monospace" font-size="20">fake camera · ${new Date().toLocaleTimeString()}</text>
</svg>`
}

const state = { status: {}, console: [], history: [], flow: null, relative: false, saved: false }
function vars() {
  return state.status.save_variables.variables
}

function baseStatus() {
  const variables = {
    box_count: 2,
    enable_box: 1,
    auto_reload_detect: 1,
    auto_read_rfid: 1,
    auto_init_detect: 0,
    slot_sync: 'slot1',
    last_load_slot: 'slot1',
    tltg_box_hub_slot: 'slot1',
    filament_slot16: 'QFA01',
    color_slot16: ['F5D90A'],
  }
  for (let t = 0; t < 16; t++) variables[`value_t${t}`] = `slot${t}`
  const status = {
    webhooks: { state: 'ready', state_message: 'Printer is ready' },
    print_stats: { filename: '', state: 'standby', print_duration: 0, total_duration: 0, filament_used: 0, message: '', info: { current_layer: null, total_layer: null } },
    virtual_sdcard: { progress: 0, is_active: false, file_position: 0 },
    display_status: { progress: 0, message: null },
    idle_timeout: { state: 'Idle' },
    pause_resume: { is_paused: false },
    toolhead: { homed_axes: '', position: [195, 195, 30, 0] },
    gcode_move: { speed_factor: 1, extrude_factor: 1, homing_origin: [0, 0, 0, 0], gcode_position: [0, 0, 0, 0] },
    extruder: { temperature: 32, target: 0, power: 0 },
    heater_bed: { temperature: 28, target: 0, power: 0 },
    'heater_generic chamber': { temperature: 30, target: 0, power: 0 },
    'heater_generic heater_box1': { temperature: 31, target: 0, power: 0 },
    'heater_generic heater_box2': { temperature: 26, target: 0, power: 0 },
    'aht20_f heater_box1': { temperature: 31, humidity: 14 },
    'aht20_f heater_box2': { temperature: 26, humidity: 43 },
    fan: { speed: 0 },
    'fan_generic chamber_circulation_fan': { speed: 0 },
    'output_pin caselight': { value: 1 },
    'filament_switch_sensor filament_switch_sensor': { filament_detected: true, enabled: true },
    save_variables: { variables },
    box_extras: { boxes: [0, 1].map((b) => ({ filament_info: [0, 1, 2, 3].map((i) => fi(i, ...BOX_SLOTS[b * 4 + i])) })) },
    multi_color_controller: {
      box: { b_endstop_state: 0, last_load_slot: 'slot1' },
      task: { flow_id: 3, current_queue: [], current_step: '', is_paused: false },
      operation: { active_slot: 'slot1' },
      extra: { filament_info: { ...fi(0, 'QIDI PLA Basic', 'F5D90A'), index: 16 } },
      box_warning_info: [],
    },
    tltg_box_feed_motion: {
      active: true,
      status: '',
      firmware: '01.01.07.01',
      layout: 'staged',
      fields: ['hub_speed', 'fast_length', 'fast_speed_1', 'fast_speed_2', 'approach_speed', 'load_accel', 'unload_extruder_length', 'unload_speed_1', 'unload_speed_2', 'unload_accel', 'park_speed'],
      global_only: ['hub_speed', 'unload_extruder_length', 'park_speed'],
      limits: {
        hub_speed: { min: 10, max: 150, unit: 'mm/s' },
        park_speed: { min: 10, max: 150, unit: 'mm/s' },
        fast_length: { min: 100, max: 2000, unit: 'mm' },
        fast_speed_1: { min: 10, max: 150, unit: 'mm/s' },
        fast_speed_2: { min: 10, max: 150, unit: 'mm/s' },
        approach_speed: { min: 2, max: 40, unit: 'mm/s' },
        load_accel: { min: 20, max: 400, unit: 'mm/s²' },
        unload_speed_1: { min: 10, max: 150, unit: 'mm/s' },
        unload_speed_2: { min: 10, max: 150, unit: 'mm/s' },
        unload_accel: { min: 20, max: 400, unit: 'mm/s²' },
        unload_extruder_length: { min: 20, max: 300, unit: 'mm' },
      },
      vendor: { hub_speed: 85, fast_length: 930, fast_speed_1: 85, fast_speed_2: 65, approach_speed: 10, load_accel: 100, unload_speed_1: 65, unload_speed_2: 85, unload_accel: 100, unload_extruder_length: 120, park_speed: 80 },
      settings: { hub_speed: null, fast_length: 950, fast_speed_1: 85, fast_speed_2: 65, approach_speed: 15, load_accel: null, unload_speed_1: 65, unload_speed_2: 85, unload_accel: null, unload_extruder_length: 55, park_speed: null },
      problems: [],
      slots: {},
    },
    tltg_printer_warnings: { warnings: [] },
    tltg_klipper_health: { timers: 61, removed_timers: 0, main_thread_load: 0.22, timer_delay: 0.001 },
    mcu: { last_stats: { mcu_awake: 0.031, mcu_task_avg: 0.00002 } },
    'mcu THR': { last_stats: { mcu_awake: 0.012 } },
    'mcu mcu_box1': { last_stats: { mcu_awake: 0.004 } },
    'mcu mcu_box2': { last_stats: { mcu_awake: 0.004 } },
    'temperature_sensor mcu_temp': { temperature: 41.2 },
    'temperature_sensor chamber_temp': { temperature: 30 },
    motion_report: { live_velocity: 0, live_extruder_velocity: 0, live_position: [0, 0, 0, 0] },
    'gcode_macro PRINT_START': {},
    'gcode_macro PRINT_END': {},
    'gcode_macro CANCEL_PRINT': {},
    'gcode_macro PAUSE': {},
    'gcode_macro RESUME': {},
    'gcode_macro M600': {},
    'gcode_macro BED_MESH_CALIBRATE': {},
    'gcode_macro CLEAR_NOZZLE': {},
    'gcode_macro SHAPER_CALIBRATE': {},
    'gcode_macro TLTG_BOX_MOTION': {},
    'gcode_macro TLTG_RESET_TOOL_MAPPINGS': {},
    'gcode_macro _TLTG_INTERNAL': {},
    tltg_printer: { name: 'Max 4' },
  }
  for (let i = 0; i < 16; i++) status[`box_stepper slot${i}`] = { runout_button: i < 8 && BOX_SLOTS[i]?.[0] ? 0 : 1, rfid_state: 0 }
  return status
}

function applyScenario(name) {
  scenario = name
  state.status = baseStatus()
  state.flow = null
  const st = state.status
  if (name === 'printing') {
    Object.assign(st.print_stats, { filename: 'planter_ribbed.gcode.3mf', state: 'printing', print_duration: 4440, total_duration: 4850, filament_used: 7400, info: { current_layer: 149, total_layer: 240 } })
    Object.assign(st.virtual_sdcard, { progress: 0.62, is_active: true })
    Object.assign(st.display_status, { progress: 0.62 })
    Object.assign(st.extruder, { temperature: 220.2, target: 220 })
    Object.assign(st.heater_bed, { temperature: 60.1, target: 60 })
    Object.assign(st['heater_generic chamber'], { temperature: 34 })
    Object.assign(st['heater_generic heater_box1'], { temperature: 42, target: 45 })
    Object.assign(st.idle_timeout, { state: 'Printing' })
    st.toolhead.homed_axes = 'xyz'
    st.fan.speed = 1
    st['fan_generic chamber_circulation_fan'].speed = 0.4
    for (const line of ['// Print start: homing', '// Adaptive mesh 3x5', '// Loading 1B', '// Purge to chute complete', '// Chamber guard raised exhaust to 40%', '// Swapped to 2B for layer 118', '// Layer 145 of 240']) state.console.push({ message: line, time: now() - 600, type: 'response' })
  } else if (name === 'box-busy') {
    vars().slot_sync = 'slot-1'
    st.multi_color_controller.task = { flow_id: 4, current_queue: ['BOX_HEAT', 'BOX_CUT', 'BOX_UNLOAD', 'BOX_LOAD', 'BOX_WIPE'], current_step: 'BOX_HEAT', is_paused: false }
    st.multi_color_controller.operation.active_slot = 'slot5'
    Object.assign(st.extruder, { temperature: 180, target: 230 })
    state.flow = { steps: ['BOX_HEAT', 'BOX_CUT', 'BOX_UNLOAD', 'BOX_LOAD', 'BOX_WIPE'], i: 0, t: 0, slot: 5 }
  } else if (name === 'box-failed') {
    vars().slot_sync = 'slot-1'
    st.multi_color_controller.task = { flow_id: 5, current_queue: ['BOX_UNLOAD', 'BOX_LOAD', 'BOX_WIPE'], current_step: 'BOX_UNLOAD', is_paused: true }
    st.multi_color_controller.operation.active_slot = 'slot5'
    st.multi_color_controller.box_warning_info = []
    pushConsole('!! Code:QDE_004_008; Message:Extruder unloading failure.')
    pushConsole('// Step BOX_UNLOAD failed. Pausing flow.')
  } else if (name === 'stranded') {
    vars().slot_sync = 'slot-1'
    st['filament_switch_sensor filament_switch_sensor'].filament_detected = false
  } else if (name === 'no-box') {
    vars().box_count = 0
  }
  if (name !== 'printing') {
    st.tltg_printer_warnings.warnings = [{ id: 'input_shaper_uncalibrated', level: 'warning', title: 'No input shaper calibration is saved.', message: 'Run SHAPER_CALIBRATE with the bed empty, then SAVE_CONFIG.' }]
  }
  seedTemps()
  broadcast('notify_klippy_ready', [])
}

// Twenty minutes of history, as Moonraker's temperature store would have.
function seedTemps() {
  for (const k of Object.keys(temps)) {
    const h = state.status[k]
    const goal = h.target > 0 ? h.target : h.temperature
    const start = k.includes('box') || k.includes('chamber') ? 26 : 30
    const t = []
    const g = []
    for (let i = 0; i < 1200; i++) {
      const rise = h.target > 0 ? Math.min(1, Math.max(0, (i - 150) / (k === 'extruder' ? 60 : 300))) : 1
      const base = h.target > 0 ? start + (goal - start) * (1 - Math.pow(1 - rise, 2)) : goal
      t.push(Math.round((base + Math.sin(i / 7 + k.length) * 0.4 + (Math.random() - 0.5) * 0.3) * 100) / 100)
      g.push(h.target > 0 && i >= 150 ? h.target : 0)
    }
    temps[k] = { temperatures: t, targets: g }
  }
}

function pushConsole(text) {
  state.console.push({ message: text, time: now(), type: 'response' })
  broadcast('notify_gcode_response', [text])
}

// ---------- history ----------
;(function seedHistory() {
  let id = 1000
  for (let d = 140; d >= 1; d--) {
    const n = (d * 7) % 5 === 0 ? 0 : (d * 13) % 3
    for (let k = 0; k < n; k++) {
      const f = FILES[(d + k) % FILES.length]
      const start = now() - d * 86400 + k * 7200
      const status = (d + k) % 23 === 7 ? 'error' : (d + k) % 17 === 3 ? 'cancelled' : 'completed'
      state.history.push({ job_id: String(id++).padStart(6, '0'), filename: f.path, status, start_time: start, end_time: start + f.est, print_duration: f.est, total_duration: f.est + 300, filament_used: 9000, exists: true })
    }
  }
  state.history.sort((a, b) => b.start_time - a.start_time)
})()

// ---------- websocket ----------
const clients = new Set()
const subs = new Map()
function broadcast(method, params) {
  const msg = JSON.stringify({ jsonrpc: '2.0', method, params })
  for (const ws of clients) ws.readyState === 1 && ws.send(msg)
}
function sendStatus(diff) {
  for (const ws of clients) {
    const want = subs.get(ws)
    if (!want) continue
    const out = {}
    for (const [k, v] of Object.entries(diff)) if (k in want) out[k] = v
    if (Object.keys(out).length && ws.readyState === 1) ws.send(JSON.stringify({ jsonrpc: '2.0', method: 'notify_status_update', params: [out, now()] }))
  }
}
function set(obj, fields) {
  state.status[obj] = { ...state.status[obj], ...fields }
  sendStatus({ [obj]: fields })
}

const temps = {}
for (const k of ['extruder', 'heater_bed', 'heater_generic chamber', 'heater_generic heater_box1', 'heater_generic heater_box2']) temps[k] = { temperatures: [], targets: [] }

async function gcode(script) {
  for (const line of script.split('\n')) {
    const [cmd, ...rest] = line.trim().split(/\s+/)
    const p = Object.fromEntries(rest.map((r) => r.split('=')).map(([k, ...v]) => [k.toUpperCase(), v.join('=')]))
    const C = cmd.toUpperCase()
    const words = Object.fromEntries(rest.filter((r) => /^[XYZEF]-?[\d.]+$/i.test(r)).map((r) => [r[0].toUpperCase(), Number(r.slice(1))]))
    if (C === 'SAVE_GCODE_STATE') state.saved = state.relative
    else if (C === 'RESTORE_GCODE_STATE') state.relative = state.saved ?? false
    else if (C === 'G91') state.relative = true
    else if (C === 'G90') state.relative = false
    else if (C === 'M83' || C === 'M82') {
      /* extrusion mode: nothing to model */
    } else if (C === 'G1' || C === 'G0') {
      const th = state.status.toolhead
      const pos = [...th.position]
      for (const [i, ax] of ['X', 'Y', 'Z'].entries()) {
        if (words[ax] == null) continue
        if (!th.homed_axes.includes(ax.toLowerCase())) throw rpcError(`Must home axis first: ${pos.slice(0, 3).map((v) => v.toFixed(3)).join(' ')} [0.000]`)
        pos[i] = state.relative ? pos[i] + words[ax] : words[ax]
        const max = ax === 'Z' ? 340 : 390
        if (pos[i] < 0 || pos[i] > max) throw rpcError(`Move out of range: ${pos.slice(0, 3).map((v) => v.toFixed(3)).join(' ')} [0.000]`)
      }
      if (words.E != null && !state.status.extruder.can_extrude) throw rpcError("Extrude below minimum temp. See the 'min_extrude_temp' config option for details")
      await new Promise((r) => setTimeout(r, 300))
      set('toolhead', { position: pos })
      set('gcode_move', { gcode_position: pos })
    } else if (C === 'VO_SCENARIO') applyScenario(p.NAME ?? 'idle')
    else if (C === 'VO_UPDATES') applyUpdates(p.NAME ?? 'none')
    else if (C === 'SAVE_VARIABLE') {
      // Roughly Klipper: outer double quotes are stripped, then the value is a Python literal.
      let v = p.VALUE
      if (/^".*"$/.test(v)) v = v.slice(1, -1)
      try {
        v = JSON.parse(v.replace(/'/g, '"'))
      } catch {}
      if (typeof v === 'string') v = v.replace(/^["']+|["']+$/g, '')
      set('save_variables', { variables: { ...vars(), [p.VARIABLE]: v } })
      if (p.VARIABLE.startsWith('filament_slot') || p.VARIABLE.startsWith('color_slot')) refreshBoxInfo()
    } else if (C === 'SET_HEATER_TEMPERATURE') {
      const obj = p.HEATER === 'extruder' || p.HEATER === 'heater_bed' ? p.HEATER : `heater_generic ${p.HEATER}`
      set(obj, { target: Number(p.TARGET) })
    } else if (C === 'M220') set('gcode_move', { speed_factor: Number(p.S ?? rest[0]?.slice(1)) / 100 })
    else if (C === 'M221') set('gcode_move', { extrude_factor: Number(p.S ?? rest[0]?.slice(1)) / 100 })
    else if (C === 'SET_GCODE_OFFSET') {
      const z = state.status.gcode_move.homing_origin[2] + Number(p.Z_ADJUST ?? 0)
      set('gcode_move', { homing_origin: [0, 0, Math.round(z * 1000) / 1000, 0] })
    } else if (C === 'SET_FAN_SPEED') set(`fan_generic ${p.FAN}`, { speed: Number(p.SPEED) })
    else if (C === 'SET_PIN') set(`output_pin ${p.PIN}`, { value: Number(p.VALUE) })
    else if (C === 'G28') {
      const axes = rest.map((r) => r.toLowerCase()).filter((r) => 'xyz'.includes(r))
      const want = axes.length ? axes : ['x', 'y', 'z']
      await new Promise((r) => setTimeout(r, 1500))
      const homed = [...new Set([...state.status.toolhead.homed_axes, ...want])].sort().join('')
      const pos = [...state.status.toolhead.position]
      if (want.includes('x')) pos[0] = 390
      if (want.includes('y')) pos[1] = 390
      if (want.includes('z')) pos[2] = 0
      set('toolhead', { homed_axes: homed, position: pos })
      set('gcode_move', { gcode_position: pos })
    } else if (C === 'SDCARD_RESET_FILE') {
      set('print_stats', { filename: '', state: 'standby', print_duration: 0, total_duration: 0, message: '' })
      set('virtual_sdcard', { progress: 0, is_active: false })
      set('display_status', { progress: 0 })
    } else if (C === 'LOAD_FILAMENT_CONTROL') startFlow(Number(p.SLOT), ['BOX_HEAT', 'BOX_CUT', 'BOX_UNLOAD', 'BOX_LOAD', 'BOX_WIPE'])
    else if (C === 'UNLOAD_FILAMENT_CONTROL') startFlow(Number(p.SLOT), ['BOX_CUT', 'BOX_UNLOAD'], 'unload')
    else if (C === 'EJECT_FILAMENT_CONTROL') startFlow(Number(p.SLOT), ['BOX_CUT', 'BOX_UNLOAD', 'BOX_EJECT'], 'unload')
    else if (C === 'FLOW_RETRY') {
      const t = state.status.multi_color_controller.task
      set('multi_color_controller', { task: { ...t, is_paused: false } })
      state.flow = { steps: t.current_queue, i: Math.max(0, t.current_queue.indexOf(t.current_step)), t: 0, slot: Number(String(state.status.multi_color_controller.operation.active_slot).replace('slot', '')) }
    } else if (C === 'FLOW_STOP') {
      state.flow = null
      set('multi_color_controller', { task: { current_queue: [], current_step: '', is_paused: false } })
    } else if (C === 'READ_RFID') {
      set(`box_stepper slot${p.SLOT}`, { rfid_state: 1 })
      setTimeout(() => set(`box_stepper slot${p.SLOT}`, { rfid_state: 0 }), 3000)
    } else if (C === 'TLTG_BOX_MOTION') {
      const m = state.status.tltg_box_feed_motion
      if (p.RESET) set('tltg_box_feed_motion', { settings: Object.fromEntries(Object.keys(m.settings).map((k) => [k, null])) })
      else {
        const s = { ...m.settings }
        for (const [k, v] of Object.entries(p)) s[k.toLowerCase()] = v === 'vendor' || v === 'default' ? null : Number(v)
        set('tltg_box_feed_motion', { settings: s })
      }
      pushConsole('// Box motion settings saved.')
    } else if (C === 'TLTG_CUT_FILAMENT') {
      await new Promise((r) => setTimeout(r, 2000))
      pushConsole('// Filament cut.')
    } else if (C === 'TLTG_BOX_SLOT_JOG') pushConsole(`// Moved slot ${p.SLOT} ${p.DISTANCE} mm.`)
    else if (C === 'M84') set('toolhead', { homed_axes: '' })
    else if (C === 'M107' || C === 'M106') {
      /* accepted */
    } else if (/^T\d+$/.test(C)) throw rpcError(`${C} not run: QIDI's tool change runs only during a print. To load a slot, use Load on the QIDI Box card or LOAD_FILAMENT_CONTROL SLOT=<n>.`)
    else if (C) pushConsole(`// fake: accepted ${C}`)
  }
  return 'ok'
}

function refreshBoxInfo() {
  const v = vars()
  const boxes = state.status.box_extras.boxes.map((b, bi) => ({
    filament_info: b.filament_info.map((info, i) => {
      const slot = bi * 4 + i
      const id = v[`filament_slot${slot}`]
      const color = v[`color_slot${slot}`]
      const key = id ? Object.keys(FILAMENTS).find((k) => FILAMENTS[k].filament_id === id) : null
      const base = key ? fi(i, key, info.color_rgb_str[0]) : info
      return { ...base, color_rgb_str: Array.isArray(color) ? color : base.color_rgb_str }
    }),
  }))
  set('box_extras', { boxes })
}

function startFlow(slot, steps, kind = 'load') {
  if (state.flow || state.status.print_stats.state === 'printing') throw rpcError('Box is busy')
  state.flow = { steps, i: 0, t: 0, slot, kind }
  set('save_variables', { variables: { ...vars(), slot_sync: 'slot-1' } })
  set('multi_color_controller', { task: { flow_id: Date.now() % 1000, current_queue: steps, current_step: steps[0], is_paused: false }, operation: { active_slot: `slot${slot}` } })
  if (steps[0] === 'BOX_HEAT') set('extruder', { target: 230 })
}

function rpcError(message) {
  const e = new Error(message)
  e.code = 400
  return e
}

const handlers = {
  'server.connection.identify': () => ({ connection_id: 1 }),
  'server.info': () => ({ klippy_connected: true, klippy_state: state.klippy, components: ['history', 'webcam', 'file_manager'], moonraker_version: 'fake' }),
  'printer.info': () => ({ state: state.klippy, state_message: state.klippy === 'ready' ? 'Printer is ready' : 'Klipper is restarting', hostname: 'fake-max4', software_version: 'v0.12-fake' }),
  'printer.objects.list': () => ({ objects: [...Object.keys(state.status), 'gcode', 'configfile'] }),
  'printer.objects.subscribe': (p, ws) => {
    subs.set(ws, p.objects)
    return { eventtime: now(), status: Object.fromEntries(Object.keys(p.objects).filter((k) => state.status[k]).map((k) => [k, state.status[k]])) }
  },
  'printer.objects.query': (p) => {
    const out = {}
    for (const k of Object.keys(p.objects)) {
      if (k === 'gcode') out.gcode = { commands: Object.fromEntries(['G28', 'M84', 'SET_HEATER_TEMPERATURE', 'SAVE_VARIABLE', 'TLTG_CUT_FILAMENT', 'TLTG_BOX_SLOT_JOG', 'TLTG_BOX_MOTION', 'TLTG_RESET_TOOL_MAPPINGS', 'LOAD_FILAMENT_CONTROL', 'FLOW_RETRY', 'FLOW_STOP', 'PRINT_START', 'M600', 'CLEAR_NOZZLE', 'BED_MESH_CALIBRATE', 'SHAPER_CALIBRATE'].map((c) => [c, { help: '' }])) }
      else if (k === 'configfile')
        out.configfile = {
          settings: {
            extruder: { min_temp: 0, max_temp: 350 },
            heater_bed: { min_temp: 0, max_temp: 120 },
            'heater_generic chamber': { min_temp: 0, max_temp: 70 },
            'heater_generic heater_box1': { min_temp: 0, max_temp: 70, target_max_temp: 65 },
            'heater_generic heater_box2': { min_temp: 0, max_temp: 70, target_max_temp: 65 },
          },
        }
      else if (state.status[k]) out[k] = state.status[k]
    }
    return { eventtime: now(), status: out }
  },
  'server.temperature_store': () => temps,
  'server.gcode_store': () => ({ gcode_store: state.console }),
  'server.files.list': () => fileEntries,
  'server.files.metadata': (p) => {
    const m = metadata(p.filename)
    if (!m) throw rpcError(`Metadata not available for <${p.filename}>`)
    return m
  },
  'server.files.metascan': (p) => metadata(p.filename),
  'server.files.delete_file': (p) => {
    const path = p.path.replace(/^gcodes\//, '')
    const i = fileEntries.findIndex((f) => f.path === path)
    if (i >= 0) fileEntries.splice(i, 1)
    broadcast('notify_filelist_changed', [{ action: 'delete_file', item: { path, root: 'gcodes' } }])
    return { item: { path, root: 'gcodes' }, action: 'delete_file' }
  },
  'server.history.list': (p) => ({ count: state.history.length, jobs: state.history.slice(0, p.limit ?? 50) }),
  'server.history.totals': () => ({
    job_totals: {
      total_jobs: state.history.length,
      total_time: state.history.reduce((a, j) => a + j.total_duration, 0),
      total_print_time: state.history.reduce((a, j) => a + j.print_duration, 0),
      total_filament_used: state.history.reduce((a, j) => a + j.filament_used, 0),
      longest_job: 16800,
      longest_print: 16500,
    },
  }),
  'server.webcams.list': () => ({ webcams: [{ name: 'Chamber', enabled: true, service: 'mjpegstreamer', stream_url: '/webcam/?action=stream', snapshot_url: '/webcam/?action=snapshot', flip_horizontal: false, flip_vertical: false, rotation: 0 }] }),
  'printer.gcode.script': (p) => gcode(p.script),
  'printer.gcode.help': () => ({
    G28: 'Home axes', M84: 'Disable motors', SET_HEATER_TEMPERATURE: 'Sets a heater temperature', SAVE_VARIABLE: 'Save arbitrary variables to disk',
    TLTG_BOX_MOTION: 'Show or set QIDI Box load and unload motion', TLTG_CUT_FILAMENT: 'Cut the filament at the toolhead without heating',
    TLTG_BOX_SLOT_JOG: "Move a Box slot's filament", LOAD_FILAMENT_CONTROL: 'Load a Box slot', PRINT_START: 'Start a print', M600: 'Filament change',
    CLEAR_NOZZLE: 'Wipe the nozzle', BED_MESH_CALIBRATE: 'Probe the bed mesh', SHAPER_CALIBRATE: 'Measure resonances and pick input shaping',
    TLTG_RESET_TOOL_MAPPINGS: 'Restore T0 → 1A, T1 → 1B, …',
  }),
  'machine.proc_stats': () => procStats(true),
  'server.files.get_directory': () => ({ dirs: [], files: [], disk_usage: { total: 7.2e9, used: 3.1e9, free: 4.1e9 }, root_info: { name: 'gcodes', permissions: 'rw' } }),
  'printer.print.start': (p) => {
    if (state.status.print_stats.state === 'printing') throw rpcError('Printer is busy')
    set('print_stats', { filename: p.filename, state: 'printing', print_duration: 0, total_duration: 0, message: '', info: { current_layer: 1, total_layer: 240 } })
    set('virtual_sdcard', { progress: 0, is_active: true })
    set('display_status', { progress: 0 })
    set('idle_timeout', { state: 'Printing' })
    broadcast('notify_history_changed', [{ action: 'added' }])
    return 'ok'
  },
  'printer.print.pause': () => (set('print_stats', { state: 'paused' }), set('pause_resume', { is_paused: true }), 'ok'),
  'printer.print.resume': () => (set('print_stats', { state: 'printing' }), set('pause_resume', { is_paused: false }), 'ok'),
  'printer.print.cancel': () => (set('print_stats', { state: 'cancelled' }), set('virtual_sdcard', { is_active: false }), set('idle_timeout', { state: 'Ready' }), 'ok'),
  'printer.emergency_stop': () => {
    pushConsole('!! Shutdown due to M112 command')
    return 'ok'
  },
  'printer.restart': () => 'ok',
  'printer.firmware_restart': () => 'ok',
}

let uptime = 86400 * 3 + 5000
function procStats(initial) {
  const cpu = 18 + Math.random() * 14
  const mr = { time: now(), cpu_usage: 3 + Math.random() * 3, memory: 52000, mem_units: 'kB' }
  return {
    moonraker_stats: initial ? [mr] : mr,
    cpu_temp: 52 + Math.random() * 3,
    system_cpu_usage: { cpu },
    system_memory: { total: 1015000, available: 420000 + Math.random() * 20000, used: 595000 },
    system_uptime: uptime,
    websocket_connections: clients.size,
  }
}

// ---------- simulation tick ----------
setInterval(() => {
  const st = state.status
  if (!st.print_stats) return
  // temperatures drift toward targets
  for (const k of Object.keys(temps)) {
    const h = st[k]
    const goal = h.target > 0 ? h.target : k.includes('box') ? 26 : 28
    const t = h.temperature + (goal - h.temperature) * 0.08 + (Math.random() - 0.5) * 0.4
    set(k, { temperature: Math.round(t * 100) / 100 })
    temps[k].temperatures = [...temps[k].temperatures.slice(-1199), t]
    temps[k].targets = [...temps[k].targets.slice(-1199), h.target]
  }
  set('extruder', { can_extrude: st.extruder.temperature >= 135 })
  for (const b of [1, 2]) set(`aht20_f heater_box${b}`, { temperature: st[`heater_generic heater_box${b}`].temperature })
  uptime++
  broadcast('notify_proc_stat_update', [procStats(false)])
  set('tltg_klipper_health', { main_thread_load: 0.18 + Math.random() * 0.12 })
  const printing = st.print_stats.state === 'printing'
  set('motion_report', { live_velocity: printing ? 180 + Math.random() * 120 : 0, live_extruder_velocity: printing ? 2 + Math.random() * 3 : 0 })
  // printing
  if (printing) {
    const p = Math.min(1, st.virtual_sdcard.progress + 0.0004)
    set('virtual_sdcard', { progress: p })
    set('display_status', { progress: p })
    const layer = Math.round(p * 240)
    if (layer !== st.print_stats.info.current_layer && layer % 5 === 0) pushConsole(`// Layer ${layer} of 240`)
    set('print_stats', { print_duration: st.print_stats.print_duration + 1, total_duration: st.print_stats.total_duration + 1, filament_used: st.print_stats.filament_used + 3, info: { current_layer: layer, total_layer: 240 } })
    set('gcode_move', { gcode_position: [120, 140, Math.round(layer * 0.2 * 100) / 100, 0] })
    if (p >= 1) set('print_stats', { state: 'complete' })
  }
  // Box flow
  const f = state.flow
  if (f && !st.multi_color_controller.task.is_paused) {
    f.t++
    if (f.t >= 3) {
      f.t = 0
      f.i++
      if (f.i >= f.steps.length) {
        state.flow = null
        const loadedNow = f.kind === 'load'
        set('save_variables', { variables: { ...vars(), slot_sync: loadedNow ? `slot${f.slot}` : 'slot-1', last_load_slot: `slot${f.slot}`, tltg_box_hub_slot: `slot${f.slot}` } })
        set('multi_color_controller', { task: { current_queue: [], current_step: '', is_paused: false }, box: { last_load_slot: `slot${f.slot}`, b_endstop_state: loadedNow ? 0 : 1 } })
        set('filament_switch_sensor filament_switch_sensor', { filament_detected: loadedNow })
        set('extruder', { target: 0 })
        pushConsole(`// ${loadedNow ? 'Loaded' : 'Unloaded'} slot${f.slot}`)
      } else set('multi_color_controller', { task: { current_step: f.steps[f.i] } })
    }
  }
}, 1000)

// ---------- Valve Oil status service (/tltg-status/api, installer/system/tltg_status.py) ----------
// Pick a starting state with --updates, or switch at runtime with `VO_UPDATES NAME=<scenario>`. Update now runs a
// simulated update; with QIDI or board firmware waiting it takes Moonraker and this API away for a while, as the real
// one restarts them.
const UPDATE_SCENARIOS = ['none', 'release-off', 'firmware-printing', 'boards', 'running', 'failed', 'interrupted', 'refused', 'firmware-failed', 'firmware-rescue', 'closed-loop', 'thr-waiting', 'reinstall', 'absent']
const ISSUE_URL =
  'https://github.com/thelegendtubaguy/valve-oil/issues/new?template=printer-support.yml&title=Box+1+bootloader+not+supported&printer=QIDI+Max+4&firmware=01.01.07.01&board=box1'
const iso = (secondsAgo = 0) => new Date(Date.now() - secondsAgo * 1000).toISOString().replace(/\.\d+Z$/, 'Z')
const board = (id, state, firmware, build, extra = {}) => ({ id, state, name: { thr: 'THR', mcu: 'Mainboard', closed_loop: 'Closed-loop drivers' }[id] ?? `Box ${id.slice(3)}`, firmware, build, pinned: firmware, bootloader: null, issue_url: null, report: null, detail: null, checked_at: iso(3600), waiting_for_firmware_update: false, ...extra })
// installer/runtime/valve_oil_status.py BLOCKERS and installer/system/tltg_status.py UPDATE_NOW_BLOCKERS.
const BLOCKERS = {
  printing: 'a print is running or paused',
  heaters_on: 'a heater is set to a temperature',
  printer_state_unknown: "Klipper didn't report whether the printer is idle",
  last_run_failed: "the last attempt didn't finish",
  run_in_progress: 'another update is still running',
  installer_busy: 'another installer run is in progress',
  // As the service names the rescue to run, for the mainboard write the firmware-rescue scenario leaves unverified.
  recovery_required: "a mainboard firmware write wasn't verified; run ./mainboard-firmware.sh rescue first",
}
const WAITING_FOR_FIRMWARE = { waiting_for_firmware_update: true, detail: 'will be updated after the QIDI firmware update to 01.01.07.01' }
// The update check's timer: hourly, a few minutes past the hour.
const nextCheck = () => {
  const next = new Date()
  next.setMinutes(5, 0, 0)
  if (next.getTime() <= Date.now()) next.setHours(next.getHours() + 1)
  return next.toISOString().replace(/\.\d+Z$/, 'Z')
}
const CLOSED_LOOP_DETAIL =
  "X driver reports firmware 03.01.01, hardware 9.2, state idle. Valve Oil sent no firmware to the X and Y closed-loop motor drivers, so they keep the firmware they have; QIDI firmware 01.01.07.01 carries driver firmware 03.01.02. Update the motor drivers from the touchscreen's firmware page."
// A firmware update kept after it failed part way, which Update now or ./install.sh continues.
const FIRMWARE_FAILED = {
  firmware_update: true,
  can_continue: true,
  kind: 'automatic',
  state: 'failed',
  step: 'soc',
  step_label: 'Installing QIDI firmware 01.01.07.01',
  resumed: false,
  message: "Installing QIDI firmware 01.01.07.01 failed (dpkg returned 1: dpkg: error processing archive /home/qidi/update/QIDI_Max4/soc.deb (--install): unexpected end of file in archive). Klipper and Moonraker were started again; run ./install.sh to try again.",
}
const FIRMWARE_RESCUE = {
  firmware_update: true,
  // Kept and failed, so Update now continues it, once the rescue has run (update_now says so).
  can_continue: true,
  kind: 'automatic',
  state: 'failed',
  step: 'boards',
  step_label: 'Updating the board firmware',
  resumed: true,
  message: "A board firmware write could not be verified (mainboard: no answer from the bootloader after writing 01.01.07.01-tltg.2). Don't turn the printer off; run ./mainboard-firmware.sh rescue, then run ./install.sh to finish the update.",
}
let vo = null
let voGeneration = 0
let outage = false
state.klippy = 'ready'

function applyUpdates(name) {
  if (!UPDATE_SCENARIOS.includes(name)) throw rpcError(`Unknown updates scenario ${name}: use ${UPDATE_SCENARIOS.join(', ')}`)
  voGeneration++
  outage = false
  vo = {
    scenario: name,
    absent: name === 'absent',
    auto: true,
    release: '26.11.11.3',
    firmware: '01.01.07.01',
    target: '01.01.07.01',
    check: { at: iso(5400), result: 'ok', message: '' },
    pending: [],
    boards: [board('thr', 'current', '1.0.4', 'patched'), board('mcu', 'current', '1.0.6', 'patched'), board('box1', 'current', '1.0.3', 'patched'), board('box2', 'current', '1.0.3', 'patched')],
    run: null,
    job: null,
    failNext: false,
    // Update now's next request is refused: the update check holds the installer lock, with a heater on, until this time.
    refuseNext: false,
    installerBusyUntil: 0,
    recovery: false,
    continueFirmware: false,
  }
  const release = { kind: 'release', version: '26.11.12.1', current: '26.11.11.3', board: null, reinstall: false }
  const firmware = { kind: 'firmware', version: '01.01.07.01', current: '01.01.06.02', board: null, reinstall: false }
  const thr = { kind: 'board', version: '1.0.4', current: '1.0.2', board: 'thr', reinstall: false }
  if (name === 'release-off') {
    vo.auto = false
    vo.pending = [release]
  } else if (name === 'firmware-printing' || name === 'running' || name === 'interrupted' || name === 'failed') {
    vo.firmware = '01.01.06.02'
    vo.pending = [firmware, thr, release]
    vo.boards[0] = board('thr', 'needs_image', '1.0.2', 'stock', { pinned: '1.0.4' })
    if (name === 'firmware-printing' && state.status.print_stats?.state !== 'printing') applyScenario('printing')
  } else if (name === 'boards') {
    vo.boards[1] = board('mcu', 'unknown', null, null, { detail: 'Klipper was not ready, so the board was not read.' })
    vo.boards[2] = board('box1', 'unrecognized_bootloader', '1.0.2', 'stock', { issue_url: ISSUE_URL, detail: "QIDI firmware 01.01.07.01 carries Box firmware 1.0.3; update this Box from the touchscreen's firmware page." })
    vo.boards[3] = board('box2', 'held', '1.0.3', 'stock')
  } else if (name === 'refused') {
    // The status still says Update now can start; the hourly check takes the installer lock just before it's pressed.
    vo.pending = [release]
    vo.refuseNext = true
  } else if (name === 'firmware-failed' || name === 'firmware-rescue') {
    // Failed after QIDI's package went in (firmware-failed: while installing it), with the THR still to flash.
    vo.firmware = name === 'firmware-failed' ? '01.01.06.02' : '01.01.07.01'
    vo.pending = name === 'firmware-failed' ? [firmware] : []
    vo.boards[0] = board('thr', 'needs_image', '1.0.2', 'stock', { pinned: '1.0.4', ...(name === 'firmware-failed' ? WAITING_FOR_FIRMWARE : {}) })
    vo.boards[1] = name === 'firmware-rescue' ? board('mcu', 'unknown', null, null, { detail: 'Klipper does not report this board.' }) : vo.boards[1]
    vo.run = { id: `run-${name}`, ...(name === 'firmware-failed' ? FIRMWARE_FAILED : FIRMWARE_RESCUE), started_at: iso(2400), finished_at: iso(1500) }
    vo.recovery = name === 'firmware-rescue'
    vo.continueFirmware = true
  } else if (name === 'closed-loop') {
    // After a firmware update that left the Max 4's closed-loop drivers on their firmware.
    vo.boards.splice(2, 0, board('closed_loop', 'held', '03.01.01', null, { pinned: '03.01.02', detail: CLOSED_LOOP_DETAIL }))
    vo.run = { id: 'run-closed-loop', kind: 'update_now', state: 'succeeded', step: 'finish', step_label: 'Starting the printer again', started_at: iso(2400), finished_at: iso(900), message: `QIDI firmware 01.01.07.01 is installed, with Valve Oil for it. ${CLOSED_LOOP_DETAIL}`, resumed: false }
  } else if (name === 'thr-waiting') {
    // The THR's image needs the newer QIDI firmware: it's flashed in the firmware update, so it has no update of its own.
    vo.firmware = '01.01.06.02'
    vo.pending = [firmware]
    vo.boards[0] = board('thr', 'needs_image', '1.0.2', 'stock', { pinned: '1.0.4', ...WAITING_FOR_FIRMWARE })
    vo.boards[1] = board('mcu', 'needs_image', '1.0.5', 'stock', { pinned: '1.0.6', ...WAITING_FOR_FIRMWARE })
  } else if (name === 'reinstall') {
    // QIDI's firmware package emptied the config folder: the installed release has to be installed again.
    vo.pending = [{ kind: 'release', version: vo.release, current: vo.release, board: null, reinstall: true }]
  }
  if (name === 'failed') {
    const log = ['Checking that the printer is idle', 'Downloading QIDI firmware 01.01.07.01', "The download didn't match its checksum, so nothing on the printer was changed."]
    vo.job = { id: 'job-failed', state: 'failed', started_at: iso(1800), finished_at: iso(1740), log, message: log.at(-1) }
    vo.run = { id: 'run-failed', kind: 'update_now', state: 'failed', step: 'download', step_label: 'Downloading QIDI firmware 01.01.07.01', started_at: iso(1799), finished_at: iso(1740), message: "The download didn't match its checksum, so nothing on the printer was changed.", resumed: false }
    vo.check = { at: iso(1700), result: 'failed', message: "The release list couldn't be reached." }
    vo.failNext = true
  } else if (name === 'interrupted') {
    vo.run = { id: 'run-interrupted', firmware_update: true, kind: 'automatic', state: 'interrupted', step: 'install_firmware', step_label: "Installing QIDI's firmware package", started_at: iso(7200), finished_at: null, message: '', resumed: false }
  } else if (name === 'running') {
    if (voPrinterBusy()) applyScenario('idle')
    startUpdate()
  }
  voWarnings()
}

// The printer warnings Klipper's tltg_printer_warnings reports for the update state (installer/klipper/tltg/
// printer_warnings.py), beside the printer scenario's own.
function voWarnings() {
  const kept = (state.status.tltg_printer_warnings?.warnings ?? []).filter((w) => !w.id.startsWith('valve_oil_') && !w.id.startsWith('box_firmware_'))
  const warnings = []
  const run = vo.run
  const step = run?.step_label ? `It stopped at: ${run.step_label}. ` : ''
  if (run?.state === 'running') warnings.push({ id: 'valve_oil_update_running', level: 'warning', title: 'A Valve Oil update is running.', message: "Don't turn the printer off until it finishes." })
  else if (run?.state === 'interrupted') warnings.push({ id: 'valve_oil_update_interrupted', level: 'error', title: 'A Valve Oil update was interrupted.', message: `${step}Finish it with Update now or by running the Valve Oil installer.` })
  else if (run?.state === 'failed' && run.firmware_update)
    warnings.push({
      id: 'valve_oil_firmware_update_failed',
      level: 'error',
      title: "A QIDI firmware update didn't finish.",
      message: `${step}${run.message}${run.can_continue && !vo.recovery ? ' Continue it with Update now or by running the Valve Oil installer.' : ''}`,
    })
  const firmware = vo.pending.find((p) => p.kind === 'firmware')
  const release = vo.pending.find((p) => p.kind === 'release')
  const reasons = [...new Set(voStatus().pending.flatMap((item) => item.blocked_by.map((b) => b.message)))]
  const waiting = !vo.auto
    ? 'Automatic updates are off, so run the Valve Oil installer to install it.'
    : reasons.length
      ? `Automatic updates are on, but it hasn't been installed yet: ${reasons.join('; ')}.`
      : 'Automatic updates are on, so it will be installed at the next update check while the printer is idle.'
  const firmwareNote = vo.auto && !reasons.length ? " The printer can't be used while it installs." : ''
  if (release) warnings.push({ id: 'valve_oil_release_waiting', level: 'warning', title: release.reinstall ? 'Valve Oil needs to be installed again.' : `Valve Oil ${release.version} is waiting.`, message: waiting })
  if (firmware) warnings.push({ id: 'valve_oil_firmware_waiting', level: 'warning', title: `QIDI firmware ${firmware.version} is waiting.`, message: waiting + firmwareNote })
  if (vo.scenario === 'boards')
    warnings.push({
      id: 'box_firmware_unsupported_box1',
      level: 'warning',
      title: "Box 1 can't get the Valve Oil Box firmware yet.",
      message:
        "Its bootloader or firmware isn't supported yet, so it keeps the firmware it has. To get it supported, open an issue at https://github.com/thelegendtubaguy/valve-oil/issues/new?template=printer-support.yml and attach tltg_box_firmware_box1.json from Fluidd's Logs. QIDI firmware 01.01.07.01 carries Box firmware 1.0.3; update this Box from the touchscreen's firmware page.",
      url: ISSUE_URL,
    })
  set('tltg_printer_warnings', { warnings: [...kept, ...warnings] })
}

function voPrinterBusy() {
  return ['printing', 'paused'].includes(state.status.print_stats?.state)
}

function voStatus() {
  const live = []
  if (voPrinterBusy()) live.push('printing')
  else if (state.status.extruder?.target > 0 || state.status.heater_bed?.target > 0 || Date.now() < vo.installerBusyUntil) live.push('heaters_on')
  const blockers = [...live]
  if (vo.recovery) blockers.push('recovery_required')
  if (vo.run?.state === 'failed') blockers.push('last_run_failed')
  const running = vo.run?.state === 'running' || vo.job?.state === 'running'
  // tltg_status.py _update_now: every reason POST api/update-now would refuse, read live.
  const refusal = [running ? 'run_in_progress' : Date.now() < vo.installerBusyUntil ? 'installer_busy' : null, ...live, vo.recovery ? 'recovery_required' : null].filter(Boolean)
  return {
    schema: 1,
    updated_at: iso(60),
    release: { installed: vo.release },
    printer: { id: 'max4', name: 'QIDI Max 4', firmware: vo.firmware, target_firmware: vo.target },
    auto_update: { enabled: vo.auto, scheduled: true },
    check: { ...vo.check, next_at: nextCheck() },
    pending: vo.pending.map((item) => ({ ...item, blocked_by: vo.auto ? blockers.map((code) => ({ code, message: BLOCKERS[code] })) : [] })),
    boards: vo.boards,
    run: vo.run && { firmware_update: false, can_continue: false, ...vo.run, job: undefined },
    // The job names its run once the run has begun and is the latest.
    job: vo.job && { ...vo.job, log: vo.job.log.slice(-200), message: vo.job.log.findLast((l) => l.trim()) ?? '', run_id: vo.run?.job === vo.job.id ? vo.run.id : null },
    busy: running,
    printer_busy: voPrinterBusy(),
    update_now: { allowed: refusal.length === 0, blocked_by: refusal.map((code) => ({ code, message: BLOCKERS[code] })) },
  }
}

// Klipper restarts while Valve Oil installs; Moonraker too while QIDI's package installs.
function klipperRestart(seconds) {
  // Stopped, as the installer stops it, then starting for the last second.
  state.klippy = 'disconnected'
  broadcast('notify_klippy_disconnected', [])
  setTimeout(() => (state.klippy = 'startup'), (seconds - 1) * 1000)
  setTimeout(() => {
    state.klippy = 'ready'
    broadcast('notify_klippy_ready', [])
  }, seconds * 1000)
}
function moonrakerOutage(seconds) {
  outage = true
  for (const ws of clients) ws.terminate()
  setTimeout(() => {
    outage = false
    klipperRestart(4)
  }, seconds * 1000)
}

function startUpdate() {
  const generation = voGeneration
  const firmware = vo.pending.find((p) => p.kind === 'firmware') ?? (vo.continueFirmware ? { version: vo.target } : null)
  const boards = vo.pending.filter((p) => p.kind === 'board')
  const release = vo.pending.find((p) => p.kind === 'release')
  const steps = []
  if (vo.continueFirmware) {
    // Update now continues the kept firmware update from the step it failed at.
    const from = vo.run.step
    if (from === 'soc') steps.push(['stop_services', 'Stopping Klipper, Moonraker, and the touchscreen', 3], ['soc', `Installing QIDI firmware ${vo.target}`, 18, () => moonrakerOutage(15)], ['update_folder', "Updating QIDI's update folder", 2])
    steps.push(['boards', 'Updating the board firmware', 6, () => klipperRestart(5)], ['install', `Installing Valve Oil for QIDI firmware ${vo.target}`, 6, () => klipperRestart(5)], ['finish', 'Starting the printer again', 3])
    vo.continueFirmware = false
  } else {
    steps.push(['check', 'Checking that the printer is idle', 3])
    if (firmware) steps.push(['download_firmware', `Downloading QIDI firmware ${firmware.version}`, 5], ['install_firmware', "Installing QIDI's firmware package", 18, () => moonrakerOutage(15)])
    for (const b of boards) steps.push([`flash_${b.board}`, `Updating the ${vo.boards.find((x) => x.id === b.board)?.name ?? b.board} firmware`, 6, () => klipperRestart(5)])
    if (release || !firmware) steps.push(['download', 'Downloading the latest Valve Oil release', 3], ['install', 'Installing the latest Valve Oil release', 6, () => klipperRestart(5)])
  }
  // The failed scenario's next update fails once, while downloading.
  const failAt = vo.failNext ? 2 : -1
  vo.failNext = false
  vo.job = { id: `job-${Date.now()}`, state: 'running', started_at: iso(), finished_at: null, log: ['Starting Valve Oil update now…'] }
  let i = -1
  const next = () => {
    if (generation !== voGeneration) return
    i++
    const log = vo.job.log
    if (i === failAt) {
      const message = "The download didn't match its checksum, so nothing on the printer was changed."
      log.push(message)
      vo.run = { ...vo.run, state: 'failed', finished_at: iso(), message }
      setTimeout(() => generation === voGeneration && Object.assign(vo.job, { state: 'failed', finished_at: iso() }), 1000)
      return
    }
    if (i >= steps.length) {
      log.push('Everything is up to date.')
      vo.release = release?.version ?? vo.release
      vo.firmware = firmware?.version ?? vo.firmware
      vo.boards = vo.boards.map((b) => (b.state === 'needs_image' || (b.state === 'unknown' && vo.recovery) ? { ...b, state: 'current', firmware: b.pinned ?? b.firmware, build: 'patched', detail: null } : b))
      vo.recovery = false
      vo.pending = []
      vo.run = { ...vo.run, state: 'succeeded', step: '', step_label: '', finished_at: iso(), message: firmware ? 'QIDI firmware and Valve Oil are up to date.' : 'The latest Valve Oil release is installed.' }
      voWarnings()
      setTimeout(() => generation === voGeneration && Object.assign(vo.job, { state: 'succeeded', finished_at: iso() }), 1500)
      return
    }
    if (i === 0) voWarnings()
    const [step, label, seconds, effect] = steps[i]
    log.push(`${label}…`)
    for (let n = 1; n < seconds; n += 2) setTimeout(() => generation === voGeneration && vo.job.log.push(`  ${label.toLowerCase()}: ${Math.round((n / seconds) * 100)}%`), n * 1000)
    vo.run = { ...vo.run, step, step_label: label }
    effect?.()
    setTimeout(next, seconds * 1000)
  }
  // The run records itself a moment after Update now's job starts, naming the job.
  const resumed = vo.run?.state === 'failed' && vo.run.can_continue === true
  setTimeout(() => {
    if (generation !== voGeneration) return
    vo.run = { id: `run-${Date.now()}`, kind: 'update_now', state: 'running', step: '', step_label: '', started_at: iso(), finished_at: null, message: '', resumed, job: vo.job.id, firmware_update: resumed, can_continue: false }
    next()
  }, 1200)
  return vo.job
}

async function statusApi(req, res, path) {
  const send = (code, body) => res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }).end(JSON.stringify(body))
  // nginx answers for the service while Moonraker (its authorizer) or the service is down.
  if (outage) return res.writeHead(502, { 'Content-Type': 'text/html' }).end('<html><body>502 Bad Gateway</body></html>')
  if (vo.absent) return res.writeHead(404, { 'Content-Type': 'text/html' }).end('<html><body>404 Not Found</body></html>')
  const route = path.slice('/tltg-status/api/'.length)
  if (req.method === 'GET') return route === 'status' ? send(200, voStatus()) : send(404, { error: 'Not found.' })
  if (req.method !== 'POST') return send(405, { error: 'Use GET.' })
  if (req.headers['x-valve-oil'] !== 'status') return send(403, { error: 'Missing X-Valve-Oil header.' })
  let raw = ''
  for await (const chunk of req) raw += chunk
  let body
  try {
    body = raw ? JSON.parse(raw) : {}
  } catch {
    return send(400, { error: 'Expected a JSON request.' })
  }
  await new Promise((r) => setTimeout(r, 400))
  if (route === 'auto-update') {
    if (typeof body.enabled !== 'boolean') return send(400, { error: 'Expected {"enabled": true} or {"enabled": false}.' })
    vo.auto = body.enabled
    return send(200, voStatus())
  }
  if (route === 'update-now') {
    if (vo.refuseNext) {
      // Meanwhile the hourly check took the installer lock, and a heater was turned on; both clear after 20 seconds.
      vo.refuseNext = false
      vo.installerBusyUntil = Date.now() + 20000
    }
    const blocked = voStatus().update_now.blocked_by
    if (blocked.length) return send(409, { error: `Update now can't start: ${blocked.map((b) => b.message).join('; ')}.`, blocked_by: blocked })
    const job = startUpdate()
    return send(202, { job: { ...job, log: [...job.log], message: job.log.at(-1), run_id: null } })
  }
  return send(404, { error: 'Not found.' })
}

// ---------- http ----------
const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.json': 'application/json', '.png': 'image/png' }

const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x')
  const path = decodeURIComponent(url.pathname)
  res.setHeader('Access-Control-Allow-Origin', '*')
  try {
    if (path === '/' || path === '/valve-oil') return res.writeHead(302, { Location: '/valve-oil/' }).end()
    if (path.startsWith('/valve-oil/')) {
      const rel = normalize(path.slice('/valve-oil/'.length) || 'index.html').replace(/^(\.\.[/\\])+/, '')
      const file = join(DIST, rel)
      await stat(file)
      res.writeHead(200, { 'Content-Type': MIME[extname(file)] ?? 'application/octet-stream', 'Cache-Control': 'no-store' })
      return res.end(await readFile(file))
    }
    if (path.startsWith('/tltg-status/')) return await statusApi(req, res, path)
    if (path.startsWith('/webcam/')) {
      res.writeHead(200, { 'Content-Type': 'image/svg+xml', 'Cache-Control': 'no-store' })
      return res.end(camSvg())
    }
    if (path.startsWith('/server/files/gcodes/') && path.includes('/.thumbs/')) {
      const name = path.split('/').pop().replace(/-\d+x\d+\.png$/, '')
      res.writeHead(200, { 'Content-Type': 'image/svg+xml' })
      return res.end(thumbSvg(name))
    }
    if (path === '/server/files/config/qidi_filament_config/filament-0.4.json') {
      res.writeHead(200, { 'Content-Type': 'application/json' })
      return res.end(JSON.stringify(FILAMENTS, null, 2))
    }
    if (path === '/server/files/upload' && req.method === 'POST') {
      let body = ''
      for await (const chunk of req) body += chunk.toString('latin1')
      const name = /filename="([^"]+)"/.exec(body)?.[1] ?? 'upload.gcode'
      fileEntries.unshift({ path: name, modified: now(), size: body.length, permissions: 'rw' })
      broadcast('notify_filelist_changed', [{ action: 'create_file', item: { path: name, root: 'gcodes' } }])
      res.writeHead(201, { 'Content-Type': 'application/json' })
      return res.end(JSON.stringify({ item: { path: name, root: 'gcodes' }, action: 'create_file' }))
    }
    res.writeHead(404).end('not found')
  } catch {
    res.writeHead(404).end('not found')
  }
})

const wss = new WebSocketServer({ server, path: '/websocket' })
wss.on('connection', (ws) => {
  // Moonraker is down during a simulated firmware update.
  if (outage) return ws.terminate()
  clients.add(ws)
  ws.on('close', () => (clients.delete(ws), subs.delete(ws)))
  ws.on('message', async (raw) => {
    let msg
    try {
      msg = JSON.parse(raw.toString())
    } catch {
      return
    }
    const h = handlers[msg.method]
    try {
      if (!h) throw rpcError(`Method not found: ${msg.method}`)
      const result = await h(msg.params ?? {}, ws)
      ws.send(JSON.stringify({ jsonrpc: '2.0', id: msg.id, result }))
    } catch (e) {
      ws.send(JSON.stringify({ jsonrpc: '2.0', id: msg.id, error: { code: e.code ?? 500, message: e.message } }))
    }
  })
})

applyScenario(scenario)
applyUpdates(args.updates ?? 'none')
server.listen(PORT, () => console.log(`fake Moonraker (${scenario}, updates ${vo.scenario}) on http://localhost:${PORT}/valve-oil/`))

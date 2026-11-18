// The Control screen's fake printer: fans, pins, and the case LEDs as a Max 4 has them, printer limits, pressure
// advance, Z offset, exclude-object polygons, pause at layer, macros with parameters, the runout sensor, and the
// manual probe and bed screws helpers. Commands it handles are taken before the core's G-code, line by line.

const SPEED_TO_RPM = { cooling_fan: 8200, auxiliary_cooling_fan: 5200, auxiliary_cooling_fan2: 5200, chamber_circulation_fan: 3100 }

// A 2x2 plate of parts, as a sliced file would define them with EXCLUDE_OBJECT_DEFINE.
function part(name, cx, cy, w, h) {
  return {
    name,
    center: [cx, cy],
    polygon: [
      [cx - w / 2, cy - h / 2],
      [cx + w / 2, cy - h / 2],
      [cx + w / 2, cy + h / 2],
      [cx - w / 2, cy + h / 2],
      [cx - w / 2, cy - h / 2],
    ],
  }
}

const MACROS = {
  VO_TEST_PROBE: { description: 'Test the nozzle probe at the bed center', gcode: '{% set samples = params.SAMPLES|default(5)|int %}\n{% set speed = params.SPEED|default(3)|float %}\n_VO_TEST_PROBE SAMPLES={samples}' },
  VO_PROBE_ACCURACY_CENTER: { description: 'Probe accuracy at the bed center', gcode: '{% set s = params.SAMPLES|default(10)|int %}\nPROBE_ACCURACY SAMPLES={s}' },
  VO_CORNER_BED_SCREW_CHECK: { description: 'Probe each bed screw and report the turns', gcode: 'G28' },
  VO_FILAMENT_SENSOR: { description: 'Turn the toolhead filament sensor on or off', gcode: '{% set enable = params.ENABLE|default(1)|int %}' },
  CLEAR_NOZZLE: { description: 'G-Code macro', gcode: 'G28\nG1 X100' },
  LED_ON: { description: 'G-Code macro', gcode: 'SET_PIN PIN=caselight VALUE=1' },
  LED_OFF: { description: 'G-Code macro', gcode: 'SET_PIN PIN=caselight VALUE=0' },
  M600: { description: 'Filament change', gcode: '{% set x = params.X|default(390)|float %}\n{% set e = params.E|default(-5)|float %}\nPAUSE' },
  M117: { description: 'G-Code macro', gcode: 'M118 {rawparams}' },
  g27: { description: 'Park the toolhead', gcode: '{% set p = params.P|default(0)|int %}' },
  PRINT_START: { description: 'G-Code macro', gcode: '{% set bed = params.BED|default(60)|float %}\n{% set hotend = params.HOTEND|default(220)|float %}\n{% set chamber = params.CHAMBER|default(0)|float %}\n{% set profile = params.PROFILE|default("textured PEI") %}' },
  SET_PRINT_STATS_INFO: { description: 'G-Code macro', gcode: 'SET_PRINT_STATS_INFO TOTAL_LAYER={params.TOTAL_LAYER}' },
  SET_PAUSE_NEXT_LAYER: { description: 'Enable a pause if the next layer is reached', gcode: '{% set enable = params.ENABLE|default(1)|int %}\n{% set macro = params.MACRO|default("PAUSE") %}' },
  SET_PAUSE_AT_LAYER: { description: 'Enable/disable a pause if a given layer number is reached', gcode: '{% set layer = params.LAYER|default(0)|int %}\n{% set macro = params.MACRO|default("PAUSE") %}' },
  M106: { description: 'Sets a fan speed. P0 part cooling, P2 auxiliary, P3 exhaust, P4 polar cooler.', gcode: '{% set p = params.P|default(0)|int %}' },
  _CLIENT_HELPER: { description: 'G-Code macro', gcode: '' },
}

export function install(fake) {
  const { set, pushConsole } = fake
  const st = () => fake.state.status

  fake.status((s, scenario) => {
    // QIDI printers have no [fan]; the part fan is fan_generic cooling_fan, driven through the M106 macro.
    const partSpeed = s.fan?.speed ?? 0
    delete s.fan
    const exhaust = s['fan_generic chamber_circulation_fan']?.speed ?? 0
    const fan = (name, speed) => ({ speed, rpm: speed * (SPEED_TO_RPM[name] ?? 4000) })
    Object.assign(s, {
      'fan_generic cooling_fan': fan('cooling_fan', partSpeed),
      'fan_generic auxiliary_cooling_fan': fan('auxiliary_cooling_fan', scenario === 'printing' ? 0.4 : 0),
      'fan_generic auxiliary_cooling_fan2': fan('auxiliary_cooling_fan2', scenario === 'printing' ? 0.4 : 0),
      'fan_generic chamber_circulation_fan': fan('chamber_circulation_fan', exhaust),
      'heater_fan hotend_fan': { speed: scenario === 'printing' ? 1 : 0, rpm: scenario === 'printing' ? 14200 : 0 },
      'controller_fan chamber_fan': { speed: 0, rpm: 0 },
      'controller_fan board_fan': { speed: 1, rpm: 6100 },
      'output_pin beeper': { value: 0 },
      'output_pin polar_cooler': { value: 0 },
      'neopixel RGB': { color_data: [0, 1, 2].map(() => [0, 0, 0, 0]) },
      'temperature_sensor Chamber_Thermal_Protection_Sensor': { temperature: 31.4, measured_min_temp: 24.1, measured_max_temp: 47.9 },
      'temperature_sensor Toolhead_MCU': { temperature: 44.2, measured_min_temp: 30.5, measured_max_temp: 51.2 },
      manual_probe: { is_active: false, z_position: null, z_position_lower: null, z_position_upper: null },
      bed_screws: { is_active: false, state: null, current_screw: 0, accepted_screws: 0 },
      stepper_enable: { steppers: { stepper_x: false, stepper_y: false, stepper_z: false, stepper_z1: false, extruder: false } },
      exclude_object: { objects: [], excluded_objects: [], current_object: null },
    })
    Object.assign(s.toolhead, { max_velocity: 565, max_accel: 20000, minimum_cruise_ratio: 0.5, square_corner_velocity: 8, axis_minimum: [-2, -5, -5, 0], axis_maximum: [392, 410, 342, 0], extruder: 'extruder' })
    Object.assign(s.extruder, { pressure_advance: 0.032, smooth_time: 0.03, can_extrude: s.extruder.temperature >= 135 })
    Object.assign(s.gcode_move, { absolute_coordinates: true, absolute_extrude: false })
    s.motion_report.live_position = [...s.toolhead.position]
    for (const [name] of Object.entries(MACROS)) s[`gcode_macro ${name}`] = {}
    s['gcode_macro SET_PRINT_STATS_INFO'] = { pause_next_layer: { enable: false, call: 'PAUSE' }, pause_at_layer: { enable: false, call: 'PAUSE', layer: 0 } }
    if (scenario === 'printing') {
      s.exclude_object = {
        objects: [part('planter_body_id_0_copy_0', 150, 160, 80, 80), part('planter_body_id_0_copy_1', 245, 160, 80, 80), part('saucer_id_1_copy_0', 150, 255, 70, 50), part('label_tag_id_2_copy_0', 245, 255, 40, 20)],
        excluded_objects: [],
        current_object: 'planter_body_id_0_copy_1',
      }
      s.stepper_enable.steppers = Object.fromEntries(Object.keys(s.stepper_enable.steppers).map((k) => [k, true]))
    }
  })

  fake.settings({
    printer: { kinematics: 'corexy', max_velocity: 565, max_accel: 20000, minimum_cruise_ratio: 0.5, square_corner_velocity: 8, max_z_velocity: 10, max_z_accel: 500 },
    extruder: { min_temp: 0, max_temp: 375, target_min_temp: 0, target_max_temp: 999, min_extrude_temp: 135, control: 'pid', nozzle_diameter: 0.4, filament_diameter: 1.75, max_extrude_only_velocity: 5000, max_extrude_only_distance: 1000, pressure_advance: 0.032, pressure_advance_smooth_time: 0.03 },
    heater_bed: { min_temp: -100, max_temp: 125, target_min_temp: 0, target_max_temp: 999, control: 'pid' },
    'heater_generic chamber': { min_temp: -100, max_temp: 68, target_min_temp: 0, target_max_temp: 999, control: 'pid' },
    'heater_generic heater_box1': { min_temp: -100, max_temp: 100, target_min_temp: 0, target_max_temp: 65, control: 'pid' },
    'heater_generic heater_box2': { min_temp: -100, max_temp: 100, target_min_temp: 0, target_max_temp: 65, control: 'pid' },
    'fan_generic cooling_fan': { max_power: 1, off_below: 0, kick_start_time: 0.1 },
    'fan_generic auxiliary_cooling_fan': { max_power: 1, off_below: 0 },
    'fan_generic auxiliary_cooling_fan2': { max_power: 1, off_below: 0 },
    'fan_generic chamber_circulation_fan': { max_power: 1, off_below: 0 },
    'heater_fan hotend_fan': { max_power: 1, heater: ['extruder'], heater_temp: 50 },
    'controller_fan chamber_fan': { max_power: 1, heater: ['chamber'] },
    'controller_fan board_fan': { max_power: 1 },
    'output_pin caselight': { pwm: false, value: 1, shutdown_value: 0 },
    'output_pin beeper': { pwm: false, value: 0, shutdown_value: 0 },
    'output_pin polar_cooler': { pwm: false, value: 0, shutdown_value: 0 },
    'neopixel rgb': { chain_count: 3, color_order: ['GRB'], initial_red: 0, initial_green: 0, initial_blue: 0 },
    'filament_switch_sensor filament_switch_sensor': { pause_on_runout: false, runout_gcode: '\nVO_FILAMENT_SENSOR_RUNOUT', insert_gcode: '\nVO_FILAMENT_SENSOR_INSERTED' },
    stepper_z: { position_min: -5, position_max: 342 },
    z_tilt: { retries: 5, retry_tolerance: 0.0075 },
    screws_tilt_adjust: { screw_thread: 'CW-M4' },
    bed_screws: { screw1: [30, 30], screw2: [360, 30], screw3: [360, 360], screw4: [30, 360] },
    force_move: { enable_force_move: true },
    ...Object.fromEntries(Object.entries(MACROS).map(([n, m]) => [`gcode_macro ${n.toLowerCase()}`, m])),
  })

  fake.commands({
    SET_VELOCITY_LIMIT: 'Set printer velocity limits',
    SET_PRESSURE_ADVANCE: 'Set pressure advance parameters',
    SET_GCODE_OFFSET: 'Set a virtual offset to g-code positions',
    Z_OFFSET_APPLY_PROBE: "Adjust the probe's z_offset",
    SET_FAN_SPEED: 'Sets the speed of a fan',
    SET_PIN: 'Set the value of an output pin',
    SET_LED: 'Set the color of an LED',
    PID_CALIBRATE: 'Run PID calibration test',
    TURN_OFF_HEATERS: 'Turn off all heaters',
    EXCLUDE_OBJECT: 'Cancel moves inside a specified objects',
    SET_FILAMENT_SENSOR: 'Sets the filament sensor on/off',
    MANUAL_PROBE: 'Start manual probe helper script',
    PROBE_CALIBRATE: 'Calibrate the probe z_offset',
    BED_SCREWS_ADJUST: 'Tool to help adjust bed leveling screws',
    SCREWS_TILT_CALCULATE: 'Tool to help adjust bed leveling screws by calculating the number of turns to level it.',
    Z_TILT_ADJUST: 'Adjust the Z tilt',
    SAVE_CONFIG: 'Overwrite config file and restart',
    M106: 'Sets a fan speed. P0 part cooling, P2 auxiliary, P3 exhaust, P4 polar cooler.',
    M107: 'G-Code macro',
    M220: 'Set speed factor override percentage',
    M221: 'Set extrude factor override percentage',
    SET_PRINT_STATS_INFO: 'Pass slicer info like layer act and total to klipper',
    SET_PAUSE_NEXT_LAYER: 'Enable a pause if the next layer is reached',
    SET_PAUSE_AT_LAYER: 'Enable/disable a pause if a given layer number is reached',
    VO_TEST_PROBE: 'Test the nozzle probe at the bed center',
    VO_PROBE_ACCURACY_CENTER: 'Probe accuracy at the bed center',
  })

  const setFan = (name, speed) => set(`fan_generic ${name}`, { speed, rpm: speed * (SPEED_TO_RPM[name] ?? 4000) })
  const allSteppers = (on) => set('stepper_enable', { steppers: Object.fromEntries(Object.keys(st().stepper_enable.steppers).map((k) => [k, on])) })
  let savedAbsolute = true

  // Returns true when the line is fully handled here; false passes it on to the core as well.
  async function handle(line) {
    const [cmd, ...rest] = line.trim().split(/\s+/)
    if (!cmd) return true
    const C = cmd.toUpperCase()
    const p = Object.fromEntries(rest.filter((r) => r.includes('=')).map((r) => r.split('=')).map(([k, ...v]) => [k.toUpperCase(), v.join('=').replace(/^"|"$/g, '')]))
    const word = (w) => {
      const r = rest.find((x) => x.toUpperCase().startsWith(w))
      return r == null ? undefined : Number(r.slice(1))
    }
    const s = st()
    switch (C) {
      case 'M106': {
        const P = word('P') ?? 0
        const speed = Math.min(1, Math.max(0, (word('S') ?? 255) / 255))
        if (P === 0) setFan('cooling_fan', speed)
        else if (P === 2) (setFan('auxiliary_cooling_fan', speed), setFan('auxiliary_cooling_fan2', speed))
        else if (P === 3) setFan('chamber_circulation_fan', speed)
        else if (P === 4) set('output_pin polar_cooler', { value: speed > 0 ? 1 : 0 })
        return true
      }
      case 'M107':
        setFan('cooling_fan', 0)
        return true
      case 'SET_FAN_SPEED':
        setFan(p.FAN, Number(p.SPEED))
        return true
      case 'SET_LED': {
        const cur = s[`neopixel ${p.LED}`]
        if (!cur) throw fake.rpcError(`Unknown LED '${p.LED}'`)
        const c = [Number(p.RED ?? 0), Number(p.GREEN ?? 0), Number(p.BLUE ?? 0), Number(p.WHITE ?? 0)]
        set(`neopixel ${p.LED}`, { color_data: cur.color_data.map(() => c) })
        return true
      }
      case 'SET_VELOCITY_LIMIT': {
        const t = {}
        if (p.VELOCITY) t.max_velocity = Number(p.VELOCITY)
        if (p.ACCEL) t.max_accel = Number(p.ACCEL)
        if (p.SQUARE_CORNER_VELOCITY) t.square_corner_velocity = Number(p.SQUARE_CORNER_VELOCITY)
        if (p.MINIMUM_CRUISE_RATIO) t.minimum_cruise_ratio = Number(p.MINIMUM_CRUISE_RATIO)
        set('toolhead', t)
        if (!rest.length) pushConsole(`// max_velocity: ${s.toolhead.max_velocity}\n// max_accel: ${s.toolhead.max_accel}\n// minimum_cruise_ratio: ${s.toolhead.minimum_cruise_ratio}\n// square_corner_velocity: ${s.toolhead.square_corner_velocity}`)
        return true
      }
      case 'SET_PRESSURE_ADVANCE': {
        const e = {}
        if (p.ADVANCE != null) e.pressure_advance = Number(p.ADVANCE)
        if (p.SMOOTH_TIME != null) e.smooth_time = Number(p.SMOOTH_TIME)
        set('extruder', e)
        return true
      }
      case 'SET_GCODE_OFFSET':
        if (p.Z == null) return false
        set('gcode_move', { homing_origin: [0, 0, Number(p.Z), 0] })
        return true
      case 'Z_OFFSET_APPLY_PROBE': {
        const z = s.gcode_move.homing_origin[2]
        pushConsole(`// probe: z_offset: ${(-0.05 - z).toFixed(3)}\n// The SAVE_CONFIG command will update the printer config file\n// with the above and restart the printer.`)
        return true
      }
      case 'TURN_OFF_HEATERS':
        for (const k of Object.keys(fake.temps)) set(k, { target: 0 })
        return true
      case 'PID_CALIBRATE': {
        const obj = p.HEATER === 'extruder' || p.HEATER === 'heater_bed' ? p.HEATER : `heater_generic ${p.HEATER}`
        if (!s[obj]) throw fake.rpcError(`Unknown heater '${p.HEATER}'`)
        set(obj, { target: Number(p.TARGET) })
        setTimeout(() => {
          set(obj, { target: 0 })
          pushConsole(`// PID parameters: pid_Kp=28.512 pid_Ki=3.401 pid_Kd=59.766\n// The SAVE_CONFIG command will update the printer config file\n// with these parameters and restart the printer.`)
        }, 8000)
        return true
      }
      case 'EXCLUDE_OBJECT': {
        const eo = s.exclude_object
        if (!p.NAME) {
          pushConsole(`// Excluded objects: ${eo.excluded_objects.join(' ') || 'none'}`)
          return true
        }
        if (!eo.objects.some((o) => o.name.toUpperCase() === p.NAME.toUpperCase())) throw fake.rpcError(`Unknown object '${p.NAME}'`)
        const name = eo.objects.find((o) => o.name.toUpperCase() === p.NAME.toUpperCase()).name
        set('exclude_object', { excluded_objects: [...new Set([...eo.excluded_objects, name])], current_object: eo.current_object === name ? null : eo.current_object })
        pushConsole(`// Excluding object ${name}`)
        return true
      }
      case 'SET_FILAMENT_SENSOR':
        set(`filament_switch_sensor ${p.SENSOR}`, { enabled: p.ENABLE === '1' })
        return true
      case 'MANUAL_PROBE':
      case 'PROBE_CALIBRATE':
        if (s.toolhead.homed_axes !== 'xyz') throw fake.rpcError('Must home before probe')
        set('manual_probe', { is_active: true, z_position: 5, z_position_lower: null, z_position_upper: null })
        pushConsole('// Starting manual Z probe. Use TESTZ to adjust position.\n// Finish with ACCEPT or ABORT command.')
        return true
      case 'TESTZ': {
        const mp = s.manual_probe
        if (!mp.is_active) throw fake.rpcError('Unknown command:"TESTZ"')
        let z = mp.z_position
        if (p.Z === '-') z = ((mp.z_position_lower ?? 0) + z) / 2
        else if (p.Z === '+') z = ((mp.z_position_upper ?? z + 1) + z) / 2
        else z = z + Number(p.Z)
        set('manual_probe', { z_position: Math.round(z * 1000) / 1000, z_position_lower: Number(p.Z) < 0 || p.Z === '-' ? z : mp.z_position_lower, z_position_upper: Number(p.Z) > 0 || p.Z === '+' ? z + 0.1 : mp.z_position_upper })
        return true
      }
      case 'BED_SCREWS_ADJUST':
        set('bed_screws', { is_active: true, state: 'adjust', current_screw: 0, accepted_screws: 0 })
        return true
      case 'ADJUSTED':
        if (s.bed_screws.is_active) set('bed_screws', { current_screw: (s.bed_screws.current_screw + 1) % 4 })
        return true
      case 'ACCEPT':
        if (s.manual_probe.is_active) {
          pushConsole(`// probe: z_offset: ${s.manual_probe.z_position.toFixed(3)}`)
          set('manual_probe', { is_active: false })
        } else if (s.bed_screws.is_active) {
          const accepted = s.bed_screws.accepted_screws + 1
          if (accepted >= 4) set('bed_screws', { is_active: false, accepted_screws: 0, current_screw: 0 })
          else set('bed_screws', { accepted_screws: accepted, current_screw: (s.bed_screws.current_screw + 1) % 4 })
        }
        return true
      case 'ABORT':
        set('manual_probe', { is_active: false })
        set('bed_screws', { is_active: false, current_screw: 0, accepted_screws: 0 })
        return true
      case 'SET_PAUSE_NEXT_LAYER': {
        const v = s['gcode_macro SET_PRINT_STATS_INFO']
        set('gcode_macro SET_PRINT_STATS_INFO', { pause_next_layer: { enable: p.ENABLE !== '0', call: p.MACRO ?? v.pause_next_layer.call } })
        return true
      }
      case 'SET_PAUSE_AT_LAYER': {
        const v = s['gcode_macro SET_PRINT_STATS_INFO']
        set('gcode_macro SET_PRINT_STATS_INFO', { pause_at_layer: { enable: p.ENABLE !== '0', layer: Number(p.LAYER ?? v.pause_at_layer.layer), call: p.MACRO ?? v.pause_at_layer.call } })
        return true
      }
      case 'SAVE_CONFIG':
        pushConsole('// fake: SAVE_CONFIG would restart Klipper here.')
        return true
      case 'Z_TILT_ADJUST':
      case 'SCREWS_TILT_CALCULATE':
        if (s.toolhead.homed_axes !== 'xyz') throw fake.rpcError('Must home before probe')
        await new Promise((r) => setTimeout(r, 1500))
        pushConsole(`// ${C}: done`)
        return true
      // Mirrored here so absolute_coordinates follows, then passed on to the core.
      case 'G90':
      case 'G91':
        set('gcode_move', { absolute_coordinates: C === 'G90' })
        return false
      case 'SAVE_GCODE_STATE':
        savedAbsolute = s.gcode_move.absolute_coordinates
        return false
      case 'RESTORE_GCODE_STATE':
        set('gcode_move', { absolute_coordinates: savedAbsolute })
        return false
      case 'G28':
        allSteppers(true)
        return false
      case 'M84':
        allSteppers(false)
        return false
      default:
        return false
    }
  }

  const handlers = fake.handle({})
  const core = handlers['printer.gcode.script']
  fake.handle({
    'printer.gcode.script': async (params, ws) => {
      for (const line of String(params.script).split('\n')) if (!(await handle(line))) await core({ ...params, script: line }, ws)
      const s = st()
      // The live position follows the toolhead, as motion_report would once a move finishes.
      fake.set('motion_report', { live_position: [...s.toolhead.position] })
      return 'ok'
    },
  })
}

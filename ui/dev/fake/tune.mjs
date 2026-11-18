// Tune screen: bed mesh profiles and calibration, Valve Oil's probe report and bed soak test, Z tilt, bed screws,
// endstops, probe accuracy, the load-cell check, the manual probe helper, and input shaper calibration, on a Max 4.
//
// Start a bed soak already running with `--scenario bed-soak`. The test runs 20 times faster than on a printer (a
// 5 × 5 mesh takes 6 s), and the bed heats at the fake's usual rate, so a run settles in about a minute and a half.

const SPEED = 20
const MESH_MIN = [15, 15]
const MESH_MAX = [375, 375]
const BED_SOAK_DIR = 'tltg_bed_soak'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const round = (v, d = 6) => Math.round(v * 10 ** d) / 10 ** d

// A seeded random, so the bed looks the same on every start.
let seed = 7
const rand = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646
const noise = (s) => (rand() - 0.5) * 2 * s

/** The Max 4's bed when cold: a shallow bowl, tilted a little toward the front left. */
function coldZ(x, y) {
  const u = (x - 195) / 180
  const v = (y - 195) / 180
  return 0.07 * (u * u + v * v - 0.6) - 0.045 * u + 0.025 * v + 0.03 * u * v - 0.06 * Math.max(0, -v) ** 2
}

function probed(min, max, count, extra = () => 0, sigma = 0.003) {
  const out = []
  for (let r = 0; r < count; r++) {
    const row = []
    for (let c = 0; c < count; c++) {
      const x = min[0] + (c * (max[0] - min[0])) / (count - 1)
      const y = min[1] + (r * (max[1] - min[1])) / (count - 1)
      row.push(round(coldZ(x, y) + extra(x, y) + noise(sigma)))
    }
    out.push(row)
  }
  return out
}

/** Klipper's interpolated mesh: pps points between probed ones, here with Catmull-Rom like its bicubic. */
function interpolate(m, pps = 2) {
  const n = m.length
  const k = m[0].length
  const rows = (n - 1) * (pps + 1) + 1
  const cols = (k - 1) * (pps + 1) + 1
  const at = (r, c) => m[Math.min(n - 1, Math.max(0, r))][Math.min(k - 1, Math.max(0, c))]
  const cr = (p0, p1, p2, p3, t) => 0.5 * (2 * p1 + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t * t * t)
  const out = []
  for (let i = 0; i < rows; i++) {
    const fr = i / (pps + 1)
    const r = Math.min(n - 2, Math.floor(fr))
    const tr = fr - r
    const row = []
    for (let j = 0; j < cols; j++) {
      const fc = j / (pps + 1)
      const c = Math.min(k - 2, Math.floor(fc))
      const tc = fc - c
      const col = [-1, 0, 1, 2].map((d) => cr(at(r + d, c - 1), at(r + d, c), at(r + d, c + 1), at(r + d, c + 2), tc))
      row.push(round(cr(col[0], col[1], col[2], col[3], tr)))
    }
    out.push(row)
  }
  return out
}

const params = (min, max, count) => ({ min_x: min[0], max_x: max[0], min_y: min[1], max_y: max[1], x_count: count, y_count: count, mesh_x_pps: 2, mesh_y_pps: 2, algo: 'bicubic', tension: 0.2 })

const mesh = {
  loaded: 'default',
  profiles: {
    default: { points: probed(MESH_MIN, MESH_MAX, 9), mesh_params: params(MESH_MIN, MESH_MAX, 9) },
    kamp: { points: probed([96.4, 88.2], [302.6, 296.8], 9), mesh_params: params([96.4, 88.2], [302.6, 296.8], 9) },
    pei_textured: { points: probed(MESH_MIN, MESH_MAX, 9, (x) => 0.04 - x * 0.0002), mesh_params: params(MESH_MIN, MESH_MAX, 9) },
  },
  // A mesh calibrated without saving: shown loaded and adaptive until the profile is saved or cleared.
  unsaved: null,
}
const report = { measured: Date.now() / 1000 - 3 * 3600, profile: 'default', mesh_min: MESH_MIN, mesh_max: MESH_MAX, probe_count: [9, 9], points: [{ x: 195, y: 105, tries: 3, spread: 0.054 }, { x: 330, y: 330, tries: 2, spread: 0.031 }] }
const pending = { items: {}, removed: false }

const SETTINGS = {
  bed_mesh: { adaptive_margin: 0, probe_count: [9, 9], mesh_min: MESH_MIN, mesh_max: MESH_MAX, mesh_pps: [2, 2], algorithm: 'bicubic', bicubic_tension: 0.2, horizontal_move_z: 5, speed: 565, fade_start: 1, fade_end: 0 },
  probe_air: { x_offset: 0, y_offset: 0, z_offset: -0.05, speed: 5, lift_speed: 5, samples: 2, sample_retract_dist: 3, samples_result: 'average', samples_tolerance: 0.02, samples_tolerance_retries: 10 },
  stepper_x: { position_endstop: 392, position_min: -2, position_max: 392, homing_speed: 65 },
  stepper_y: { position_endstop: -1, position_min: -5, position_max: 410, homing_speed: 65 },
  stepper_z: { endstop_pin: 'probe:z_virtual_endstop', position_min: -5, position_max: 342, homing_speed: 8 },
  screws_tilt_adjust: { screw1: [30, 30], screw1_name: 'Front left', screw2: [345, 30], screw2_name: 'Front right', screw3: [345, 345], screw3_name: 'Rear right', screw4: [30, 345], screw4_name: 'Rear left', screw_thread: 'CW-M4', horizontal_move_z: 5, speed: 150 },
  bed_screws: { screw1: [30, 30], screw1_name: 'Front left', screw2: [345, 30], screw2_name: 'Front right', screw3: [345, 345], screw3_name: 'Last right', screw4: [30, 345], screw4_name: 'screw at 30.000,345.000', speed: 50, probe_speed: 5, horizontal_move_z: 5, probe_height: 0 },
  z_tilt: { z_positions: [[-16.5, 202.5], [406.5, 202.5]], retries: 4, retry_tolerance: 0.05, points: [[15, 202.5], [375, 202.5]], horizontal_move_z: 5, speed: 565 },
  input_shaper: { shaper_type: 'mzv', shaper_type_x: 'mzv', damping_ratio_x: 0.1, shaper_freq_x: 54.2, shaper_type_y: 'mzv', damping_ratio_y: 0.1, shaper_freq_y: 41.0 },
  resonance_tester: { move_speed: 50, min_freq: 5, max_freq: 133.33, accel_per_hz: 150, hz_per_sec: 1, probe_points: [[195, 195, 10]], accel_chip: 'lis2dw', max_smoothing: 0.25 },
}

const COMMANDS = {
  BED_MESH_CALIBRATE: 'Adaptive mesh calibration with intelligent probe density optimization.',
  BED_MESH_PROFILE: 'Bed Mesh Persistent Storage management',
  BED_MESH_CLEAR: 'Clear the Mesh so no z-adjustment is made',
  BED_MESH_OUTPUT: 'Retrieve interpolated grid of probed z-points',
  SAVE_CONFIG: 'Overwrite config file and restart',
  SAVE_CONFIG_QD: 'Overwrite config file without restarting',
  Z_TILT_ADJUST: 'Adjust the Z tilt',
  SCREWS_TILT_CALCULATE: 'Tool to help adjust bed leveling screws by calculating the number of turns to level it.',
  BED_SCREWS_ADJUST: 'Tool to help adjust bed leveling screws',
  ADJUSTED: 'Notify the bed screws helper that the current screw was adjusted',
  ACCEPT: 'Accept the current Z position',
  ABORT: 'Abort manual Z probing tool',
  TESTZ: 'Move to new Z height',
  QUERY_ENDSTOPS: 'Report on the status of each endstop',
  QUERY_PROBE: 'Return the status of the z-probe',
  PROBE: 'Probe Z-height at current XY position',
  PROBE_ACCURACY: 'Probe Z-height accuracy at current XY position',
  PROBE_CALIBRATE: 'Calibrate the probe\'s z_offset',
  MANUAL_PROBE: 'Start manual probe helper script',
  Z_OFFSET_APPLY_PROBE: 'Adjust the probe\'s z_offset',
  SHAPER_CALIBRATE: 'Simular to TEST_RESONANCES but suggest input shaper config',
  TEST_RESONANCES: 'Runs the resonance test for a specifed axis',
  SET_INPUT_SHAPER: 'Set cartesian parameters for input shaper',
  VO_PROBE_ACCURACY_CENTER: 'Homes and runs probe accuracy sampling at the bed center.',
  VO_TEST_PROBE: 'Guided cold, motion-free nozzle load-cell response check; requires CONFIRM=1.',
  VO_CORNER_BED_SCREW_CHECK: 'Homes, runs Z tilt, then reports bed screw turn adjustments.',
  VO_BED_SOAK: 'Measure how long the bed takes to heat and stop moving',
  VO_BED_SOAK_STOP: 'Stop the bed soak test',
  VO_SAVE_CONFIG_QD: 'Save pending SAVE_CONFIG values without blocking Klipper',
}

export function install(fake) {
  const { set, pushConsole, rpcError } = fake
  const st = () => fake.state.status
  const homed = () => ['x', 'y', 'z'].every((a) => (st().toolhead?.homed_axes ?? '').includes(a))
  const requireHomed = () => {
    if (!homed()) throw rpcError('Must home axis first: 195.000 195.000 30.000 [0.000]')
  }
  const busy = async (seconds, fn) => {
    set('idle_timeout', { state: 'Printing' })
    try {
      return await fn()
    } finally {
      await sleep(seconds * 1000)
      set('idle_timeout', { state: 'Ready' })
    }
  }
  const home = async () => {
    await sleep(2000)
    set('toolhead', { homed_axes: 'xyz', position: [195, 195, 10, 0] })
    set('z_tilt', { applied: false })
  }

  // ---- bed mesh ----
  function meshStatus() {
    const name = mesh.loaded
    const p = name ? (mesh.unsaved?.name === name ? mesh.unsaved : mesh.profiles[name]) : null
    const mp = p?.mesh_params
    return {
      profile_name: name ?? '',
      mesh_min: mp ? [mp.min_x, mp.min_y] : [0, 0],
      mesh_max: mp ? [mp.max_x, mp.max_y] : [0, 0],
      probed_matrix: p ? p.points : [[]],
      mesh_matrix: p ? interpolate(p.points) : [[]],
      profiles: mesh.profiles,
    }
  }
  const publishMesh = () => {
    set('bed_mesh', meshStatus())
    set('tltg_probe_report', { ...report, loaded: !!mesh.loaded && report.profile === mesh.loaded })
  }
  const publishPending = () => set('configfile', { save_config_pending: pending.removed || Object.keys(pending.items).length > 0, save_config_pending_items: { ...pending.items } })
  const pointsText = (points) => '\n' + points.map((r) => r.map((v) => v.toFixed(6)).join(', ')).join('\n')

  async function calibrateMesh(profile = 'default') {
    requireHomed()
    await busy(0, async () => {
      pushConsole('// Adaptive bed mesh: no print object detected, using the whole bed')
      const extra = (x, y) => 0.004 * Math.sin(x / 60) * Math.cos(y / 80)
      const points = probed(MESH_MIN, MESH_MAX, 9, extra)
      for (let r = 0; r < 9; r += 2) {
        await sleep(600)
        pushConsole(`// probe at ${(15 + r * 45).toFixed(3)},${(15).toFixed(3)} is z=${(points[r][0] + 0.15).toFixed(6)}`)
      }
      mesh.profiles = { ...mesh.profiles, [profile]: { points, mesh_params: params(MESH_MIN, MESH_MAX, 9) } }
      mesh.loaded = profile
      mesh.unsaved = null
      Object.assign(report, { measured: Date.now() / 1000, profile, points: [{ x: 240, y: 60, tries: 2, spread: 0.028 }] })
      pending.items[`bed_mesh ${profile}`] = { version: '1', points: pointsText(points), x_count: '9', y_count: '9', mesh_x_pps: '2', mesh_y_pps: '2', algo: 'bicubic', tension: '0.2', min_x: '15.0', max_x: '375.0', min_y: '15.0', max_y: '375.0' }
      publishMesh()
      publishPending()
      pushConsole(`// Mesh Bed Leveling Complete\n// Bed Mesh state has been saved to profile [${profile}]\n// for the current session.  The SAVE_CONFIG command will\n// update the printer config file and restart the printer.`)
    })
  }

  // ---- calibration helpers ----
  const screws = () => {
    const s = SETTINGS.screws_tilt_adjust
    return [1, 2, 3, 4].map((i) => ({ key: `screw${i}`, xy: s[`screw${i}`], name: s[`screw${i}_name`] }))
  }
  async function screwsTilt() {
    requireHomed()
    await busy(0, async () => {
      const zs = [2.4612, 2.4398, 2.4875, 2.4521]
      const results = {}
      pushConsole('// 01:20 means 1 full turn and 20 minutes, CW=clockwise, CCW=counter-clockwise')
      for (const [i, s] of screws().entries()) {
        await sleep(900)
        const diff = zs[i] - zs[0]
        // CW-M4 is 0.7 mm per turn.
        const turns = Math.abs(diff) / 0.7
        const full = Math.floor(turns)
        const minutes = Math.round((turns - full) * 60)
        const sign = diff < 0 ? 'CCW' : 'CW'
        results[s.key] = { z: zs[i], sign, adjust: i === 0 ? '00:00' : `${String(full).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`, is_base: i === 0 }
        pushConsole(i === 0 ? `// ${s.name} (base) : x=${s.xy[0].toFixed(1)}, y=${s.xy[1].toFixed(1)}, z=${zs[i].toFixed(5)}` : `// ${s.name} : x=${s.xy[0].toFixed(1)}, y=${s.xy[1].toFixed(1)}, z=${zs[i].toFixed(5)} : adjust ${sign} ${results[s.key].adjust}`)
      }
      set('screws_tilt_adjust', { error: false, max_deviation: null, results })
    })
  }
  async function zTilt() {
    requireHomed()
    await busy(0, async () => {
      await sleep(2500)
      pushConsole('// Making the following Z adjustments:\n// stepper_z = 0.013420\n// stepper_z1 = -0.013420\n// Retries: 0/4 Probed points range: 0.026840 tolerance: 0.050000')
      await sleep(1500)
      pushConsole('// Retries: 1/4 Probed points range: 0.004100 tolerance: 0.050000')
      set('z_tilt', { applied: true })
    })
  }
  async function probeAccuracy(samples, x = 195, y = 195) {
    await busy(0, async () => {
      pushConsole(`// PROBE_ACCURACY at X:${x.toFixed(3)} Y:${y.toFixed(3)} Z:10.000 (samples=${samples} retract=3.000 speed=5.0 lift_speed=5.0)\n`)
      const zs = []
      for (let i = 0; i < samples; i++) {
        const z = round(0.0075 + noise(0.004), 6)
        zs.push(z)
        pushConsole(`// probe at ${x.toFixed(3)},${y.toFixed(3)} is z=${z.toFixed(6)}`)
        await sleep(150)
      }
      const sorted = [...zs].sort((a, b) => a - b)
      const avg = zs.reduce((a, v) => a + v, 0) / zs.length
      const sd = Math.sqrt(zs.reduce((a, v) => a + (v - avg) ** 2, 0) / zs.length)
      const median = sorted[Math.floor(sorted.length / 2)]
      pushConsole(`// probe accuracy results: maximum ${sorted[sorted.length - 1].toFixed(6)}, minimum ${sorted[0].toFixed(6)}, range ${(sorted[sorted.length - 1] - sorted[0]).toFixed(6)}, average ${avg.toFixed(6)}, median ${median.toFixed(6)}, standard deviation ${sd.toFixed(6)}`)
    })
  }

  // The manual probe helper, as PROBE_CALIBRATE starts it.
  const manual = { history: [], z: 0, finalize: null }
  function startManual(z, finalize) {
    if (st().manual_probe?.is_active) throw rpcError('Already in a manual Z probe. Use ABORT to abort it.')
    manual.history = []
    manual.z = z
    manual.finalize = finalize
    publishManual()
    pushConsole('// Starting manual Z probe. Use TESTZ to adjust position.\n// Finish with ACCEPT or ABORT command.')
  }
  function publishManual(active = true) {
    const lower = manual.history.filter((h) => h < manual.z).sort((a, b) => b - a)[0] ?? null
    const upper = manual.history.filter((h) => h > manual.z).sort((a, b) => a - b)[0] ?? null
    set('manual_probe', active ? { is_active: true, z_position: round(manual.z, 3), z_position_lower: lower, z_position_upper: upper } : { is_active: false, z_position: null, z_position_lower: null, z_position_upper: null })
    if (active) pushConsole(`// Z position: ${lower?.toFixed(3) ?? '?????'} --> ${manual.z.toFixed(3)} <-- ${upper?.toFixed(3) ?? '?????'}`)
  }
  function testz(arg) {
    const lower = manual.history.filter((h) => h < manual.z).sort((a, b) => b - a)[0]
    const upper = manual.history.filter((h) => h > manual.z).sort((a, b) => a - b)[0]
    manual.history.push(manual.z)
    if (arg === '-' || arg === '--') manual.z = lower != null ? manual.z - (manual.z - lower) * (arg === '--' ? 0.5 : 0.2) : manual.z - (arg === '--' ? 0.5 : 0.1)
    else if (arg === '+' || arg === '++') manual.z = upper != null ? manual.z + (upper - manual.z) * (arg === '++' ? 0.5 : 0.2) : manual.z + (arg === '++' ? 0.5 : 0.1)
    else manual.z += Number(arg)
    manual.z = Math.max(-2, manual.z)
    set('toolhead', { position: [195, 195, round(manual.z, 3), 0] })
    publishManual()
  }

  // Bed screws adjust: Klipper walks the screws, then again with fine adjustments until all are accepted.
  const bedScrews = { state: null, current: 0, accepted: 0 }
  const publishBedScrews = () => set('bed_screws', { is_active: bedScrews.state != null, state: bedScrews.state, current_screw: bedScrews.current, accepted_screws: bedScrews.accepted })
  function nextScrew(accepted) {
    bedScrews.accepted = accepted ? bedScrews.accepted + 1 : 0
    if (bedScrews.accepted >= 4) {
      bedScrews.state = null
      pushConsole('// Bed screws tool completed successfully')
    } else {
      bedScrews.current = (bedScrews.current + 1) % 4
      if (bedScrews.current === 0 && bedScrews.state === 'adjust') bedScrews.state = 'fine'
      pushConsole(`// Adjust ${SETTINGS.bed_screws[`screw${bedScrews.current + 1}_name`]}. Then run ACCEPT, ADJUSTED, or ABORT`)
    }
    publishBedScrews()
  }

  // ---- load-cell check ----
  async function loadCellCheck(p) {
    const t = st().tltg_probe_test
    if (t.active) throw rpcError('A load-cell response check is already running.')
    if (p.CONFIRM !== '1') throw rpcError('Make sure the cold nozzle is accessible and clear of the bed, without homing. Then run VO_TEST_PROBE CONFIRM=1.')
    set('tltg_probe_test', { active: true, result: null, metrics: {} })
    pushConsole('// Load-cell response check: the printer will not move, heat, or change calibration. Do not home or jog during the test. Press lightly only when prompted; never force the nozzle.')
    for (const [instruction, reminder, cue] of [
      ['Measuring the unloaded sensor. Do not touch the nozzle.', 'DO NOT TOUCH', 'Measuring...'],
      ['Next, press lightly upward on the nozzle and hold until told to stop.', 'GET READY TO PRESS', 'PRESS'],
      ['Stop pressing and let go of the nozzle.', 'DO NOT TOUCH', 'Measuring...'],
    ]) {
      pushConsole(`// ${instruction}`)
      for (let i = 0; i < 3; i++) (pushConsole(`// ${reminder}`), await sleep(1000))
      await sleep(2000)
      for (let i = 0; i < 3; i++) (pushConsole(`// ${cue}`), await sleep(1000))
    }
    const metrics = { change: 1840, required_change: 212, noise: 26.4, release_error: 38 }
    set('tltg_probe_test', { active: false, result: 'RESPONSE_DETECTED', metrics })
    pushConsole('// VO_TEST_PROBE RESPONSE_DETECTED: Your load cell readings look correct. The signal changed under light pressure and returned to baseline on release.')
    pushConsole(`// Signal change ${metrics.change} counts (needed ${metrics.required_change}); baseline noise ${Math.round(metrics.noise)}; release error ${metrics.release_error}.`)
    pushConsole('// This checks the sensor signal only; it does not verify the homing trigger.')
  }

  // ---- input shaper ----
  async function shaperCalibrate(axis) {
    requireHomed()
    const axes = axis ? [axis.toLowerCase()] : ['x', 'y']
    const best = { x: ['mzv', 53.8], y: ['mzv', 41.2] }
    await busy(0, async () => {
      pushConsole('// Resonance test with sweeping: ACCEL_PER_HZ=150 SWEEPING_PERIOD=1.2 SWEEPING_ACCEL=400')
      for (const a of axes) {
        pushConsole(`// Testing axis ${a}`)
        for (let f = 5; f <= 133; f += 8) (pushConsole(`// Testing frequency ${f} Hz`), await sleep(160))
      }
      pushConsole('// Wait for calculations..')
      await sleep(1200)
      const save = { ...(pending.items.input_shaper ?? {}) }
      for (const a of axes) {
        pushConsole(`// Calculating the best input shaper parameters for ${a} axis`)
        const [type, freq] = best[a]
        for (const [s, f, vib, sm, acc] of [
          ['zv', freq + 4.4, 6.1, 0.044, 14000],
          ['mzv', freq, 1.2, 0.067, 9400],
          ['ei', freq + 9.6, 1.6, 0.071, 8900],
          ['2hump_ei', freq + 17.2, 0.4, 0.081, 7700],
          ['3hump_ei', freq + 24.8, 0.1, 0.088, 7100],
        ]) {
          await sleep(200)
          pushConsole(`// Fitted shaper '${s}' frequency = ${f.toFixed(1)} Hz (vibrations = ${vib.toFixed(1)}%, smoothing ~= ${sm.toFixed(3)})`)
          pushConsole(`// To avoid too much smoothing with '${s}', suggested max_accel <= ${acc} mm/sec^2`)
        }
        pushConsole(`// Recommended shaper_type_${a} = ${type}, shaper_freq_${a} = ${freq.toFixed(1)} Hz`)
        pushConsole(`// Shaper calibration data written to /tmp/calibration_data_${a}_20261117_101500.csv file`)
        Object.assign(save, { [`shaper_type_${a}`]: type, [`shaper_freq_${a}`]: freq.toFixed(1) })
      }
      pending.items.input_shaper = save
      publishPending()
      pushConsole('// The SAVE_CONFIG command will update the printer config file\n// with these parameters and restart the printer.')
    })
  }

  async function saveConfig() {
    // What SAVE_CONFIG writes becomes the config Klipper starts with.
    for (const [section, values] of Object.entries(pending.items)) {
      if (section === 'input_shaper') for (const [k, v] of Object.entries(values)) SETTINGS.input_shaper[k] = k.startsWith('shaper_freq') ? Number(v) : v
      if (section === 'probe_air') SETTINGS.probe_air.z_offset = Number(values.z_offset)
    }
    pending.items = {}
    pending.removed = false
    pushConsole('// fake: SAVE_CONFIG wrote printer.cfg; Klipper restarts')
    fake.state.klippy = 'startup'
    fake.broadcast('notify_klippy_disconnected', [])
    await sleep(2500)
    fake.state.klippy = 'ready'
    set('toolhead', { homed_axes: '' })
    publishPending()
    fake.broadcast('notify_klippy_ready', [])
  }

  // ---- bed soak ----
  const files = new Map()
  const writeLine = (name, record) => {
    const f = files.get(name) ?? { lines: [], modified: Date.now() / 1000 }
    f.lines.push(JSON.stringify(record))
    f.modified = Date.now() / 1000
    files.set(name, f)
    fake.broadcast('notify_filelist_changed', [{ action: 'modify_file', item: { path: `${BED_SOAK_DIR}/${name}`, root: 'config' } }])
  }
  const fileName = (started, bed, chamber) => {
    const d = new Date(started * 1000)
    const p = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}-bed${bed}${chamber ? `-chamber${chamber}` : ''}.jsonl`
  }
  const LIMITS = { speed: 0.002, out_of_line: 0.015, jump: 0.015, bed_wait: 1, chamber_wait: 3 }
  const idleSoak = () => ({
    state: 'idle',
    step: '',
    stop_requested: false,
    chamber_available: true,
    settings: null,
    limits: LIMITS,
    started: null,
    heaters_on: null,
    elapsed: null,
    milestones: { bed: null, chamber: null, chamber_max: null, settled: null, ready: null },
    meshes: 0,
    latest: null,
    mesh_progress: null,
    problems: { points: 0, jumps: 0 },
    file: '',
    reason: '',
  })
  let soak = idleSoak()
  let soakRun = null
  const publishSoak = (fields) => {
    soak = { ...soak, ...fields }
    set('tltg_bed_soak', soak)
  }

  /** The bed's change from cold t seconds after the heaters turned on: height rises with the bed and keeps creeping. */
  const heatChange = (t, frac) => {
    if (t <= 0) return { h: 0, s: 0 }
    return { h: 0.12 * frac + 0.035 * (1 - Math.exp(-t / 700)), s: 0.018 * (1 - Math.exp(-t / 500)) }
  }
  const bulge = (x, y) => 1 - (((x - 195) / 180) ** 2 + ((y - 195) / 180) ** 2) / 1.3
  const meshFromChange = (count, h, s, sigma = 0.0015) => probed(MESH_MIN, MESH_MAX, count, (x, y) => h + s * 2.2 * (bulge(x, y) - 0.5), sigma)
  const heightShape = (matrix, cold, offset = 0) => {
    const changes = matrix.flat().map((z, i) => z - offset - cold.flat()[i])
    const h = changes.reduce((a, v) => a + v, 0) / changes.length
    return { height: round(h, 4), shape: round(changes.reduce((a, v) => a + Math.abs(v - h), 0) / changes.length, 4) }
  }

  /** A whole run at once, for the past runs. Temperatures follow first-order heating. */
  function seedRun({ daysAgo, bed, chamber, count = 5, timeLimit = 90, stopWhenSettled = true, keepHeating = false, end, reason, retried = {}, jumpMesh = null, maxMeshes = 99, noResult = false, slowSettle = false }) {
    const started = Math.round(Date.now() / 1000 - daysAgo * 86400)
    const name = fileName(started, bed, chamber)
    const D = count * count * 4.8
    const bedT = (t) => (t <= 0 ? 24 : 24 + (bed - 24) * (1 - Math.exp(-t / 170)))
    const chT = (t) => (!chamber ? null : t <= 0 ? 25 : 25 + (chamber - 25) * (1 - Math.exp(-t / 380)))
    writeLine(name, { type: 'settings', version: 1, started, bed, chamber: chamber ?? null, nozzle: 140, probe_count: count, time_limit: timeLimit, stop_when_settled: stopWhenSettled, keep_heating: keepHeating, mesh_min: MESH_MIN, mesh_max: MESH_MAX, limits: LIMITS, check_point: [195, 195] })
    const heatersOn = started + 160 + D
    const cold = meshFromChange(count, 0, 0)
    writeLine(name, { type: 'heaters_on', time: 0, epoch: heatersOn })
    const temps = (from, to) => {
      const out = []
      for (let t = from; t <= to; t += 10) out.push([round(t, 1), round(bedT(t), 2), chT(t) == null ? null : round(chT(t), 2)])
      return out
    }
    writeLine(name, { type: 'mesh', index: 0, start: -D - 5, end: -5, time: round(-D / 2 - 5, 1), matrix: cold, offset: 0, height: 0, shape: 0, speed: null, used: true, retried: retried[0] ?? [], out_of_line: [], check: { before: 0.002, after: 0.0021, jump: null }, temps: temps(-D - 5, -5) })
    const ms = { bed: null, chamber: null, chamber_max: null, settled: null, ready: null }
    for (let t = 0; t < 7200; t += 10) {
      if (ms.bed == null && bedT(t) >= bed - 1) ms.bed = t
      if (chamber && ms.chamber == null && chT(t) >= chamber - 3) ms.chamber = t
    }
    let prevH = 0
    let under = 0
    let index = 1
    let t = 0
    let jumps = []
    for (; t + D <= timeLimit * 60 && index <= maxMeshes; index++, t += D) {
      const mid = t + D / 2
      const frac = (bedT(mid) - 24) / (bed - 24)
      const ch = heatChange(mid, frac)
      // A bed that keeps creeping up, about 2.7 µm a minute, never settles.
      const matrix = meshFromChange(count, ch.h + (slowSettle ? 0.0000445 * mid : 0), ch.s)
      const used = jumpMesh !== index
      const { height, shape } = heightShape(matrix, cold)
      const speed = round(Math.abs(height - prevH) / (D / 60), 6)
      prevH = height
      if (!used) jumps.push({ mesh: index, time: round(mid, 1), size: 0.041 })
      writeLine(name, { type: 'mesh', index, start: round(t, 1), end: round(t + D, 1), time: round(mid, 1), matrix, offset: 0, height, shape, speed, used, retried: retried[index] ?? [], out_of_line: [], check: { before: 0.002 + height, after: 0.002 + height, jump: used ? null : 0.041 }, temps: temps(t, t + D) })
      if (!used) writeLine(name, { type: 'jump', mesh: index, time: round(mid, 1), size: 0.041 })
      if (ms.bed != null && mid >= ms.bed && speed < LIMITS.speed) under++
      else under = 0
      if (under >= 3 && ms.settled == null) {
        ms.settled = round(mid - 2 * D, 1)
        ms.ready = Math.max(ms.settled, ms.chamber ?? 0)
        if (stopWhenSettled && (!chamber || ms.chamber != null)) {
          t += D
          index++
          break
        }
      }
    }
    if (noResult) return
    // Milestones reached after the run ended didn't happen.
    if (ms.bed != null && ms.bed > t) ms.bed = null
    if (ms.chamber != null && ms.chamber > t) ms.chamber = null
    const state = end ?? 'finished'
    writeLine(name, {
      type: 'result',
      state,
      reason: reason ?? (state === 'finished' ? (ms.settled != null && stopWhenSettled ? 'The bed stopped moving' : 'Reached the time limit') : ''),
      ended: heatersOn + t,
      milestones: ms,
      meshes: index,
      jumps,
      problem_points: [],
      warning: jumps.length > 0 || Object.values(retried).flat().length > 1,
    })
  }
  const P = (x, y, row, col, tries, spread) => ({ x, y, row, col, tries, spread })
  seedRun({ daysAgo: 6.4, bed: 100, chamber: 50 })
  seedRun({ daysAgo: 4.9, bed: 100, chamber: 50, retried: { 2: [P(285, 15, 0, 3, 3, 0.051)], 3: [P(285, 15, 0, 3, 2, 0.033)] }, jumpMesh: 4 })
  seedRun({ daysAgo: 3.2, bed: 60, chamber: null, count: 7, timeLimit: 30, stopWhenSettled: false, slowSettle: true })
  seedRun({ daysAgo: 2.1, bed: 110, chamber: 60, end: 'stopped', reason: 'Stopped by the operator', maxMeshes: 4 })
  seedRun({ daysAgo: 1.3, bed: 90, chamber: null, count: 5, maxMeshes: 3, noResult: true })

  /** The live run: the same steps as tltg_bed_soak, sped up. */
  async function startSoak(p) {
    if (soak.state === 'running') throw rpcError('A bed soak test is already running')
    if (st().print_stats?.state === 'printing') throw rpcError("A bed soak test can't run during a print")
    if (!p.BED) throw rpcError('VO_BED_SOAK needs BED=')
    const bed = Number(p.BED)
    const chamber = Number(p.CHAMBER ?? 0) || null
    const count = Number(p.PROBE_COUNT ?? 5)
    if (bed > 120) throw rpcError(`BED=${bed} is outside that heater's limits (up to 120)`)
    if (count < 3 || count > 11) throw rpcError(`Error on 'VO_BED_SOAK': PROBE_COUNT must have a minimum of 3`)
    const settings = { bed, chamber, nozzle: 140, probe_count: count, time_limit: Number(p.TIME_LIMIT ?? 90), stop_when_settled: (p.STOP_WHEN_SETTLED ?? '1') !== '0', keep_heating: p.KEEP_HEATING === '1' }
    const started = Date.now() / 1000
    const name = fileName(Math.round(started), bed, chamber)
    soakRun = { name, stop: null, settings }
    writeLine(name, { type: 'settings', version: 1, started, ...settings, mesh_min: MESH_MIN, mesh_max: MESH_MAX, limits: LIMITS, check_point: [195, 195] })
    publishSoak({ ...idleSoak(), state: 'running', step: 'nozzle', settings, started, file: `${BED_SOAK_DIR}/${name}` })
    runSoak(soakRun).catch((e) => console.error('fake bed soak', e))
  }

  async function runSoak(run) {
    const { settings: s, name } = run
    const D = s.probe_count ** 2 * 4.8
    const stopped = () => run.stop
    const wait = async (simSeconds) => {
      const end = Date.now() + (simSeconds / SPEED) * 1000
      while (Date.now() < end) {
        if (run.stop === 'now') throw new Error('stop')
        await sleep(100)
      }
    }
    const heat = (on) => {
      set('heater_bed', { target: on ? s.bed : 0 })
      if (s.chamber) set('heater_generic chamber', { target: on ? s.chamber : 0 })
    }
    const chamberT = () => st()['heater_generic chamber']?.temperature ?? null
    let pendingTemps = []
    let heatersOnAt = null
    const ms = { bed: null, chamber: null, chamber_max: null, settled: null, ready: null }
    const meshes = []
    let state = 'finished'
    let reason = ''
    // Sample temperatures, the elapsed time, and the milestones as the test runs.
    const sampler = setInterval(() => {
      const t = heatersOnAt == null ? null : ((Date.now() - heatersOnAt) / 1000) * SPEED
      const bedT = st().heater_bed.temperature
      const ch = chamberT()
      if (t != null) {
        pendingTemps.push([round(t, 1), round(bedT, 2), s.chamber ? round(ch, 2) : null])
        if (ms.bed == null && bedT >= s.bed - LIMITS.bed_wait) ms.bed = round(t, 1)
        if (s.chamber && ms.chamber == null && ch >= s.chamber - LIMITS.chamber_wait) ms.chamber = round(t, 1)
      }
      const fields = { elapsed: t == null ? null : round(t, 1), milestones: { ...ms } }
      if (run.meshStarted) fields.mesh_progress = Math.min(0.99, round((Date.now() - run.meshStarted) / ((D / SPEED) * 1000), 2))
      publishSoak(fields)
    }, 500)
    const measure = async (index, cold) => {
      run.meshStarted = Date.now()
      publishSoak({ step: index === 0 ? 'cold_mesh' : 'mesh', mesh_progress: 0 })
      const start = heatersOnAt == null ? null : ((Date.now() - heatersOnAt) / 1000) * SPEED
      await wait(D)
      run.meshStarted = null
      const end = heatersOnAt == null ? null : ((Date.now() - heatersOnAt) / 1000) * SPEED
      const mid = start == null ? 0 : (start + end) / 2
      const bedT = st().heater_bed.temperature
      const frac = Math.max(0, (bedT - 28) / (s.bed - 28))
      const ch = heatChange(mid, frac)
      const matrix = meshFromChange(s.probe_count, ch.h, ch.s)
      const m = { type: 'mesh', index, start: start == null ? null : round(start, 1), end: end == null ? null : round(end, 1), time: round(mid, 1), matrix, offset: 0, height: 0, shape: 0, speed: null, used: true, retried: [], out_of_line: [], check: { before: null, after: null, jump: null }, temps: [] }
      if (index === 3) m.retried = [{ x: 105, y: 285, row: 3, col: 1, tries: 3, spread: 0.047 }]
      if (cold) {
        Object.assign(m, heightShape(matrix, cold.matrix))
        const prev = meshes[meshes.length - 1]
        m.speed = round(Math.abs(m.height - prev.height) / ((m.time - prev.time) / 60), 6)
      }
      meshes.push(m)
      return m
    }
    try {
      for (const [step, sec] of [
        ['nozzle', 40],
        ['home', 30],
        ['wipe', 15],
        ['z_tilt', 10],
        ['home_z', 10],
      ]) {
        publishSoak({ step })
        if (step === 'nozzle') set('extruder', { target: 140 })
        await wait(sec)
        if (step === 'home') set('toolhead', { homed_axes: 'xyz' })
      }
      const cold = await measure(0, null)
      heatersOnAt = Date.now()
      heat(true)
      // The cold mesh's times count back from the heaters turning on.
      Object.assign(cold, { start: round(-D - 5, 1), end: -5, time: round(-D / 2 - 5, 1), temps: [[round(-D - 5, 1), 28, s.chamber ? 30 : null], [-5, 28.1, s.chamber ? 30.1 : null]] })
      writeLine(name, { type: 'heaters_on', time: 0, epoch: heatersOnAt / 1000 })
      writeLine(name, cold)
      publishSoak({ heaters_on: heatersOnAt / 1000, elapsed: 0, meshes: 1, latest: { index: 0, time: cold.time, height: 0, shape: 0, speed: null, used: true } })
      let under = 0
      for (let index = 1; ; index++) {
        if (stopped()) {
          ;(state = 'stopped'), (reason = 'Stopped by the operator')
          break
        }
        const m = await measure(index, cold)
        m.temps = pendingTemps
        pendingTemps = []
        writeLine(name, m)
        if (ms.bed != null && m.time >= ms.bed && m.speed < LIMITS.speed) under++
        else under = 0
        if (under >= 3 && ms.settled == null) {
          ms.settled = meshes[meshes.length - 3].time
          ms.ready = Math.max(ms.settled, ms.chamber ?? 0)
        }
        publishSoak({ meshes: meshes.length, latest: { index, time: m.time, height: m.height, shape: m.shape, speed: m.speed, used: true }, problems: { points: index >= 3 ? 1 : 0, jumps: 0 }, milestones: { ...ms } })
        if (s.stop_when_settled && ms.settled != null && (!s.chamber || ms.chamber != null)) {
          reason = 'The bed stopped moving'
          break
        }
        if (m.end >= s.time_limit * 60) {
          reason = 'Reached the time limit'
          break
        }
      }
    } catch {
      ;(state = 'stopped'), (reason = 'Stopped by the operator')
    }
    clearInterval(sampler)
    publishSoak({ step: 'finishing', mesh_progress: null })
    await sleep(600)
    set('extruder', { target: 0 })
    if (!(state === 'finished' && s.keep_heating)) heat(false)
    const elapsed = heatersOnAt == null ? 0 : ((Date.now() - heatersOnAt) / 1000) * SPEED
    const ended = (heatersOnAt ?? Date.now()) / 1000 + elapsed
    writeLine(name, { type: 'result', state, reason, ended, milestones: { ...ms }, meshes: meshes.length, jumps: [], problem_points: [], warning: false })
    publishSoak({ state, step: '', stop_requested: false, reason, milestones: { ...ms } })
    pushConsole(`// Bed soak test ${state}: ${reason}\n// Bed at temperature: ${ms.bed == null ? '-' : `${(ms.bed / 60).toFixed(1)} min`}\n// Saved as ${BED_SOAK_DIR}/${name}`)
    soakRun = null
  }

  // ---- G-code ----
  fake.commands(COMMANDS)
  fake.settings(SETTINGS)
  fake.gcode(async (C, p, words, line) => {
    const mp = st().manual_probe?.is_active
    switch (C) {
      case 'BED_MESH_CALIBRATE':
      case '_BED_MESH_CALIBRATE':
        await calibrateMesh(p.PROFILE ?? 'default')
        return true
      case 'BED_MESH_PROFILE': {
        if (p.LOAD) {
          const n = p.LOAD.replace(/^"|"$/g, '')
          if (!mesh.profiles[n]) throw rpcError(`bed_mesh: Unknown profile [${n}]`)
          mesh.loaded = n
          mesh.unsaved = null
        } else if (p.SAVE) {
          const n = p.SAVE.replace(/^"|"$/g, '')
          const cur = meshStatus()
          if (!mesh.loaded) throw rpcError('Unable to save to profile, the bed has not been probed')
          const src = mesh.unsaved?.name === mesh.loaded ? mesh.unsaved : mesh.profiles[mesh.loaded]
          mesh.profiles = { ...mesh.profiles, [n]: { points: src.points, mesh_params: src.mesh_params } }
          mesh.loaded = n
          mesh.unsaved = null
          pending.items[`bed_mesh ${n}`] = { version: '1', points: pointsText(cur.probed_matrix) }
          pushConsole(`// Bed Mesh state has been saved to profile [${n}]\n// for the current session.  The SAVE_CONFIG command will\n// update the printer config file and restart the printer.`)
        } else if (p.REMOVE) {
          const n = p.REMOVE.replace(/^"|"$/g, '')
          const { [n]: _gone, ...rest } = mesh.profiles
          mesh.profiles = rest
          if (pending.items[`bed_mesh ${n}`]) delete pending.items[`bed_mesh ${n}`]
          else pending.removed = true
          pushConsole(`// Profile [${n}] removed from storage for this session.\n// The SAVE_CONFIG command will update the printer\n// configuration and restart the printer`)
        }
        publishMesh()
        publishPending()
        return true
      }
      case 'BED_MESH_CLEAR':
        mesh.loaded = null
        mesh.unsaved = null
        publishMesh()
        return true
      case 'BED_MESH_OUTPUT':
        pushConsole('// Mesh Leveling Probed Z positions:\n' + meshStatus().probed_matrix.map((r) => '// ' + r.map((v) => v.toFixed(6)).join(' ')).join('\n'))
        return true
      case 'SAVE_CONFIG':
        await saveConfig()
        return true
      case 'SAVE_CONFIG_QD':
      case 'VO_SAVE_CONFIG_QD':
        pushConsole('// fake: printer.cfg written without restarting')
        return true
      case 'Z_TILT_ADJUST':
        await zTilt()
        return true
      case 'SCREWS_TILT_CALCULATE':
        await screwsTilt()
        return true
      case 'VO_CORNER_BED_SCREW_CHECK':
        await home()
        await zTilt()
        await screwsTilt()
        return true
      case 'QUERY_PROBE':
        pushConsole('// probe: open')
        set('probe', { last_query: false })
        return true
      case 'QUERY_ENDSTOPS':
        set('query_endstops', { last_query: { x: 0, y: 0, z: 0 } })
        pushConsole('// x:open y:open z:open')
        return true
      case 'PROBE_ACCURACY':
        requireHomed()
        await probeAccuracy(Number(p.SAMPLES ?? 10))
        return true
      case 'VO_PROBE_ACCURACY_CENTER':
        await home()
        await probeAccuracy(Number(p.SAMPLES ?? 20))
        return true
      case 'VO_TEST_PROBE':
        await loadCellCheck(p)
        return true
      case 'PROBE_CALIBRATE':
        requireHomed()
        pushConsole('// probe at 195.000,195.000 is z=0.051250')
        startManual(5, (z) => {
          const offset = round(-0.05 + (z - 0.1) * 0.1, 3)
          pending.items.probe_air = { z_offset: offset.toFixed(3) }
          publishPending()
          pushConsole(`// probe: z_offset: ${offset.toFixed(3)}\n// The SAVE_CONFIG command will update the printer config file\n// with the above and restart the printer.`)
        })
        return true
      case 'MANUAL_PROBE':
        requireHomed()
        startManual(5, (z) => pushConsole(`// Z position is ${z.toFixed(3)}`))
        return true
      case 'TESTZ':
        if (!mp) throw rpcError('Unknown command:"TESTZ"')
        testz(p.Z ?? '0')
        return true
      case 'ACCEPT':
        if (mp) {
          publishManual(false)
          manual.finalize?.(manual.z)
        } else if (bedScrews.state) nextScrew(true)
        else throw rpcError('Unknown command:"ACCEPT"')
        return true
      case 'ADJUSTED':
        if (!bedScrews.state) throw rpcError('Unknown command:"ADJUSTED"')
        nextScrew(false)
        return true
      case 'ABORT':
        if (mp) (publishManual(false), pushConsole('// Manual probe aborted'))
        else if (bedScrews.state) (Object.assign(bedScrews, { state: null }), publishBedScrews(), pushConsole('// Bed screws tool aborted'))
        else throw rpcError('Unknown command:"ABORT"')
        return true
      case 'BED_SCREWS_ADJUST':
        requireHomed()
        if (bedScrews.state) throw rpcError('Already in bed_screws helper; use ABORT to exit')
        Object.assign(bedScrews, { state: 'adjust', current: 0, accepted: 0 })
        publishBedScrews()
        pushConsole(`// Adjust ${SETTINGS.bed_screws.screw1_name}. Then run ACCEPT, ADJUSTED, or ABORT\n// Use ADJUSTED if a significant screw adjustment is made`)
        return true
      case 'SHAPER_CALIBRATE':
        await shaperCalibrate(p.AXIS)
        return true
      case 'VO_BED_SOAK':
        await startSoak(p)
        return true
      case 'VO_BED_SOAK_STOP':
        if (soak.state !== 'running') throw rpcError('No bed soak test is running')
        soakRun.stop = 'after_mesh'
        publishSoak({ stop_requested: true })
        pushConsole('// Bed soak test stops after the mesh in progress')
        return true
    }
    return false
  })

  // ---- RPC and HTTP ----
  fake.handle({
    'printer.query_endstops.status': () => {
      set('query_endstops', { last_query: { x: 0, y: 0, z: 0 } })
      return { x: 'open', y: 'open', z: 'open' }
    },
    'printer.tltg_bed_soak.stop': () => {
      if (soak.state !== 'running') throw rpcError('No bed soak test is running')
      soakRun.stop = 'now'
      publishSoak({ stop_requested: true })
      return {}
    },
  })

  fake.http(async (req, res, path, url) => {
    if (path === '/server/files/directory' && url.searchParams.get('path') === `config/${BED_SOAK_DIR}`) {
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ result: { dirs: [], files: [...files].map(([filename, f]) => ({ filename, modified: f.modified, size: f.lines.join('\n').length, permissions: 'rw' })), disk_usage: { total: 7.2e9, used: 3.1e9, free: 4.1e9 }, root_info: { name: 'config', permissions: 'rw' } } }))
      return true
    }
    const prefix = `/server/files/config/${BED_SOAK_DIR}/`
    if (!path.startsWith(prefix)) return false
    const name = path.slice(prefix.length)
    const f = files.get(name)
    if (!f) {
      res.writeHead(404).end('not found')
      return true
    }
    if (req.method === 'DELETE') {
      if (soakRun?.name === name) {
        res.writeHead(400).end('in use')
        return true
      }
      files.delete(name)
      fake.broadcast('notify_filelist_changed', [{ action: 'delete_file', item: { path: `${BED_SOAK_DIR}/${name}`, root: 'config' } }])
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ result: { item: { path: `${BED_SOAK_DIR}/${name}`, root: 'config' }, action: 'delete_file' } }))
      return true
    }
    res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Disposition': `attachment; filename="${name}"` })
    res.end(f.lines.join('\n') + '\n')
    return true
  })

  // ---- Klipper objects, for every scenario ----
  fake.status((status, scenario) => {
    Object.assign(status, {
      bed_mesh: meshStatus(),
      tltg_probe_report: { ...report, loaded: !!mesh.loaded && report.profile === mesh.loaded },
      tltg_probe_test: { active: false, result: null, metrics: {} },
      tltg_bed_soak: soak,
      screws_tilt_adjust: { error: false, max_deviation: null, results: [] },
      z_tilt: { applied: scenario === 'printing' },
      query_endstops: { last_query: {} },
      probe: { name: 'probe_air', last_query: false, last_z_result: 0 },
      probe_air: { name: 'probe_air', last_query: false, last_z_result: 0 },
      manual_probe: { is_active: false, z_position: null, z_position_lower: null, z_position_upper: null },
      bed_screws: { is_active: false, state: null, current_screw: 0, accepted_screws: 0 },
      configfile: { ...(status.configfile ?? {}), save_config_pending: pending.removed || Object.keys(pending.items).length > 0, save_config_pending_items: { ...pending.items }, warnings: [] },
    })
    status.toolhead = { axis_minimum: [-2, -5, -5, 0], axis_maximum: [392, 410, 342, 0], ...status.toolhead }
    status.tltg_macro_status = { box_available: true, vendor_flow: true, loaded_slot: 'slot1', max_accel: 30000, ...status.tltg_macro_status, input_shaper: { loaded: true, unshaped_axes: '', saved: true } }
    if (scenario === 'bed-soak' && soak.state !== 'running') setTimeout(() => startSoak({ BED: '100', CHAMBER: '45', PROBE_COUNT: '5' }), 500)
  })
}

// Files: every Moonraker root the Files screen browses, as one in-memory tree per root. gcodes starts from the core's
// files plus a couple of folders; config holds a made-up QIDI configuration (printer.cfg, klipper-macros-qd/,
// tltg-optimized-macros/, box.cfg, moonraker.conf, a read-only fluidd.cfg); config_examples and docs are read-only.
//
// Answers server.files.get_directory (with extended G-code metadata), list, metadata, metascan, move, copy,
// delete_file, delete_directory, post_directory, zip (a real zip), uploads with root, path, and print, and downloads
// at GET /server/files/<root>/<path>, and sends notify_filelist_changed for every change. G-code files without
// uploaded content are left to the G-code preview plugin, which generates them from their names. Also adds
// job_queue, machine.system_info, and machine.services.restart when no other plugin has.
import { crc32, deflateRawSync } from 'node:zlib'

const dirname = (p) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '')
const basename = (p) => p.slice(p.lastIndexOf('/') + 1)

const PRINTER_CFG = `# QIDI Max 4 (fake). Valve Oil's optimized macros load after QIDI's.
[include MCU_ID.cfg]
[include timelapse.cfg]
[include klipper-macros-qd/*.cfg]
[include tltg-optimized-macros/*.cfg]
[include box.cfg]

[printer]
kinematics: corexy
max_velocity: 600
max_accel: 20000
max_z_velocity: 20
max_z_accel: 500
square_corner_velocity: 8

[stepper_x]
step_pin: PB4
dir_pin: !PB3
enable_pin: !PB5
microsteps: 16
rotation_distance: 40
full_steps_per_rotation: 200
endstop_pin: tmc5160_stepper_x: virtual_endstop
position_min: -1
position_endstop: -1
position_max: 394
homing_speed: 60
homing_retract_dist: 0

[extruder]
step_pin: PA6
dir_pin: PA7
enable_pin: !PA5
rotation_distance: 53.5
gear_ratio: 1517:170
microsteps: 16
nozzle_diameter: 0.400
filament_diameter: 1.750
heater_pin: PB8
sensor_type: NTC 100K MGB18-104F39050L32
sensor_pin: PA1
min_temp: 0
max_temp: 370
pressure_advance: 0.025 ; tuned for PLA
max_extrude_only_distance: 1000

[heater_bed]
heater_pin: PB10
sensor_type: NTC 100K MGB18-104F39050L32
sensor_pin: PC3
min_temp: -10
max_temp: 120

[heater_generic chamber]
heater_pin: PC8
max_power: 0.4
sensor_type: NTC 100K MGB18-104F39050L32
sensor_pin: PC1
control: watermark
min_temp: -100
max_temp: 70

[z_tilt]
z_positions: -17.5, 18.8
  -17.5, 362.8
  411.3, 190
points: 30, 25
  30, 365
  360, 190
speed: 300
horizontal_move_z: 5
retries: 3
retry_tolerance: 0.05

[gcode_macro PRINT_PREHEAT]
description: Heat the bed and chamber before a print
variable_bed: 60
gcode:
  {% set bed = params.BED|default(printer["gcode_macro PRINT_PREHEAT"].bed)|float %}
  {% set chamber = params.CHAMBER|default(0)|int %}
  M140 S{bed}
  {% if chamber > 0 and printer["heater_generic chamber"] is defined %}
    SET_HEATER_TEMPERATURE HEATER=chamber TARGET={chamber}
  {% endif %}
  RESPOND MSG="Preheating to {bed} °C"

#*# <---------------------- SAVE_CONFIG ---------------------->
#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.
#*#
#*# [extruder]
#*# control = pid
#*# pid_kp = 21.527
#*# pid_ki = 1.063
#*# pid_kd = 108.982
#*#
#*# [bed_mesh default]
#*# version = 1
#*# points =
#*# 	-0.052500, -0.022500, 0.005000
#*# 	-0.040000, -0.010000, 0.012500
#*# 	-0.030000, 0.002500, 0.025000
#*# x_count = 3
#*# y_count = 3
#*# min_x = 25.0
#*# max_x = 365.0
#*# min_y = 25.0
#*# max_y = 365.0
`

const MCU_CFG = `# Made-up placeholder; the printer's real file names its boards.
[mcu]
serial: /dev/ttyS7
baud: 500000
restart_method: command

[mcu THR]
serial: /dev/ttyS8
baud: 500000
restart_method: command
`

const START_END = `[gcode_macro PRINT_START]
gcode:
  {% set bed = params.BED|default(60)|float %}
  {% set hotend = params.HOTEND|default(220)|float %}
  {% set chamber = params.CHAMBER|default(0)|int %}
  M140 S{bed}
  G28
  Z_TILT_ADJUST
  M190 S{bed}
  M104 S{hotend}
  CLEAR_NOZZLE HOTEND={hotend}
  BED_MESH_CALIBRATE ADAPTIVE=1
  M109 S{hotend}

[gcode_macro PRINT_END]
gcode:
  M104 S0
  M140 S0
  G91
  G1 Z2 F600
  G90
  G1 X380 Y380 F12000
  M84
`

const FILAMENT_CFG = `[gcode_macro M603]
description: Unload filament
gcode:
  {% set temp = printer.extruder.target|int %}
  {% if temp < 200 %}
    M109 S220
  {% endif %}
  G91
  G1 E-60 F600
  G90
`

const QD_GLOBALS = `[gcode_macro _QD_GLOBALS]
variable_wipe_x: 382
variable_wipe_y: 395
variable_purge_length: 40
gcode:
`

const VO_GLOBALS = `# Valve Oil's optimized macros. Installer-managed: changes here are replaced on the next install.
[gcode_macro _VO_GLOBALS]
variable_version: "26.11.16.1"
variable_heat_soak_minutes: 0
gcode:
`

const COOLING = `[gcode_macro M106]
rename_existing: M106.1
gcode:
  {% set p = params.P|default(0)|int %}
  {% set s = params.S|default(255)|int %}
  {% if p == 3 %}
    SET_FAN_SPEED FAN=chamber_fan SPEED={s / 255}
  {% else %}
    M106.1 {rawparams}
  {% endif %}
`

const BOX_CFG = `[box_extras]

[multi_color_controller]
box_count: 2

[heater_generic heater_box1]
heater_pin: box1:PB0
max_power: 0.5
sensor_type: aht20_f
min_temp: -100
max_temp: 70
control: watermark
`

const MOONRAKER_CONF = `[server]
host: 0.0.0.0
port: 7125
klippy_uds_address: /home/qidi/printer_data/comms/klippy.sock

[authorization]
trusted_clients:
  10.0.0.0/8
  127.0.0.0/8
  169.254.0.0/16
  172.16.0.0/12
  192.168.0.0/16
cors_domains:
  *.lan
  *.local

[octoprint_compat]

[history]

[job_queue]
load_on_startup: False

[timelapse]
output_path: ~/printer_data/timelapse/
frame_path: /tmp/timelapse/printer
`

const FLUIDD_CFG = `# QIDI's Fluidd macros. Read-only: Valve Oil leaves this file as QIDI ships it.
[virtual_sdcard]
path: ~/printer_data/gcodes
on_error_gcode: CANCEL_PRINT

[pause_resume]

[display_status]

[gcode_macro CANCEL_PRINT]
description: Cancel the actual running print
rename_existing: CANCEL_PRINT_BASE
gcode:
  TURN_OFF_HEATERS
  CANCEL_PRINT_BASE
`

const CROWSNEST = `[crowsnest]
log_path: ~/printer_data/logs/crowsnest.log
log_level: quiet

[cam 1]
mode: ustreamer
port: 8080
device: /dev/video0
resolution: 1280x720
max_fps: 15
`

const EXAMPLE_COREXY = `# This file is an example config file for corexy style printers.
# One may copy and edit this file to configure a new corexy printer.

# DO NOT COPY THIS FILE WITHOUT CAREFULLY READING AND UPDATING IT
# FIRST. Incorrectly configured parameters may cause damage.

# See docs/Config_Reference.md for a description of parameters.

[stepper_x]
step_pin: PF0
dir_pin: PF1
enable_pin: !PD7
microsteps: 16
rotation_distance: 40
endstop_pin: ^PE5
position_endstop: 0
position_max: 200
homing_speed: 50

[printer]
kinematics: corexy
max_velocity: 300
max_accel: 3000
max_z_velocity: 5
max_z_accel: 100
`

const DOC_MD = (title) => `# ${title}

This document is a reference for options available in the Klipper config file.

The descriptions in this document are formatted so that it is possible to cut-and-paste them into a printer config
file.

## Micro-controller configuration

### [mcu]

Configuration of the primary micro-controller.

\`\`\`
[mcu]
serial:
#   The serial port to connect to the MCU.
\`\`\`
`

const LOGO_SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200" viewBox="0 0 320 200"><rect width="320" height="200" rx="18" fill="#161c25"/><circle cx="100" cy="100" r="54" fill="#f2b05a"/><text x="175" y="112" font-family="sans-serif" font-size="34" font-weight="700" fill="#f0f0f0">Klipper</text></svg>`

const STATE_YAML = `schema: 3
package_version: 26.11.16.1
printer: max4
firmware: 01.01.07.01
installed_files:
  - path: tltg-optimized-macros/globals.cfg
    sha256: 0000000000000000000000000000000000000000000000000000000000000000
`

export function install(fake) {
  const handlers = fake.handle({})
  const core = { ...handlers }
  const now = fake.now
  const day = 86400

  /** root -> path -> { kind, modified, size, permissions, content?, orig? } */
  const fs = { gcodes: new Map(), config: new Map(), config_examples: new Map(), docs: new Map() }
  const READONLY = new Set(['config_examples', 'docs'])

  const put = (root, path, node) => {
    const t = fs[root]
    for (let d = dirname(path); d; d = dirname(d)) if (!t.has(d)) t.set(d, { kind: 'dir', modified: node.modified, size: 4096, permissions: READONLY.has(root) ? 'r' : 'rw' })
    t.set(path, node)
  }
  const file = (root, path, content, ageDays = 3, permissions) => put(root, path, { kind: 'file', modified: now() - ageDays * day, size: content == null ? 1200 : Buffer.byteLength(content), permissions: permissions ?? (READONLY.has(root) ? 'r' : 'rw'), content })
  const dir = (root, path, ageDays = 3) => put(root, path, { kind: 'dir', modified: now() - ageDays * day, size: 4096, permissions: READONLY.has(root) ? 'r' : 'rw' })

  // ---- gcodes: the core's files (their metadata comes from the core by original name), plus folders ----
  for (const f of core['server.files.list']()) put('gcodes', f.path, { kind: 'file', modified: f.modified, size: f.size, permissions: 'rw', orig: f.path })
  for (const [path, est, type, age] of [
    ['Box parts/hinge_left.gcode', 1980, 'PETG', 6],
    ['Box parts/hinge_right.gcode', 1990, 'PETG', 6],
    ['Box parts/spool_spacer_x4.gcode.3mf', 3120, 'PLA', 8],
    ['calibration/temperature_tower.gcode.3mf', 2700, 'PLA', 20],
    ['calibration/pressure_advance_test.gcode', 640, 'PLA', 21],
  ])
    put('gcodes', path, { kind: 'file', modified: now() - age * day, size: 180000 + est * 90, permissions: 'rw', extra: { est, type } })
  dir('gcodes', 'Archive', 30)

  // ---- config ----
  file('config', 'printer.cfg', PRINTER_CFG, 0.2)
  file('config', 'MCU_ID.cfg', MCU_CFG, 40)
  file('config', 'box.cfg', BOX_CFG, 1)
  file('config', 'moonraker.conf', MOONRAKER_CONF, 40)
  file('config', 'fluidd.cfg', FLUIDD_CFG, 40, 'r')
  file('config', 'crowsnest.conf', CROWSNEST, 2)
  file('config', 'timelapse.cfg', '[gcode_macro TIMELAPSE_TAKE_FRAME]\ngcode:\n  {action_call_remote_method("timelapse_newframe")}\n', 40)
  file('config', 'saved_variables.cfg', '[Variables]\nfilament_slot0 = \'QFA01\'\ncolor_slot0 = \'1D1D1F\'\nlast_load_slot = \'slot0\'\n', 0.05)
  file('config', 'drying.conf', '[drying]\npla = 50, 8\npetg = 65, 8\nabs = 80, 6\n', 40)
  file('config', 'officiall_filas_list.cfg', '[filament 1]\nfilament = PLA Rapido\ntype = PLA\nmin_temp = 190\nmax_temp = 230\n', 40)
  file('config', '.moonraker.conf.bkp', MOONRAKER_CONF, 40)
  file('config', 'printer-20261117_062049.cfg', PRINTER_CFG, 0.5)
  file('config', 'tltg_optimized_state.yaml', STATE_YAML, 1)
  file('config', 'tltg_optimized_auto_update_state.json', '{\n  "enabled": true,\n  "last_check": "2026-11-16T22:00:00Z"\n}\n', 0.3)
  file('config', 'klipper-macros-qd/start_end.cfg', START_END, 10)
  file('config', 'klipper-macros-qd/filament.cfg', FILAMENT_CFG, 10)
  file('config', 'klipper-macros-qd/globals.cfg', QD_GLOBALS, 10)
  file('config', 'tltg-optimized-macros/globals.cfg', VO_GLOBALS, 1)
  file('config', 'tltg-optimized-macros/cooling.cfg', COOLING, 1)
  file('config', 'tltg-optimized-macros/start_print.cfg', START_END.replace('PRINT_START]', 'VO_PRINT_START]'), 1)
  file('config', 'KAMP/Adaptive_Meshing.cfg', '[gcode_macro _KAMP_Settings]\nvariable_mesh_margin: 0\ngcode:\n', 60)
  // The core serves this one (the Box screen reads it); listed here, content left to it.
  file('config', 'qidi_filament_config/filament-0.4.json', null, 5)
  // ---- read-only roots ----
  file('config_examples', 'example-corexy.cfg', EXAMPLE_COREXY, 400)
  file('config_examples', 'example-cartesian.cfg', EXAMPLE_COREXY.replace(/corexy/g, 'cartesian'), 400)
  file('config_examples', 'sample-macros.cfg', START_END, 400)
  file('config_examples', 'sample-bltouch.cfg', '[bltouch]\nsensor_pin: ^PC14\ncontrol_pin: PB0\n', 400)
  file('docs', 'Config_Reference.md', DOC_MD('Configuration reference'), 400)
  file('docs', 'G-Codes.md', DOC_MD('G-Codes'), 400)
  file('docs', 'Overview.md', DOC_MD('Overview'), 400)
  file('docs', 'img/klipper-logo.svg', LOGO_SVG, 400)

  // ---- helpers ----
  const split = (full) => {
    const s = String(full ?? '').replace(/^\/+|\/+$/g, '')
    const i = s.indexOf('/')
    const root = i < 0 ? s : s.slice(0, i)
    const path = i < 0 ? '' : s.slice(i + 1)
    if (!fs[root]) throw fake.rpcError(`Invalid root request: ${root}`)
    return [root, path]
  }
  const writable = (root, path) => {
    if (READONLY.has(root)) throw fake.rpcError(`Root '${root}' is read-only`)
    const n = fs[root].get(path)
    if (n && !n.permissions.includes('w')) throw fake.rpcError(`File '${root}/${path}' is read-only`)
  }
  const children = (root, path) => [...fs[root].entries()].filter(([p]) => dirname(p) === path && p !== '')
  const below = (root, path) => [...fs[root].keys()].filter((p) => p === path || p.startsWith(`${path}/`))
  const item = (root, path) => {
    const n = fs[root].get(path)
    return { root, path, modified: n?.modified, size: n?.size, permissions: n?.permissions }
  }
  const notify = (action, root, path, source) => fake.broadcast('notify_filelist_changed', [{ action, item: item(root, path), ...(source ? { source_item: { root: source[0], path: source[1] } } : {}) }])
  const diskUsage = (root) => {
    const used = 9.1e9 + [...fs[root].values()].reduce((a, n) => a + (n.kind === 'file' ? n.size : 0), 0)
    return { total: 30654427136, used, free: 30654427136 - used }
  }

  // G-code metadata: the core's by original name, made up for files it doesn't know, with the file's last print.
  function metadata(path) {
    const n = fs.gcodes.get(path)
    if (!n || n.kind !== 'file') throw fake.rpcError(`Metadata not available for <${path}>`)
    let m = null
    if (n.orig) {
      try {
        m = core['server.files.metadata']({ filename: n.orig })
      } catch {}
    }
    if (!m && n.extra) {
      const thumb = `.thumbs/${basename(path).replace(/\.gcode(\.3mf)?$/, '')}-300x300.png`
      m = { slicer: 'QIDIStudio', slicer_version: '02.04.01.11', estimated_time: n.extra.est, layer_height: 0.2, first_layer_height: 0.2, object_height: 22, nozzle_diameter: 0.4, filament_total: n.extra.est * 1.4, filament_weight_total: n.extra.est / 220, filament_type: n.extra.type, filament_name: `QIDI ${n.extra.type} Basic`, filament_colors: ['#8A8F98'], first_layer_extr_temp: n.extra.type === 'PETG' ? 245 : 220, first_layer_bed_temp: n.extra.type === 'PETG' ? 75 : 60, chamber_temp: 0, thumbnails: [{ width: 300, height: 300, size: 30000, relative_path: thumb }] }
    }
    const last = fake.state.history?.find((j) => j.filename === path)
    return { ...(m ?? {}), filename: path, size: n.size, modified: n.modified, print_start_time: last?.start_time ?? null, job_id: last?.job_id ?? null }
  }

  function zip(entries) {
    const local = []
    const central = []
    let offset = 0
    for (const [name, data] of entries) {
      const raw = Buffer.isBuffer(data) ? data : Buffer.from(data)
      const packed = deflateRawSync(raw)
      const nameBuf = Buffer.from(name)
      const crc = crc32(raw)
      const head = Buffer.alloc(30)
      head.writeUInt32LE(0x04034b50, 0)
      head.writeUInt16LE(20, 4)
      head.writeUInt16LE(8, 8)
      head.writeUInt32LE(crc, 14)
      head.writeUInt32LE(packed.length, 18)
      head.writeUInt32LE(raw.length, 22)
      head.writeUInt16LE(nameBuf.length, 26)
      local.push(head, nameBuf, packed)
      const cd = Buffer.alloc(46)
      cd.writeUInt32LE(0x02014b50, 0)
      cd.writeUInt16LE(20, 4)
      cd.writeUInt16LE(20, 6)
      cd.writeUInt16LE(8, 10)
      cd.writeUInt32LE(crc, 16)
      cd.writeUInt32LE(packed.length, 20)
      cd.writeUInt32LE(raw.length, 24)
      cd.writeUInt16LE(nameBuf.length, 28)
      cd.writeUInt32LE(offset, 42)
      central.push(cd, nameBuf)
      offset += 30 + nameBuf.length + packed.length
    }
    const cdBuf = Buffer.concat(central)
    const end = Buffer.alloc(22)
    end.writeUInt32LE(0x06054b50, 0)
    end.writeUInt16LE(entries.length, 8)
    end.writeUInt16LE(entries.length, 10)
    end.writeUInt32LE(cdBuf.length, 12)
    end.writeUInt32LE(offset, 16)
    return Buffer.concat([...local, cdBuf, end])
  }
  const contentOf = (root, path, n) => n.content ?? (root === 'gcodes' ? `; ${path}\n; generated by the fake Moonraker\nG28\nG1 Z0.2 F600\nG1 X100 Y100 E10 F1500\nM104 S0\n` : '')

  // ---- RPC ----
  const listDir = (p) => {
    const [root, path] = split(p.path ?? 'gcodes')
    if (path && fs[root].get(path)?.kind !== 'dir') throw fake.rpcError(`Directory does not exist (${root}/${path})`)
    const dirs = []
    const files = []
    for (const [full, n] of children(root, path)) {
      if (n.kind === 'dir') dirs.push({ modified: n.modified, size: 4096, permissions: n.permissions, dirname: basename(full) })
      else {
        let extra = {}
        if (p.extended && root === 'gcodes') {
          const { filename: _f, size: _s, modified: _m, ...rest } = metadata(full)
          extra = rest
        }
        files.push({ modified: n.modified, size: n.size, permissions: n.permissions, filename: basename(full), ...extra })
      }
    }
    // The gcodes root has a .thumbs folder on a real printer.
    if (root === 'gcodes' && !path) dirs.push({ modified: now() - day, size: 4096, permissions: 'rw', dirname: '.thumbs' })
    return { dirs, files, disk_usage: diskUsage(root), root_info: { name: root, permissions: READONLY.has(root) ? 'r' : 'rw' } }
  }

  function moveOrCopy(p, copy) {
    const [sr, sp] = split(p.source)
    const [dr, dp] = split(p.dest)
    const src = fs[sr].get(sp)
    if (!src) throw fake.rpcError(`File or directory does not exist: ${p.source}`)
    if (!copy) writable(sr, sp)
    writable(dr, dp)
    const existing = fs[dr].get(dp)
    if (existing?.kind === 'dir' || (existing && src.kind === 'dir')) throw fake.rpcError(`Destination path already exists: ${p.dest}`)
    for (const path of below(sr, sp)) {
      const n = fs[sr].get(path)
      const to = dp + path.slice(sp.length)
      put(dr, to, { ...n, modified: copy ? now() : n.modified, permissions: dr === sr ? n.permissions : 'rw' })
      if (!copy) fs[sr].delete(path)
    }
    const action = `${copy ? 'create' : 'move'}_${src.kind === 'dir' ? 'dir' : 'file'}`
    notify(action, dr, dp, copy ? null : [sr, sp])
    return { item: item(dr, dp), ...(copy ? {} : { source_item: { root: sr, path: sp } }), action }
  }

  fake.handle({
    'server.files.get_directory': (p) => (fs[String(p.path ?? 'gcodes').split('/')[0]] ? listDir(p) : core['server.files.get_directory']?.(p)),
    'server.files.list': (p) => {
      const root = p.root ?? 'gcodes'
      if (!fs[root]) return core['server.files.list'](p)
      return [...fs[root].entries()].filter(([, n]) => n.kind === 'file').map(([path, n]) => ({ path, modified: n.modified, size: n.size, permissions: n.permissions }))
    },
    'server.files.metadata': (p) => metadata(p.filename),
    'server.files.metascan': (p) => metadata(p.filename),
    'server.files.move': (p) => moveOrCopy(p, false),
    'server.files.copy': (p) => moveOrCopy(p, true),
    'server.files.delete_file': (p) => {
      const [root, path] = split(String(p.path).includes('/') ? p.path : `gcodes/${p.path}`)
      if (fs[root].get(path)?.kind !== 'file') throw fake.rpcError(`File does not exist: ${p.path}`)
      writable(root, path)
      const before = item(root, path)
      fs[root].delete(path)
      fake.broadcast('notify_filelist_changed', [{ action: 'delete_file', item: before }])
      return { item: before, action: 'delete_file' }
    },
    'server.files.delete_directory': (p) => {
      const [root, path] = split(p.path)
      if (!path || fs[root].get(path)?.kind !== 'dir') throw fake.rpcError(`Directory does not exist: ${p.path}`)
      writable(root, path)
      const inside = below(root, path)
      if (inside.length > 1 && !p.force) throw fake.rpcError(`Directory not empty: ${p.path}`)
      const before = item(root, path)
      for (const x of inside) fs[root].delete(x)
      fake.broadcast('notify_filelist_changed', [{ action: 'delete_dir', item: before }])
      return { item: before, action: 'delete_dir' }
    },
    'server.files.post_directory': (p) => {
      const [root, path] = split(p.path)
      writable(root, path)
      if (fs[root].has(path)) throw fake.rpcError(`Directory already exists: ${p.path}`)
      dir(root, path, 0)
      notify('create_dir', root, path)
      return { item: item(root, path), action: 'create_dir' }
    },
    'server.files.zip': async (p) => {
      const [root, dest] = split(p.dest)
      writable(root, dest)
      const entries = []
      for (const it of p.items ?? []) {
        const [r, path] = split(it)
        for (const x of below(r, path)) {
          const n = fs[r].get(x)
          if (n.kind === 'file') entries.push([x.slice(dirname(path) ? dirname(path).length + 1 : 0), contentOf(r, x, n)])
        }
      }
      await new Promise((r) => setTimeout(r, 600))
      const data = zip(entries)
      put(root, dest, { kind: 'file', modified: now(), size: data.length, permissions: 'rw', content: data })
      notify('create_file', root, dest)
      return { destination: item(root, dest), action: 'zip_files' }
    },
  })

  // server.info: the roots and the job queue the real printers have. Kept as a property so a plugin loaded later that
  // replaces server.info outright still gets them added; one that wraps it gets this wrapper as its `next`.
  const withRoots = (r) => ({ ...r, components: [...new Set([...(r.components ?? []), 'file_manager', 'job_queue'])], registered_directories: [...new Set([...(r.registered_directories ?? []), 'config', 'logs', 'gcodes', 'config_examples', 'docs'])] })
  let info = handlers['server.info']
  Object.defineProperty(handlers, 'server.info', {
    enumerable: true,
    configurable: true,
    get: () => {
      const impl = info
      return async (p, ws) => withRoots(await impl(p, ws))
    },
    set: (fn) => {
      info = fn
    },
  })

  if (!handlers['server.job_queue.post_job']) {
    const queue = []
    let qid = 1
    const status = () => ({ queued_jobs: queue, queue_state: 'paused' })
    const changed = (action) => fake.broadcast('notify_job_queue_changed', [{ action, updated_queue: queue, queue_state: 'paused' }])
    fake.handle({
      'server.job_queue.status': status,
      'server.job_queue.post_job': (p) => {
        for (const filename of [].concat(p.filenames ?? p.filename ?? [])) queue.push({ filename, job_id: String(qid++).padStart(4, '0'), time_added: now(), time_in_queue: 0 })
        changed('jobs_added')
        return status()
      },
      'server.job_queue.delete_job': (p) => {
        const ids = p.all ? queue.map((j) => j.job_id) : [].concat(p.job_ids ?? [])
        for (const id of ids) queue.splice(queue.findIndex((j) => j.job_id === id) >>> 0, 1)
        changed('jobs_removed')
        return status()
      },
    })
  }
  if (!handlers['machine.system_info'])
    fake.handle({
      'machine.system_info': () => ({ system_info: { available_services: ['klipper', 'moonraker', 'crowsnest', 'klipper-mcu', 'qidi-client'], instance_ids: { moonraker: 'moonraker', klipper: 'klipper' }, service_state: { klipper: { active_state: 'active', sub_state: 'running' }, moonraker: { active_state: 'active', sub_state: 'running' }, crowsnest: { active_state: 'active', sub_state: 'running' } } } }),
    })
  if (!handlers['machine.services.restart'])
    fake.handle({
      'machine.services.restart': (p) => {
        fake.pushConsole(`// Restarting the ${p.service} service (fake).`)
        return 'ok'
      },
    })

  // ---- HTTP: uploads and downloads ----
  const MIME = { '.cfg': 'text/plain', '.conf': 'text/plain', '.md': 'text/markdown', '.json': 'application/json', '.yaml': 'text/plain', '.svg': 'image/svg+xml', '.zip': 'application/zip', '.gcode': 'text/plain' }
  const send = (res, root, path, n) => {
    const data = contentOf(root, path, n)
    const ext = path.slice(path.lastIndexOf('.'))
    res.writeHead(200, { 'Content-Type': MIME[ext] ?? 'application/octet-stream', 'Content-Disposition': `attachment; filename="${encodeURIComponent(basename(path))}"`, 'Cache-Control': 'no-store', 'Access-Control-Expose-Headers': 'Content-Disposition' })
    res.end(data)
  }

  let fallbackAdded = false
  fake.http(async (req, res, path) => {
    // G-code without uploaded content: anything later (the G-code preview plugin) answers first; this is the last resort.
    if (!fallbackAdded) {
      fallbackAdded = true
      fake.http(async (req2, res2, path2) => {
        if (req2.method !== 'GET' || !path2.startsWith('/server/files/gcodes/') || path2.includes('/.thumbs/')) return false
        const p = path2.slice('/server/files/gcodes/'.length)
        const n = fs.gcodes.get(p)
        if (!n || n.kind !== 'file') return false
        send(res2, 'gcodes', p, n)
        return true
      })
    }
    if (req.method === 'OPTIONS' && path.startsWith('/server/files/')) {
      res.writeHead(204, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET, POST, DELETE', 'Access-Control-Allow-Headers': '*' })
      return res.end(), true
    }
    if (req.method === 'POST' && path === '/server/files/upload') {
      const chunks = []
      for await (const c of req) chunks.push(c)
      const body = Buffer.concat(chunks)
      const boundary = /boundary=(?:"([^"]+)"|([^;]+))/.exec(req.headers['content-type'] ?? '')
      if (!boundary) return res.writeHead(400).end('Expected multipart/form-data'), true
      const sep = Buffer.from(`--${boundary[1] ?? boundary[2]}`)
      const fields = {}
      let upload = null
      let at = body.indexOf(sep)
      while (at >= 0) {
        const next = body.indexOf(sep, at + sep.length)
        if (next < 0) break
        const part = body.subarray(at + sep.length + 2, next - 2)
        const split = part.indexOf('\r\n\r\n')
        const head = part.subarray(0, split).toString()
        const data = part.subarray(split + 4)
        const name = /name="([^"]+)"/.exec(head)?.[1]
        const filename = /filename="([^"]*)"/.exec(head)?.[1]
        if (filename != null) upload = { filename, data }
        else if (name) fields[name] = data.toString()
        at = next
      }
      const root = fields.root ?? 'gcodes'
      const send400 = (msg) => (res.writeHead(400, { 'Content-Type': 'application/json' }).end(JSON.stringify({ error: { code: 400, message: msg } })), true)
      if (!upload) return send400('No file in the upload')
      if (!fs[root]) return send400(`Invalid root request: ${root}`)
      const dest = [fields.path?.replace(/^\/+|\/+$/g, ''), upload.filename].filter(Boolean).join('/')
      try {
        writable(root, dest)
      } catch (e) {
        res.writeHead(403, { 'Content-Type': 'application/json' }).end(JSON.stringify({ error: { code: 403, message: e.message } }))
        return true
      }
      const existed = fs[root].has(dest)
      await new Promise((r) => setTimeout(r, 300))
      put(root, dest, { kind: 'file', modified: now(), size: upload.data.length, permissions: 'rw', content: upload.data })
      notify(existed ? 'modify_file' : 'create_file', root, dest)
      let started = false
      if (root === 'gcodes' && fields.print === 'true') {
        try {
          handlers['printer.print.start']({ filename: dest })
          started = true
        } catch {}
      }
      res.writeHead(201, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ item: item(root, dest), print_started: started, print_queued: false, action: existed ? 'modify_file' : 'create_file' }))
      return true
    }
    if (req.method === 'GET' && path.startsWith('/server/files/') && !path.includes('/.thumbs/')) {
      const rest = path.slice('/server/files/'.length)
      const root = rest.split('/')[0]
      const p = rest.slice(root.length + 1)
      const n = fs[root]?.get(p)
      // Unknown files, and G-code without uploaded content, go on to the other routes.
      if (!n || n.kind !== 'file' || n.content === null || (root === 'gcodes' && n.content === undefined)) return false
      send(res, root, p, n)
      return true
    }
    return false
  })
}

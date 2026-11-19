// System screen: host details, MCUs, services, logs, Moonraker's database and backups, connected devices, and the
// touchscreen stream (tltg-remote-screen) at /tltg-screen/ws. Shaped like a Max 4; every identifier here is made up.
import { deflateSync } from 'node:zlib'

export function install(fake) {
  const { state, set, broadcast, now } = fake

  // ---- machine.system_info ----
  const services = {
    moonraker: { active_state: 'active', sub_state: 'running' },
    crowsnest: { active_state: 'active', sub_state: 'running' },
    'klipper-mcu': { active_state: 'active', sub_state: 'running' },
    klipper: { active_state: 'active', sub_state: 'running' },
    'qidi-client': { active_state: 'active', sub_state: 'running' },
  }
  const systemInfo = () => ({
    python: { version: [3, 9, 2, 'final', 0], version_string: '3.9.2 (default, Feb 28 2021, 17:03:44)  [GCC 10.2.1 20210110]' },
    cpu_info: { cpu_count: 4, bits: '64bit', processor: 'aarch64', cpu_desc: '', serial_number: '0000000000000000', hardware_desc: '', model: '', total_memory: 498620, memory_units: 'kB' },
    sd_info: {},
    distribution: { name: 'Debian GNU/Linux 11 (bullseye)', id: 'debian', version: '11', version_parts: { major: '11', minor: '', build_number: '' }, like: '', codename: 'bullseye', release_info: {}, kernel_version: '5.10.160' },
    virtualization: { virt_type: 'none', virt_identifier: 'none' },
    network: {
      eth0: {
        mac_address: '02:00:00:00:00:01',
        ip_addresses: [
          { family: 'ipv4', address: '192.168.1.50', is_link_local: false },
          { family: 'ipv6', address: 'fe80::1', is_link_local: true },
        ],
      },
    },
    canbus: {},
    provider: 'systemd_dbus',
    available_services: Object.keys(services),
    service_state: structuredClone(services),
    machine_name: 'QIDI',
    instance_ids: { moonraker: 'moonraker', klipper: 'klipper' },
  })

  const changeService = (name, steps) => {
    let delay = 0
    for (const [active_state, sub_state, wait] of steps) {
      delay += wait
      setTimeout(() => {
        services[name] = { active_state, sub_state }
        broadcast('notify_service_state_changed', [{ [name]: services[name] }])
      }, delay)
    }
  }
  const service = (p) => {
    if (!services[p.service]) throw fake.rpcError(`Invalid argument: ${p.service}`)
    return p.service
  }

  // ---- logs ----
  const day = (d) => new Date((now() - d * 86400) * 1000).toISOString().slice(0, 10)
  const stamp = (t) => new Date(t * 1000).toISOString().replace('T', ' ').slice(0, 19)
  const klippyLines = (n, t0) => {
    const out = ['Starting Klippy...', 'Args: [\'/home/qidi/klipper/klippy/klippy.py\', \'/home/qidi/printer_data/config/printer.cfg\', \'-l\', \'/home/qidi/printer_data/logs/klippy.log\']', 'Git version: \'v0.12.0-fake\'', '===== Config file =====', '[mcu]', 'serial = /dev/ttyFAKE0', 'restart_method = command', '=======================', 'Loaded MCU \'mcu\' 112 commands (02.01.01.12-valve-oil.1 / gcc: (GNU Arm Embedded Toolchain 10.3-2021.10) 10.3.1)']
    for (let i = 0; i < n; i++) {
      const t = t0 + i
      if (i % 97 === 50) out.push(`Box: slot1 load finished in 31.${i % 10}s`)
      if (i % 311 === 200) out.push('!! Move out of range: 412.000 195.000 30.000 [0.000]')
      out.push(`Stats ${(t % 100000).toFixed(1)}: gcodein=0  mcu: mcu_awake=0.031 mcu_task_avg=0.000026 mcu_task_stddev=0.000018 bytes_write=${4229731 + i * 90} bytes_read=${1525045 + i * 30} send_seq=${95660 + i} srtt=0.001 freq=168005612 THR: mcu_awake=0.012 sysload=0.65 cputime=${(738 + i * 0.25).toFixed(3)} memavail=209556 print_time=${(1500 + i).toFixed(3)} buffer_time=0.000 print_stall=0 extruder: target=0 temp=32.0 pwm=0.000`)
    }
    return out.join('\n') + '\n'
  }
  const moonrakerLines = (n, t0) => {
    const msgs = ['Websocket Request::server.files.get_directory', 'Client connected: Valve Oil UI', 'Update Timer: running', 'Job Queue: idle', 'File Manager: Metadata extraction complete', 'Websocket Request::machine.system_info']
    return Array.from({ length: n }, (_, i) => `${stamp(t0 + i * 7)},${String(i % 1000).padStart(3, '0')} [${i % 4 ? 'websockets.py:on_message()' : 'application.py:log_request()'}] - ${msgs[i % msgs.length]}`).join('\n') + '\n'
  }
  const logs = new Map()
  const addLog = (filename, content, ageSeconds) => logs.set(filename, { content: Buffer.from(content), modified: now() - ageSeconds })
  addLog('klippy.log', klippyLines(4000, now() - 4000), 1)
  addLog(`klippy.log.${day(2)}`, klippyLines(1500, now() - 86400 * 2), 86400 * 2)
  addLog(`klippy.log.${day(4)}`, klippyLines(900, now() - 86400 * 4), 86400 * 4)
  addLog('moonraker.log', moonrakerLines(1200, now() - 9000), 3)
  addLog(`moonraker.log.${day(1)}`, moonrakerLines(800, now() - 86400), 86400)
  // installer/runtime/run_log.py: each install, update, uninstall, and backup restore keeps a log here.
  const runLog = (mode, ageSeconds, steps) => {
    const d = new Date((now() - ageSeconds) * 1000)
    const pad = (n) => String(n).padStart(2, '0')
    const name = `valve-oil-${mode}-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}.log`
    addLog(name, steps.map((step, i) => `${new Date((now() - ageSeconds + i * 40) * 1000).toISOString()} ${step}`).join('\n') + '\n', ageSeconds)
  }
  runLog('install', 86400 * 2 + 3600, ['install: Valve Oil 26.11.11.3 (from 26.11.11.2), started automatic', 'step 1/6: Check the printer', 'step 2/6: Back up your configuration', 'step 3/6: Macros and Klipper fixes', 'step 4/6: System changes', 'step 5/6: Restart Klipper and verify', 'step 6/6: Finish', 'Installed Valve Oil 26.11.11.3.'])
  runLog('usb', 86400 * 6, ['usb: Valve Oil 26.11.11.2, started from a USB drive', 'step 1/5: Check the printer', 'step 2/5: Macros and Klipper fixes', 'step 3/5: System changes', 'step 4/5: Restart Klipper and verify', 'step 5/5: Finish', 'Installed Valve Oil 26.11.11.2.'])
  fake.addLog = addLog
  addLog('crowsnest.log', '[cs] crowsnest - A webcam Service for multiple Cams and Stream Services.\n[cs] V4L2 Control: Device /dev/video0 found\n[cs] ustreamer: Streaming on port 8080\n', 3600)
  addLog('tltg_box_link.log', 'box link: connected to Box 1 (USB)\nbox link: connected to Box 2 (USB)\nbox link: heartbeat ok\n', 60)
  addLog('tltg_boot_animation.log', 'boot animation installed\n', 86400 * 3)
  // The report the installer leaves for a Box it can't support yet, which the updates scenario `boards` warns about.
  addLog('tltg_box_firmware_box1.json', JSON.stringify({ board: 'Box 1', application: 'unrecognized', supported_bootloader: false, bootloader_sha256: '0'.repeat(64), application_sha256: '1'.repeat(64), version: '1.0.2' }, null, 2) + '\n', 600)
  setInterval(() => {
    const k = logs.get('klippy.log')
    const t = now()
    k.content = Buffer.concat([k.content, Buffer.from(`Stats ${(t % 100000).toFixed(1)}: gcodein=0  mcu: mcu_awake=0.0${30 + Math.floor(Math.random() * 9)} sysload=0.6${Math.floor(Math.random() * 9)} cputime=${state.status.system_stats?.cputime?.toFixed(3) ?? '0'} memavail=209556\n`)])
    k.modified = t
  }, 1000)

  const logListing = () => ({
    dirs: [],
    files: [...logs].map(([filename, l]) => ({ filename, size: l.content.length, modified: l.modified, permissions: 'r' })),
    disk_usage: { total: 7.2e9, used: 3.1e9, free: 4.1e9 },
    root_info: { name: 'logs', permissions: 'r' },
  })

  // ---- database backups: snapshots of the in-memory database another plugin keeps in state.database ----
  const db = (state.database ??= {})
  const backups = new Map([
    ['20261110-112450.db', structuredClone(db)],
    ['20261103-015528.db', structuredClone(db)],
  ])
  const BUILTIN_NAMESPACES = ['moonraker', 'gcode_metadata', 'history', 'announcements', 'webcams', 'timelapse', 'fluidd']

  // ---- devices (made up) ----
  const peripherals = {
    serial_devices: [
      { device_type: 'hardware_uart', device_path: '/dev/ttyS2', device_name: 'ttyS2', driver_name: 'dw-apb-uart', path_by_hardware: null, path_by_id: null, usb_location: null },
      { device_type: 'usb', device_path: '/dev/ttyACM0', device_name: 'ttyACM0', driver_name: 'cdc_acm', path_by_hardware: '/dev/serial/by-path/platform-fake-usb-0:1.1:1.0', path_by_id: '/dev/serial/by-id/usb-Klipper_rp2040_FAKE000000000001-if00', usb_location: '1:2' },
    ],
    usb_devices: [
      { device_num: 2, bus_num: 1, vendor_id: '1d50', product_id: '614e', usb_location: '1:2', manufacturer: 'Klipper', product: 'rp2040', class: 'Communications', subclass: null, protocol: null, description: 'OpenMoko, Inc. Klipper 3d-Printer Firmware', serial_number: 'FAKE000000000001' },
      { device_num: 3, bus_num: 1, vendor_id: '0c45', product_id: '6366', usb_location: '1:3', manufacturer: 'Generic', product: 'USB Camera', class: 'Miscellaneous Device', subclass: null, protocol: null, description: 'Microdia Webcam Vitade AF', serial_number: null },
    ],
    v4l2_devices: [{ device_name: 'video0', device_path: '/dev/video0', camera_name: 'USB Camera', driver_name: 'uvcvideo', hardware_bus: 'usb-fake-1.3', modes: [], capabilities: ['VIDEO_CAPTURE', 'STREAMING'], version: '5.10.160', alt_name: null }],
    libcamera_devices: [],
  }

  // Chain onto whatever answers get_directory for the other roots (core or a Files plugin loaded earlier).
  const handlers = fake.handle({})
  const prevGetDirectory = handlers['server.files.get_directory']

  fake.handle({
    'machine.system_info': () => ({ system_info: systemInfo() }),
    'machine.services.restart': (p) => {
      const name = service(p)
      changeService(name, [
        ['deactivating', 'stop-sigterm', 50],
        ['activating', 'start', 600],
        ['active', 'running', 1200],
      ])
      fake.pushConsole(`// Restarting ${name}`)
      return 'ok'
    },
    'machine.services.stop': (p) => {
      const name = service(p)
      changeService(name, [
        ['deactivating', 'stop-sigterm', 50],
        ['inactive', 'dead', 800],
      ])
      return 'ok'
    },
    'machine.services.start': (p) => {
      const name = service(p)
      changeService(name, [
        ['activating', 'start', 50],
        ['active', 'running', 900],
      ])
      return 'ok'
    },
    'machine.reboot': () => (fake.pushConsole('// Fake host reboot requested; nothing happens'), 'ok'),
    'machine.shutdown': () => (fake.pushConsole('// Fake host shutdown requested; nothing happens'), 'ok'),
    'machine.peripherals.serial': () => ({ serial_devices: peripherals.serial_devices }),
    'machine.peripherals.usb': () => ({ usb_devices: peripherals.usb_devices }),
    'machine.peripherals.video': () => ({ v4l2_devices: peripherals.v4l2_devices, libcamera_devices: peripherals.libcamera_devices }),
    'server.files.get_directory': (p, ws) => {
      const path = String(p.path ?? 'gcodes').replace(/\/+$/, '')
      if (path === 'logs') return logListing()
      return prevGetDirectory ? prevGetDirectory(p, ws) : { dirs: [], files: [], disk_usage: { total: 7.2e9, used: 3.1e9, free: 4.1e9 }, root_info: { name: path, permissions: 'rw' } }
    },
    'server.logs.rollover': (p) => {
      const apps = p.application ? [p.application] : ['klipper', 'moonraker']
      const rolled = []
      for (const app of apps) {
        const file = app === 'klipper' ? 'klippy.log' : 'moonraker.log'
        const old = logs.get(file)
        let name = `${file}.${day(0)}`
        for (let i = 1; logs.has(name); i++) name = `${file}.${day(0)}.${i}`
        logs.set(name, { ...old })
        addLog(file, app === 'klipper' ? klippyLines(5, now() - 5) : moonrakerLines(3, now() - 20), 0)
        rolled.push(app)
      }
      return { rolled_over: rolled, failed: {} }
    },
    'server.database.list': () => ({ namespaces: [...new Set([...BUILTIN_NAMESPACES, ...Object.keys(db)])], backups: [...backups.keys()] }),
    'server.database.post_backup': (p) => {
      const name = p.filename ?? `backup-${Date.now()}.db`
      backups.set(name, structuredClone(db))
      return { backup_path: `/home/qidi/printer_data/backup/database/${name}` }
    },
    'server.database.restore': (p) => {
      const snap = backups.get(p.filename)
      if (!snap) throw fake.rpcError(`Backup file ${p.filename} does not exist`)
      for (const k of Object.keys(db)) delete db[k]
      Object.assign(db, structuredClone(snap))
      return { restored_tables: ['namespace_store', 'job_history'], restored_namespaces: Object.keys(db) }
    },
    'server.database.delete_backup': (p) => {
      if (!backups.delete(p.filename)) throw fake.rpcError(`Backup file ${p.filename} does not exist`)
      return { backup_path: `/home/qidi/printer_data/backup/database/${p.filename}` }
    },
    'server.database.compact': () => ({ previous_size: 2015232, new_size: 1474560 }),
    'access.oneshot_token': () => 'fake-oneshot-token',
  })

  // ---- Klipper objects: MCUs in full, and system_stats ----
  const CONSTANTS = {
    mcu: { ADC_MAX: 4095, BUS_PINS_i2c1: 'PB6,PB7', BUS_PINS_spi1: 'PA6,PA7,PA5', CLOCK_FREQ: 168000000, MCU: 'stm32f407xx', PWM_MAX: 255, RESERVE_PINS_USB: 'PA11,PA12', RESERVE_PINS_crystal: 'PH0,PH1', STATS_SUMSQ_BASE: 256, STEPPER_BOTH_EDGE: 1 },
    'mcu THR': { ADC_MAX: 4095, CLOCK_FREQ: 12000000, MCU: 'rp2040', PWM_MAX: 255, STATS_SUMSQ_BASE: 256, STEPPER_BOTH_EDGE: 1 },
    'mcu mcu_box1': { ADC_MAX: 4095, CLOCK_FREQ: 72000000, MCU: 'stm32f103xe', PWM_MAX: 255, STATS_SUMSQ_BASE: 256 },
    'mcu mcu_box2': { ADC_MAX: 4095, CLOCK_FREQ: 72000000, MCU: 'stm32f103xe', PWM_MAX: 255, STATS_SUMSQ_BASE: 256 },
  }
  const VERSIONS = { mcu: '02.01.01.12-valve-oil.1', 'mcu THR': '00.00.06.00-valve-oil.1', 'mcu mcu_box1': '01.01.00.13', 'mcu mcu_box2': '01.01.00.13' }
  const BUILD = 'gcc: (GNU Arm Embedded Toolchain 10.3-2021.10) 10.3.1 20210824 (release) binutils: (GNU Arm Embedded Toolchain 10.3-2021.10) 2.36.1.20210621'
  const lastStats = (base, i) => ({
    mcu_awake: Math.round((base + Math.random() * base * 0.4) * 1000) / 1000,
    mcu_task_avg: 0.000015 + Math.random() * 0.00002,
    mcu_task_stddev: 0.00001 + Math.random() * 0.00001,
    bytes_write: 4229731 + i * 900,
    bytes_read: 1525045 + i * 300,
    bytes_retransmit: 9,
    bytes_invalid: 0,
    send_seq: 95660 + i * 10,
    receive_seq: 95660 + i * 10,
    retransmit_seq: 2,
    srtt: 0.001,
    rttvar: 0,
    rto: 0.025,
    ready_bytes: 0,
    upcoming_bytes: 0,
    freq: 168005612,
  })
  const AWAKE = { mcu: 0.035, 'mcu THR': 0.012, 'mcu mcu_box1': 0.004, 'mcu mcu_box2': 0.004 }
  let cputime = 738.98
  fake.status((st, scenario) => {
    for (const name of Object.keys(CONSTANTS)) {
      if (!st[name] && scenario === 'no-box' && name.includes('box')) continue
      st[name] = { ...st[name], mcu_version: VERSIONS[name], mcu_build_versions: BUILD, mcu_constants: CONSTANTS[name], last_stats: lastStats(AWAKE[name], 0) }
    }
    st.system_stats = { sysload: 0.65, cputime, memavail: 209556 }
  })
  let tick = 0
  setInterval(() => {
    tick++
    cputime += 0.2 + Math.random() * 0.15
    if (state.status.system_stats) set('system_stats', { sysload: Math.round((0.5 + Math.random() * 0.4) * 100) / 100, cputime, memavail: 200000 + Math.round(Math.random() * 20000) })
    for (const name of Object.keys(CONSTANTS)) if (state.status[name]) set(name, { last_stats: lastStats(AWAKE[name], tick) })
    if (tick % 5 === 0 && state.status.tltg_klipper_health) set('tltg_klipper_health', { timers: 58 + Math.floor(Math.random() * 25) })
  }, 1000)

  // ---- HTTP: log files, with range requests as Moonraker answers them ----
  fake.http(async (req, res, path) => {
    if (!path.startsWith('/server/files/logs/')) return false
    if (req.method === 'OPTIONS') {
      res.writeHead(204, { 'Access-Control-Allow-Headers': 'Range', 'Access-Control-Allow-Methods': 'GET, OPTIONS' }).end()
      return true
    }
    const log = logs.get(path.slice('/server/files/logs/'.length))
    if (!log) return res.writeHead(404).end('not found'), true
    const body = log.content
    const headers = { 'Content-Type': 'text/plain', 'Accept-Ranges': 'bytes', 'Access-Control-Expose-Headers': 'Content-Range', 'Content-Disposition': `attachment; filename="${path.split('/').pop()}"` }
    const m = /^bytes=(\d*)-(\d*)$/.exec(req.headers.range ?? '')
    if (m) {
      let start = m[1] === '' ? Math.max(0, body.length - Number(m[2])) : Number(m[1])
      let end = m[1] !== '' && m[2] !== '' ? Math.min(Number(m[2]), body.length - 1) : body.length - 1
      if (start >= body.length) return res.writeHead(416, { ...headers, 'Content-Range': `bytes */${body.length}` }).end(), true
      res.writeHead(206, { ...headers, 'Content-Range': `bytes ${start}-${end}/${body.length}`, 'Content-Length': end - start + 1 })
      return res.end(body.subarray(start, end + 1)), true
    }
    res.writeHead(200, { ...headers, 'Content-Length': body.length })
    return res.end(body), true
  })

  // ---- the touchscreen stream ----
  fake.websocket('/tltg-screen/ws', (ws) => onScreen(ws))
  const touches = []
  function onScreen(ws) {
    ws.send(JSON.stringify({ type: 'hello', width: SCREEN_W, height: SCREEN_H, touch: true, touch_error: null }))
    const send = () => ws.readyState === 1 && ws.send(drawScreen(touches))
    send()
    const t = setInterval(send, 1000)
    ws.on('message', (raw) => {
      let m
      try {
        m = JSON.parse(raw.toString())
      } catch {
        return
      }
      if (m.type !== 'touch') return
      if (m.action !== 'up') touches.push({ x: m.x, y: m.y, at: Date.now(), down: m.action === 'down' })
      if (m.action === 'down') console.log(`[screen] touch at ${m.x},${m.y}`)
      while (touches.length > 40) touches.shift()
      send()
    })
    ws.on('close', () => clearInterval(t))
  }
}

// ---- a generated touchscreen image, as a PNG ----
const SCREEN_W = 800
const SCREEN_H = 480

function drawScreen(touches) {
  const px = Buffer.alloc(SCREEN_W * SCREEN_H * 3)
  const rect = (x, y, w, h, [r, g, b]) => {
    for (let j = Math.max(0, y); j < Math.min(SCREEN_H, y + h); j++)
      for (let i = Math.max(0, x); i < Math.min(SCREEN_W, x + w); i++) {
        const o = (j * SCREEN_W + i) * 3
        px[o] = r
        px[o + 1] = g
        px[o + 2] = b
      }
  }
  const circle = (cx, cy, rad, c) => {
    for (let j = -rad; j <= rad; j++) for (let i = -rad; i <= rad; i++) if (i * i + j * j <= rad * rad) rect(cx + i, cy + j, 1, 1, c)
  }
  const BG = [13, 17, 23]
  const CARD = [22, 28, 37]
  const LINE = [43, 52, 64]
  const AMBER = [242, 176, 90]
  const INK = [240, 240, 240]
  const DIM = [111, 122, 136]
  rect(0, 0, SCREEN_W, SCREEN_H, BG)
  // left rail with five buttons, the first one active
  rect(0, 0, 84, SCREEN_H, [9, 12, 17])
  for (let k = 0; k < 5; k++) rect(14, 20 + k * 88, 56, 56, k === 0 ? [60, 46, 26] : CARD), rect(30, 36 + k * 88, 24, 24, k === 0 ? AMBER : DIM)
  // three temperature tiles with bars that move
  const t = Date.now() / 1000
  for (let k = 0; k < 3; k++) {
    const x = 104 + k * 228
    rect(x, 24, 212, 120, CARD)
    rect(x, 24, 212, 1, LINE)
    rect(x + 16, 40, 60, 10, DIM)
    const level = 0.35 + 0.3 * Math.sin(t / 7 + k)
    rect(x + 16, 112, 180, 12, LINE)
    rect(x + 16, 112, Math.round(180 * level), 12, k === 0 ? AMBER : INK)
    sevenSeg(rect, x + 16, 62, String(Math.round(30 + level * 200)).padStart(3, ' '), INK, 3)
  }
  // a clock and a progress bar
  rect(104, 164, 668, 152, CARD)
  const d = new Date()
  const clock = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
  sevenSeg(rect, 128, 188, clock, AMBER, 5)
  rect(128, 284, 620, 14, LINE)
  rect(128, 284, Math.round(620 * ((t % 120) / 120)), 14, AMBER)
  // buttons
  for (let k = 0; k < 3; k++) rect(104 + k * 228, 340, 212, 112, k === 2 ? [70, 30, 38] : CARD), rect(104 + k * 228 + 76, 380, 60, 32, k === 2 ? [200, 74, 90] : DIM)
  // where it was touched, fading
  for (const tp of touches) {
    const age = (Date.now() - tp.at) / 1000
    if (age > 6) continue
    circle(tp.x, tp.y, tp.down ? 12 : 4, age < 2 ? AMBER : DIM)
  }
  return png(px)
}

// Seven-segment digits, so the image changes every second without a font.
const SEGS = { 0: 'abcdef', 1: 'bc', 2: 'abdeg', 3: 'abcdg', 4: 'bcfg', 5: 'acdfg', 6: 'acdefg', 7: 'abc', 8: 'abcdefg', 9: 'abcdfg', ' ': '' }
function sevenSeg(rect, x, y, text, c, s) {
  const w = 8 * s
  const h = 14 * s
  for (const ch of text) {
    if (ch === ':') {
      rect(x + s, Math.round(y + h / 3), s * 2, s * 2, c)
      rect(x + s, Math.round(y + (2 * h) / 3), s * 2, s * 2, c)
      x += s * 5
      continue
    }
    const on = SEGS[ch] ?? ''
    const seg = {
      a: [x + s, y, w - 2 * s, s],
      b: [x + w - s, y + s, s, h / 2 - s],
      c: [x + w - s, y + h / 2, s, h / 2 - s],
      d: [x + s, y + h - s, w - 2 * s, s],
      e: [x, y + h / 2, s, h / 2 - s],
      f: [x, y + s, s, h / 2 - s],
      g: [x + s, y + h / 2 - s / 2, w - 2 * s, s],
    }
    for (const k of on) rect(...seg[k].map(Math.round), c)
    x += w + s * 3
  }
}

const CRC = new Int32Array(256).map((_, n) => {
  let c = n
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
  return c
})
function crc32(buf) {
  let c = -1
  for (const b of buf) c = CRC[(c ^ b) & 0xff] ^ (c >>> 8)
  return (c ^ -1) >>> 0
}
function chunk(type, data) {
  const len = Buffer.alloc(4)
  len.writeUInt32BE(data.length)
  const td = Buffer.concat([Buffer.from(type), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(td))
  return Buffer.concat([len, td, crc])
}
function png(rgb) {
  const raw = Buffer.alloc((SCREEN_W * 3 + 1) * SCREEN_H)
  for (let y = 0; y < SCREEN_H; y++) rgb.copy(raw, y * (SCREEN_W * 3 + 1) + 1, y * SCREEN_W * 3, (y + 1) * SCREEN_W * 3)
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(SCREEN_W, 0)
  ihdr.writeUInt32BE(SCREEN_H, 4)
  ihdr[8] = 8
  ihdr[9] = 2
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), chunk('IHDR', ihdr), chunk('IDAT', deflateSync(raw, { level: 1 })), chunk('IEND', Buffer.alloc(0))])
}

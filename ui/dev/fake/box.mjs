// QIDI Box extras beyond the core fake's Box: Valve Oil's turn spools while heating (tltg_box_spool_roll), spool
// tags the Box read (info_from_rfid), the active tool (extrude_state), per-slot load and unload motion
// (TLTG_BOX_MOTION SLOT=), and QIDI's filament list (qidi_filament_config/filament-0.4.json) that the Filaments tab
// reads and writes back whole.

// More of QIDI's 01.01.07.01 filament list, beside the core fake's seven. Fields as the real file has them.
const entry = (id, vendor, type, nozzle, boxMax, extra = {}) => ({
  type,
  filament_id: id,
  nozzle_temperature: nozzle,
  box_temperature_range_high: [String(boxMax)],
  box_temperature_range_low: ['0'],
  filament_vendor: vendor,
  filament_is_support: '0',
  chamber_temperatures: '0',
  filament_dev_ams_drying_temperature: [String(boxMax || 45), String(boxMax || 45), String(boxMax || 45), String(boxMax || 45)],
  filament_dev_ams_drying_time: ['12', '12', '12', '12'],
  ...extra,
})
const EXTRA_FILAMENTS = {
  'QIDI PLA Rapido': entry('QFA03', 'QIDI', 'PLA', [190, 240], 0),
  'QIDI PLA-CF': entry('QFA10', 'QIDI', 'PLA-CF', [210, 250], 50),
  'QIDI ABS Rapido': entry('QFC01', 'QIDI', 'ABS', [240, 280], 65, { chamber_temperatures: '50' }),
  'QIDI ASA': entry('QFE01', 'QIDI', 'ASA', [240, 280], 65, { chamber_temperatures: '50' }),
  'QIDI PA12-CF': entry('QFG01', 'QIDI', 'PA12-CF', [280, 320], 65, { chamber_temperatures: '60' }),
  'QIDI PC/ABS-FR': entry('QFH01', 'QIDI', 'PC-ABS-FR', [260, 290], 65, { chamber_temperatures: '60' }),
  'QIDI Support For PLA': entry('QFS01', 'QIDI', 'PLA-S', [190, 230], 0, { filament_is_support: '1' }),
  'QIDI TPU 95A-HF': entry('QFD02', 'QIDI', 'TPU', [210, 240], 0),
  'Bambu PLA Basic': entry('QFA80', 'Bambu', 'PLA', [190, 240], 0),
  'Bambu PLA Matte': entry('QFA81', 'Bambu', 'PLA', [190, 240], 0),
  'Bambu PETG HF': entry('QFB80', 'Bambu', 'PETG', [230, 270], 45),
  'Bambu ASA': entry('QFE80', 'Bambu', 'ASA', [240, 280], 65),
  'Elegoo PLA': entry('QFA60', 'Elegoo', 'PLA', [190, 230], 0),
  'Elegoo PLA Silk': entry('QFA61', 'Elegoo', 'PLA', [200, 230], 0),
  'Elegoo PETG': entry('QFB60', 'Elegoo', 'PETG', [230, 260], 45),
  'Anycubic PLA': entry('QFA70', 'Anycubic', 'PLA', [190, 230], 0),
  'Anycubic PETG': entry('QFB70', 'Anycubic', 'PETG', [230, 260], 45),
  'Polymaker PolyLite PLA': entry('QFA50', 'Polymaker', 'PLA', [190, 230], 0),
  'HATCHBOX PLA': entry('QFA55', 'HATCHBOX', 'PLA', [190, 220], 0),
  'Overture PETG': entry('QFB40', 'Overture', 'PETG', [230, 260], 45),
  'Generic PLA': entry('QFA90', 'Generic', 'PLA', [190, 240], 0),
  'Generic PETG': entry('QFB90', 'Generic', 'PETG', [230, 270], 45),
  'Generic ABS': entry('QFC90', 'Generic', 'ABS', [240, 280], 65),
  'Generic PVA': entry('QFS90', 'Generic', 'PVA', [200, 230], 0, { filament_is_support: '1' }),
}
// The core fake's seven, for when it doesn't share its list (fake.filaments).
const CORE_FILAMENTS = {
  'QIDI PLA Basic': { filament_id: 'QFA01', type: 'PLA', filament_vendor: 'QIDI', nozzle_temperature: [190, 230], box_temperature_range_high: ['50'] },
  'QIDI PLA Matte': { filament_id: 'QFA02', type: 'PLA', filament_vendor: 'QIDI', nozzle_temperature: [190, 230], box_temperature_range_high: ['50'] },
  'QIDI PETG Tough': { filament_id: 'QFB01', type: 'PETG', filament_vendor: 'QIDI', nozzle_temperature: [230, 260], box_temperature_range_high: ['60'] },
  'Polymaker PETG': { filament_id: 'QFB12', type: 'PETG', filament_vendor: 'Polymaker', nozzle_temperature: [230, 260], box_temperature_range_high: ['60'] },
  'Bambu ABS': { filament_id: 'QFC20', type: 'ABS', filament_vendor: 'Bambu', nozzle_temperature: [240, 270], box_temperature_range_high: ['65'] },
  'Overture PLA Silk': { filament_id: 'QFA40', type: 'PLA', filament_vendor: 'Overture', nozzle_temperature: [200, 230], box_temperature_range_high: ['50'] },
  'Generic TPU 95A': { filament_id: 'QFD01', type: 'TPU', filament_vendor: 'Generic', nozzle_temperature: [210, 240], box_temperature_range_high: ['0'] },
}
const FILAMENT_FILE = '/server/files/config/qidi_filament_config/filament-0.4.json'

export function install(fake) {
  const { set, state, pushConsole, rpcError } = fake
  const st = () => state.status
  const vars = () => st().save_variables.variables

  // The core's list when it shares it, so slots and tag writes resolve the same filaments; otherwise our own copy.
  const seeded = fake.filaments ? null : JSON.parse(JSON.stringify({ ...CORE_FILAMENTS, ...EXTRA_FILAMENTS }))
  const db = fake.filaments ?? seeded
  if (fake.filaments) for (const [k, v] of Object.entries(EXTRA_FILAMENTS)) if (!(k in db)) db[k] = v

  fake.commands({
    ...Object.fromEntries(Array.from({ length: 16 }, (_, t) => [`T${t}`, 'G-Code macro'])),
    UNLOAD_FILAMENT_CONTROL: 'Unload a Box slot',
    EJECT_FILAMENT_CONTROL: 'Eject a Box slot',
    READ_RFID: "Read a Box slot's RFID tag",
    T_MANAGER: 'Change tool',
  })

  fake.status((s) => {
    const v = s.save_variables.variables
    v.vo_box_spool_roll = 1
    v.extrude_state = 1
    s.tltg_box_spool_roll = { enabled: true, rolling: null }
    // 1A and 2B were set from their spools' tags.
    for (const [b, i] of [
      [0, 0],
      [1, 1],
    ]) {
      const info = s.box_extras?.boxes?.[b]?.filament_info?.[i]
      if (info) info.info_from_rfid = 1
    }
    const m = s.tltg_box_feed_motion
    if (m) {
      m.vendor_keys = []
      m.slots = Object.fromEntries(Array.from({ length: 8 }, (_, i) => [`slot${i}`, { overrides: i === 5 ? { fast_speed_1: 120, approach_speed: 20 } : {} }]))
      m.slots.slot3.overrides = { unload_accel: 'abc' }
      refreshMotion(m)
    }
  })

  // Each slot's effective motion, from its overrides, the global settings, and QIDI's values.
  function refreshMotion(m) {
    const eff = (o, k) => {
      const ok = (x) => typeof x === 'number'
      return ok(o[k]) ? o[k] : ok(m.settings[k]) ? m.settings[k] : m.vendor[k]
    }
    for (const [key, slot] of Object.entries(m.slots)) {
      const o = slot.overrides ?? {}
      const bad = Object.entries(o).find(([, x]) => typeof x !== 'number')
      slot.load = { state: 'custom', error: null, fast_length: eff(o, 'fast_length'), fast_speed_1: eff(o, 'fast_speed_1'), fast_speed_2: eff(o, 'fast_speed_2'), approach_speed: eff(o, 'approach_speed'), approach_length: 240, accel: eff(o, 'load_accel') }
      slot.unload = { state: bad ? 'error' : 'custom', error: bad ? `${bad[0]} override ${JSON.stringify(bad[1])} is not a whole number` : null, speed_1: eff(o, 'unload_speed_1'), speed_2: eff(o, 'unload_speed_2'), accel: eff(o, 'unload_accel'), length_1: 350, length_2: 1150 }
      m.slots[key] = slot
    }
  }

  function checkMotion(p, perSlot) {
    const m = st().tltg_box_feed_motion
    for (const [K, raw] of Object.entries(p)) {
      if (K === 'SLOT' || K === 'RESET') continue
      const k = K.toLowerCase()
      const lim = m.limits[k]
      if (!lim) throw rpcError(`Unknown Box motion setting ${K}`)
      if (perSlot && m.global_only.includes(k)) throw rpcError(`${K} applies to every slot; set it without SLOT`)
      if (raw === 'vendor' || raw === 'global' || raw === 'default') continue
      const n = Number(raw)
      if (!/^\d+$/.test(raw) || n < lim.min || n > lim.max) throw rpcError(`${K} must be a whole number from ${lim.min} to ${lim.max} ${lim.unit ?? ''}`.trim())
    }
  }

  // Lines handled here, before the core: per-slot motion, and checking motion values the core takes as given.
  async function handle(line) {
    const [cmd, ...rest] = line.trim().split(/\s+/)
    const C = (cmd ?? '').toUpperCase()
    const p = Object.fromEntries(rest.filter((r) => r.includes('=')).map((r) => r.split('=')).map(([k, ...v]) => [k.toUpperCase(), v.join('=').replace(/^"|"$/g, '')]))
    if (C !== 'TLTG_BOX_MOTION') return false
    const m = st().tltg_box_feed_motion
    if (st().print_stats.state === 'printing' || st().print_stats.state === 'paused') throw rpcError('Box motion settings can be changed while the printer is not printing')
    if (p.SLOT == null) {
      checkMotion(p, false)
      return false
    }
    const key = `slot${p.SLOT}`
    if (!m.slots[key]) throw rpcError(`Slot ${p.SLOT} has no motion settings`)
    checkMotion(p, true)
    const overrides = p.RESET ? {} : { ...m.slots[key].overrides }
    for (const [K, raw] of Object.entries(p)) {
      if (K === 'SLOT' || K === 'RESET') continue
      if (raw === 'global' || raw === 'default') delete overrides[K.toLowerCase()]
      else overrides[K.toLowerCase()] = Number(raw)
    }
    const slots = { ...m.slots, [key]: { ...m.slots[key], overrides } }
    const next = { ...m, slots }
    refreshMotion(next)
    set('tltg_box_feed_motion', { slots: next.slots })
    pushConsole(`// Box motion for slot ${p.SLOT} saved.`)
    return true
  }
  const handlers = fake.handle({})
  const core = handlers['printer.gcode.script']
  fake.handle({
    'printer.gcode.script': async (params, ws) => {
      for (const line of String(params.script).split('\n')) if (!(await handle(line))) await core({ ...params, script: line }, ws)
      return 'ok'
    },
  })

  // Keep derived status in step with what the core changes: the spool roll follows its saved variable, and while a Box
  // heats it turns each empty slot's spool in turn; slot motion follows the global settings.
  let tick = 0
  setInterval(() => {
    const s = st()
    if (!s.save_variables) return
    tick++
    const enabled = Number(vars().vo_box_spool_roll ?? 0) === 1
    let rolling = null
    if (enabled && tick % 8 < 3) {
      const empty = []
      for (let b = 1; b <= Number(vars().box_count ?? 0); b++) {
        if (!(s[`heater_generic heater_box${b}`]?.target > 0)) continue
        for (let i = 0; i < 4; i++) if (s[`box_stepper slot${(b - 1) * 4 + i}`]?.runout_button === 1) empty.push(`${b}${'ABCD'[i]}`)
      }
      if (empty.length) rolling = empty[Math.floor(tick / 8) % empty.length]
    }
    const r = s.tltg_box_spool_roll
    if (r && (r.enabled !== enabled || r.rolling !== rolling)) set('tltg_box_spool_roll', { enabled, rolling })
    const m = s.tltg_box_feed_motion
    if (m?.slots) {
      const before = JSON.stringify(m.slots)
      const copy = { ...m, slots: JSON.parse(before) }
      refreshMotion(copy)
      if (JSON.stringify(copy.slots) !== before) set('tltg_box_feed_motion', { slots: copy.slots })
    }
  }, 1000)

  // QIDI's filament list: read with GET, and written back whole by uploading to qidi_filament_config.
  fake.http(async (req, res, path) => {
    if (path === FILAMENT_FILE && req.method === 'GET') {
      res.writeHead(200, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
      res.end(`${JSON.stringify(db, null, 2)}\n`)
      return true
    }
    if (path !== '/server/files/upload' || req.method !== 'POST') return false
    const chunks = []
    for await (const chunk of req) chunks.push(chunk)
    const body = Buffer.concat(chunks)
    const text = body.toString('latin1')
    const field = (name) => new RegExp(`name="${name}"\\r\\n\\r\\n([^\\r]*)`).exec(text)?.[1]
    const filename = /name="file"; filename="([^"]+)"/.exec(text)?.[1]
    if (field('root') !== 'config' || field('path') !== 'qidi_filament_config' || filename !== 'filament-0.4.json') {
      // Not ours: hand the body on to the next route, which reads the request again.
      req[Symbol.asyncIterator] = async function* () {
        yield body
      }
      return false
    }
    const start = text.indexOf('\r\n\r\n', text.indexOf('name="file"')) + 4
    const end = text.lastIndexOf('\r\n--')
    let parsed
    try {
      parsed = JSON.parse(body.subarray(start, end).toString('utf8'))
    } catch {
      parsed = null
    }
    if (parsed && typeof parsed === 'object') {
      for (const k of Object.keys(db)) delete db[k]
      Object.assign(db, parsed)
    }
    fake.broadcast('notify_filelist_changed', [{ action: 'create_file', item: { path: 'qidi_filament_config/filament-0.4.json', root: 'config' } }])
    res.writeHead(201, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ item: { path: 'qidi_filament_config/filament-0.4.json', root: 'config' }, action: 'create_file' }))
    return true
  })
}

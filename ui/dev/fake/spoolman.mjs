// Spoolman: Moonraker's Spoolman component and a made-up Spoolman, with Valve Oil's tltg_spoolman Klipper module
// giving each QIDI Box slot its spool (docs/spoolman.md).
//
// Moonraker reports the `spoolman` component only when started with `--spoolman`, which takes an optional mode:
//
//   --spoolman          Spoolman reachable, with live updates over its websocket at /spoolman/api/v1/
//   --spoolman cors     Reachable through Moonraker, but Spoolman refuses the page's origin, so live updates fail
//   --spoolman offline  Spoolman unreachable
//
// `VO_SPOOLMAN CONNECTED=0` and `=1` take Spoolman away and back at runtime. While printing, the active spool uses
// about a gram every 10 s. tltg_spoolman is there either way; with --spoolman, 1A, 1B, 2A, and the external spool
// start with spools, and 1B is loaded.

const EXTERNAL_SLOT = 16
const label = (slot) => (slot === EXTERNAL_SLOT ? 'External spool' : `${Math.floor(slot / 4) + 1}${'ABCD'[slot % 4]}`)
const days = (d) => new Date(Date.now() - d * 86400000).toISOString().replace(/\.\d+Z$/, 'Z')

const VENDORS = {
  qidi: { id: 1, registered: days(400), name: 'QIDI', empty_spool_weight: 180 },
  polymaker: { id: 2, registered: days(380), name: 'Polymaker', empty_spool_weight: 140 },
  esun: { id: 3, registered: days(300), name: 'eSUN', empty_spool_weight: 220 },
  elegoo: { id: 4, registered: days(200), name: 'Elegoo', empty_spool_weight: 160 },
}

const FILAMENTS = [
  { id: 1, vendor: 'qidi', name: 'PLA Basic', material: 'PLA', density: 1.24, color_hex: '1D1D1F', extruder: 220, bed: 60 },
  { id: 2, vendor: 'qidi', name: 'PLA Matte', material: 'PLA', density: 1.24, color_hex: 'F5D90A', extruder: 220, bed: 60 },
  { id: 3, vendor: 'qidi', name: 'PETG Tough', material: 'PETG', density: 1.27, color_hex: '2A78D6', extruder: 245, bed: 80 },
  { id: 4, vendor: 'polymaker', name: 'PolyTerra PLA', material: 'PLA', density: 1.31, color_hex: 'E87BA4', extruder: 215, bed: 55 },
  { id: 5, vendor: 'polymaker', name: 'PolyLite ASA', material: 'ASA', density: 1.07, color_hex: 'F0F0F0', extruder: 255, bed: 100 },
  { id: 6, vendor: 'esun', name: 'PLA+ Silk Dual', material: 'PLA', density: 1.23, color_hex: 'C9A227', multi_color_hexes: 'C9A227,B23A4B', extruder: 215, bed: 60 },
  { id: 7, vendor: 'esun', name: 'ABS+', material: 'ABS', density: 1.04, color_hex: 'B23A4B', extruder: 250, bed: 100 },
  { id: 8, vendor: 'elegoo', name: 'Rapid PETG', material: 'PETG', density: 1.27, color_hex: '1BAF7A', extruder: 240, bed: 75 },
  { id: 9, vendor: 'qidi', name: 'PLA-CF', material: 'PLA-CF', density: 1.21, color_hex: '3A3F47', extruder: 230, bed: 60 },
  { id: 10, vendor: 'elegoo', name: 'TPU 95A', material: 'TPU', density: 1.21, color_hex: 'EB6834', extruder: 230, bed: 45 },
]

function filament(id) {
  const f = FILAMENTS.find((x) => x.id === id)
  return {
    id: f.id,
    registered: days(360 - f.id * 10),
    name: f.name,
    vendor: VENDORS[f.vendor],
    material: f.material,
    density: f.density,
    diameter: 1.75,
    weight: 1000,
    spool_weight: VENDORS[f.vendor].empty_spool_weight,
    color_hex: f.color_hex,
    ...(f.multi_color_hexes ? { multi_color_hexes: f.multi_color_hexes, multi_color_direction: 'coaxial' } : {}),
    settings_extruder_temp: f.extruder,
    settings_bed_temp: f.bed,
  }
}

// [id, filament, used g, first used days ago, last used days ago, location, lot, comment, archived]
const SEED = [
  [1, 1, 612.4, 40, 1, 'Box 1', 'QD2408-11', null],
  [2, 2, 188.9, 21, 0, 'Box 1', 'QD2410-03', null],
  [3, 3, 0, null, null, 'Shelf', null, null],
  [4, 4, 402.7, 90, 12, 'Shelf', 'PT-2231', 'Prints best at 210 °C'],
  [5, 5, 75.2, 6, 3, 'Box 2', null, null],
  [6, 6, 811.8, 120, 30, 'Dry box', null, 'Nearly empty'],
  [7, 7, 254.1, 60, 18, 'Dry box', 'ES-ABS-77', null],
  [8, 8, 33.6, 2, 2, 'Shelf', null, null],
  [9, 9, 499, 45, 7, 'External', null, 'Hardened nozzle only'],
  [10, 10, 120.5, 80, 50, 'Shelf', null, null],
  [11, 1, 1000, 200, 150, null, null, 'Used up', true],
]
const spools = SEED.map(([id, f, used, first, last, location, lot, comment, archived = false]) => ({
  id,
  registered: days(220 - id * 5),
  filament: f,
  initial_weight: 1000,
  used_weight: used,
  first_used: first == null ? null : days(first),
  last_used: last == null ? null : days(last),
  location,
  lot_nr: lot,
  comment,
  archived,
}))

const length = (g, f) => (g / f.density / ((Math.PI / 4) * f.diameter ** 2)) * 1000

/** A spool as Spoolman's API returns it, with its weights and lengths worked out. */
function view(s) {
  const f = filament(s.filament)
  const out = {
    id: s.id,
    registered: s.registered,
    filament: f,
    price: 22.99,
    initial_weight: s.initial_weight,
    spool_weight: f.spool_weight,
    used_weight: Math.round(s.used_weight * 100) / 100,
    remaining_weight: Math.max(0, Math.round((s.initial_weight - s.used_weight) * 100) / 100),
    used_length: Math.round(length(s.used_weight, f) * 10) / 10,
    remaining_length: Math.max(0, Math.round(length(s.initial_weight - s.used_weight, f) * 10) / 10),
    archived: s.archived,
    extra: {},
  }
  for (const k of ['first_used', 'last_used', 'location', 'lot_nr', 'comment']) if (s[k] != null) out[k] = s[k]
  return out
}

export function install(fake) {
  const { args, set, broadcast, pushConsole, rpcError } = fake
  const enabled = 'spoolman' in args
  const mode = typeof args.spoolman === 'string' && !args.spoolman.startsWith('--') ? args.spoolman : 'live'
  let reachable = mode !== 'offline'
  let active = null
  // tltg_spoolman's saved spools by slot, kept in the saved variable the module uses.
  let slots = enabled ? { slot0: 1, slot1: 2, slot4: 5, slot16: 9 } : {}
  let synced = null
  const sockets = new Set()
  const st = () => fake.state.status
  const vars = () => st().save_variables?.variables ?? {}

  function loadedSlot() {
    const v = vars()
    if (v.enable_box !== 1 || !st().box_extras || !(Number(v.box_count) > 0)) return EXTERNAL_SLOT
    const m = /^slot(\d+)$/.exec(String(v.last_load_slot ?? 'slot16'))
    return m && Number(m[1]) <= EXTERNAL_SLOT ? Number(m[1]) : null
  }
  const moduleStatus = () => {
    const slot = loadedSlot()
    return { loaded_slot: slot == null ? null : `slot${slot}`, spools: { ...slots } }
  }

  function setActive(id) {
    if (active === id) return
    active = id
    broadcast('notify_active_spool_set', [{ spool_id: id }])
  }

  // As tltg_spoolman does twice a second: the loaded slot's spool becomes the active one, when any slot has a spool.
  function sync() {
    if (!st().save_variables) return
    const status = moduleStatus()
    const prev = st().tltg_spoolman
    if (!prev || prev.loaded_slot !== status.loaded_slot || JSON.stringify(prev.spools) !== JSON.stringify(status.spools)) set('tltg_spoolman', status)
    if (!enabled || !Object.keys(slots).length || status.loaded_slot == null) return (synced = null)
    const key = `${status.loaded_slot}:${slots[status.loaded_slot] ?? 0}`
    if (synced === key) return
    synced = key
    setActive(slots[status.loaded_slot] ?? null)
  }
  setInterval(sync, 500)

  function save(next) {
    slots = next
    set('save_variables', { variables: { ...vars(), tltg_spoolman_spools: { ...slots } } })
    sync()
  }

  fake.status((status) => {
    status.save_variables.variables.tltg_spoolman_spools = { ...slots }
    status.tltg_spoolman = { loaded_slot: null, spools: { ...slots } }
    queueMicrotask(sync)
  })

  fake.commands({ TLTG_SPOOLMAN_SLOT: 'Set or list the Spoolman spool for each QIDI Box slot', ...(enabled ? { VO_SPOOLMAN: 'Fake: take Spoolman away (CONNECTED=0) or back' } : {}) })

  fake.gcode(async (C, p) => {
    if (C === 'TLTG_SPOOLMAN_SLOT') {
      if (p.RESET === '1') {
        save({})
        pushConsole("// Spoolman: cleared every slot's spool. The active spool no longer follows the loaded slot.")
        return true
      }
      if (p.SPOOL == null) {
        const keys = Object.keys(slots).sort((a, b) => Number(a.slice(4)) - Number(b.slice(4)))
        if (!keys.length) pushConsole("// Spoolman: no slot has a spool, so the active spool doesn't follow the loaded slot. Set one with TLTG_SPOOLMAN_SLOT SLOT=<n> SPOOL=<id>.")
        else {
          const loaded = loadedSlot()
          const lines = ['Spoolman spools:', ...keys.map((k) => `${label(Number(k.slice(4)))}: spool ${slots[k]}`)]
          if (loaded != null) lines.push(`Loaded: ${label(loaded)}, ${slots[`slot${loaded}`] ? `spool ${slots[`slot${loaded}`]}` : 'no spool'}`)
          if (!enabled) lines.push("Moonraker's Spoolman integration isn't connected; add [spoolman] to moonraker.conf.")
          pushConsole(lines.map((l) => `// ${l}`).join('\n'))
        }
        return true
      }
      const spool = Number(p.SPOOL)
      let slot = p.SLOT != null ? Number(p.SLOT) : loadedSlot()
      if (!Number.isInteger(spool) || spool < 0) throw rpcError(`Error on 'TLTG_SPOOLMAN_SLOT': SPOOL must have minimum of 0`)
      if (p.SLOT != null && !(Number.isInteger(slot) && slot >= 0 && slot <= EXTERNAL_SLOT)) throw rpcError(`Error on 'TLTG_SPOOLMAN_SLOT': SLOT must be between 0 and 16`)
      if (slot == null) throw rpcError('Spoolman: no slot is recorded as loaded; name one with SLOT=')
      const next = { ...slots }
      if (spool) next[`slot${slot}`] = spool
      else delete next[`slot${slot}`]
      save(next)
      pushConsole(spool ? `// Spoolman: ${label(slot)} is spool ${spool}.` : `// Spoolman: ${label(slot)} has no spool.`)
      return true
    }
    if (C === 'VO_SPOOLMAN' && enabled) {
      reachable = p.CONNECTED !== '0'
      broadcast('notify_spoolman_status_changed', [{ spoolman_connected: reachable }])
      if (!reachable) for (const ws of sockets) ws.terminate()
      pushConsole(`// Fake Spoolman ${reachable ? 'reachable' : 'unreachable'}.`)
      return true
    }
    return false
  })

  if (!enabled) return

  const port = Number(args.port ?? 7125)
  const server = `http://127.0.0.1:${port}/spoolman`

  fake.wrap('server.info', async (p, ws, next) => {
    const info = await next(p, ws)
    return { ...info, components: [...info.components, 'spoolman'] }
  })
  fake.wrap('server.config', async (p, ws, next) => {
    const r = await Promise.resolve()
      .then(() => next(p, ws))
      .catch(() => ({ config: {}, orig: {}, files: [] }))
    return { ...r, config: { ...r.config, spoolman: { server, sync_rate: 5 } } }
  })

  const proxyError = (status_code, message) => ({ response: null, error: { status_code, message } })
  fake.handle({
    'server.spoolman.get_spool_id': () => ({ spool_id: active }),
    'server.spoolman.post_spool_id': async (p) => {
      const id = p.spool_id == null ? null : Number(p.spool_id)
      setActive(id)
      // Valve Oil's Moonraker patch: a spool picked while a Box slot is loaded becomes that slot's spool.
      if (loadedSlot() != null) {
        const slot = loadedSlot()
        const next = { ...slots }
        if (id) next[`slot${slot}`] = id
        else delete next[`slot${slot}`]
        save(next)
        pushConsole(id ? `// Spoolman: ${label(slot)} is spool ${id}.` : `// Spoolman: ${label(slot)} has no spool.`)
      }
      return { spool_id: id }
    },
    'server.spoolman.proxy': async (p) => {
      if (!reachable) return proxyError(503, 'Spoolman server not reachable')
      if ((p.request_method ?? 'GET').toUpperCase() !== 'GET') return proxyError(405, 'The fake Spoolman only answers GET')
      const [path, query = ''] = String(p.path ?? '').split('?')
      await new Promise((r) => setTimeout(r, 120))
      if (path === '/v1/info') return { response: { version: '0.22.1', debug_mode: false, automatic_backups: true, data_dir: '/home/app/.local/share/spoolman', logs_dir: '/home/app/.local/share/spoolman', backups_dir: '/home/app/.local/share/spoolman/backups', db_type: 'sqlite', git_commit: 'fake', build_date: '2025-06-01T00:00:00Z' }, error: null }
      if (path === '/v1/spool') {
        const archived = /allow_archived=true/.test(query + (p.query ?? ''))
        return { response: spools.filter((s) => archived || !s.archived).map(view), error: null }
      }
      const m = /^\/v1\/spool\/(\d+)$/.exec(path)
      if (m) {
        const s = spools.find((x) => x.id === Number(m[1]))
        return s ? { response: view(s), error: null } : proxyError(404, `No spool with ID ${m[1]} found.`)
      }
      return proxyError(404, 'Not Found')
    },
  })

  // Spoolman's own server, which the browser reaches directly: /api/v1/info for the UI's reachability probe, the
  // websocket at /api/v1/ for live changes, and a placeholder page for links into Spoolman's web UI.
  fake.http(async (req, res, path) => {
    if (path !== '/spoolman' && !path.startsWith('/spoolman/')) return false
    if (!reachable) return req.socket.destroy(), true
    if (mode === 'cors') res.removeHeader('Access-Control-Allow-Origin')
    if (path === '/spoolman/api/v1/info') {
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ version: '0.22.1' }))
      return true
    }
    const id = /\/spool\/show\/(\d+)$/.exec(path)?.[1]
    res.writeHead(200, { 'Content-Type': 'text/html' })
    res.end(`<!doctype html><title>Fake Spoolman</title><body style="font-family:sans-serif;background:#111;color:#eee;padding:2rem"><h1>Fake Spoolman</h1><p>${id ? `Spool #${id}` : 'Spools'}</p></body>`)
    return true
  })
  if (mode === 'live') {
    fake.websocket('/spoolman/api/v1/', (ws) => {
      if (!reachable) return ws.terminate()
      sockets.add(ws)
      ws.on('close', () => sockets.delete(ws))
    })
  }

  // Filament used while printing, charged to the active spool as Moonraker reports it to Spoolman.
  setInterval(() => {
    if (!reachable || active == null || st().print_stats?.state !== 'printing') return
    const s = spools.find((x) => x.id === active)
    if (!s || s.used_weight >= s.initial_weight) return
    s.used_weight = Math.min(s.initial_weight, s.used_weight + 0.2)
    s.last_used = new Date().toISOString().replace(/\.\d+Z$/, 'Z')
    s.first_used ??= s.last_used
    const msg = JSON.stringify({ type: 'updated', resource: 'spool', date: s.last_used, payload: view(s) })
    for (const ws of sockets) ws.readyState === 1 && ws.send(msg)
  }, 2000)
}

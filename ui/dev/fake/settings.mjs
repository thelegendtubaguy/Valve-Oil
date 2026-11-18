// Settings screen fakes: Moonraker's webcams (server.webcams.*), QIDI's LAN only mode (QIDI Moonraker's
// server.database.config.*), Valve Oil's add-ons service at /tltg-addons/api (installer/system/vo_addons.py), and
// Fluidd's temperature presets in the database another plugin keeps in state.database.
//
// Pick the add-ons' state with `--addons <scenario>`, or switch at runtime with `VO_ADDONS NAME=<scenario>`:
//   none       every add-on not installed, as on a fresh install (the default)
//   installed  OctoEverywhere connected but not linked, Tailscale connected, ZeroTier on two networks
//   linking    Obico waiting with its link code, Tailscale waiting for a sign-in, a ZeroTier update
//   busy       the printer is printing, so installs wait
//   foreign    OctoEverywhere installed outside Valve Oil
//   failed     the last install failed, with its log
//   absent     no add-ons service (404), as on a printer without it
// Install, update, remove, and relink run as jobs for a few seconds. `--lan-mode 1` starts with LAN only mode on, and
// `--lan-mode none` answers as upstream Moonraker, which has no LAN only mode.

const ADDONS = {
  octoeverywhere: {
    id: 'octoeverywhere',
    name: 'OctoEverywhere',
    description: 'Remote access to Fluidd, print notifications, and AI failure detection from anywhere.',
    homepage: 'https://octoeverywhere.com',
    available_version: '5.4.1',
    service: 'octoeverywhere',
    group: 'remote',
  },
  obico: {
    id: 'obico',
    name: 'Obico',
    description: "Remote access to Fluidd and the camera, print notifications, and AI failure detection, through Obico's cloud or your own Obico server.",
    homepage: 'https://www.obico.io',
    available_version: '2.2.4',
    service: 'moonraker-obico',
    group: 'remote',
  },
  tailscale: {
    id: 'tailscale',
    name: 'Tailscale',
    description: 'Reach Fluidd, the camera, and SSH from your own devices anywhere, over your private Tailscale network.',
    homepage: 'https://tailscale.com',
    available_version: '1.104.6',
    service: 'tailscaled',
  },
  zerotier: {
    id: 'zerotier',
    name: 'ZeroTier',
    description: 'Reach Fluidd, the camera, and SSH from your own devices anywhere, over your ZeroTier network.',
    homepage: 'https://www.zerotier.com',
    available_version: '1.16.2',
    service: 'zerotier-one',
  },
}

// Made-up identities: a ZeroTier node and networks, a tailnet, and link codes.
const NODE_ID = 'f00dfeed42'
const HOME_NET = '8056c2e21c000001'
const CODE_PAGE = 'https://octoeverywhere.com/code'

export function install(fake) {
  const { state, broadcast, now } = fake

  // ---------------- webcams ----------------
  // The core lists one camera; this keeps Moonraker's full entries so they can be added, edited, and removed.
  const cams = [
    {
      name: 'Chamber',
      location: 'printer',
      service: 'mjpegstreamer-adaptive',
      enabled: true,
      icon: 'mdiWebcam',
      target_fps: 15,
      target_fps_idle: 5,
      stream_url: '/webcam/?action=stream',
      snapshot_url: '/webcam/?action=snapshot',
      flip_horizontal: false,
      flip_vertical: false,
      rotation: 0,
      aspect_ratio: '4:3',
      extra_data: {},
      source: 'database',
      uid: '0b6f5a9e-1c2d-4e3f-8a7b-9c0d1e2f3a4b',
    },
  ]
  const camsChanged = () => broadcast('notify_webcams_changed', [{ webcams: cams }])
  const camByUid = (uid) => {
    const c = cams.find((x) => x.uid === uid)
    if (!c) throw fake.rpcError(`Webcam with uid ${uid} not found`)
    return c
  }
  const FIELDS = ['name', 'location', 'service', 'enabled', 'icon', 'target_fps', 'target_fps_idle', 'stream_url', 'snapshot_url', 'flip_horizontal', 'flip_vertical', 'rotation', 'aspect_ratio', 'extra_data']
  fake.handle({
    'server.webcams.list': () => ({ webcams: cams }),
    'server.webcams.get_item': (p) => ({ webcam: camByUid(p.uid) }),
    'server.webcams.post_item': (p) => {
      if (!p.uid && !p.name) throw fake.rpcError('Webcam name is required')
      const existing = p.uid ? camByUid(p.uid) : cams.find((c) => c.name === p.name)
      if (existing?.source === 'config') throw fake.rpcError(`Cannot overwrite webcam '${existing.name}' sourced from the config`)
      const cam = existing ?? { location: 'printer', icon: 'mdiWebcam', extra_data: {}, aspect_ratio: '4:3', source: 'database', uid: crypto.randomUUID() }
      for (const k of FIELDS) if (p[k] !== undefined) cam[k] = p[k]
      if (!existing) cams.push(cam)
      camsChanged()
      return { webcam: cam }
    },
    'server.webcams.delete_item': (p) => {
      const cam = camByUid(p.uid)
      cams.splice(cams.indexOf(cam), 1)
      camsChanged()
      return { webcam: cam }
    },
  })

  // ---------------- LAN only mode ----------------
  // QIDI's Moonraker answers its settings table with a status, not an RPC error.
  let lanMode = fake.args['lan-mode'] ?? '0'
  if (lanMode !== 'none')
    fake.handle({
      'server.database.config.select': (p) =>
        p.section === 'general' && p.key === 'lan_mode' ? { status: 'success', result: { 'general.lan_mode': lanMode } } : { status: 'error', message: `Config item '${p.section}.${p.key}' not found` },
      'server.database.config.update': (p) => {
        if (p.section !== 'general' || p.key !== 'lan_mode') return { status: 'error', message: `Config item '${p.section}.${p.key}' not found` }
        lanMode = String(p.value)
        fake.pushConsole(`// LAN only mode ${lanMode === '1' ? 'on' : 'off'}`)
        return { status: 'success', result: { 'general.lan_mode': lanMode } }
      },
    })

  // ---------------- temperature presets ----------------
  // Fluidd's shape (uiSettings.dashboard.tempPresets), keyed by heater names as Fluidd keys them.
  const db = (state.database ??= {})
  const fluidd = (db.fluidd ??= {})
  const ui = (fluidd.uiSettings ??= {})
  const dashboard = (ui.dashboard ??= {})
  dashboard.tempPresets ??= [
    {
      id: '6f1c0b52-6a0e-4d55-9a8e-2f6d5b1c9e01',
      name: 'PLA',
      values: { extruder: { value: 220, type: 'heater', active: true }, heater_bed: { value: 60, type: 'heater', active: true }, chamber: { value: 0, type: 'heater', active: false } },
    },
    {
      id: '6f1c0b52-6a0e-4d55-9a8e-2f6d5b1c9e02',
      name: 'PETG',
      values: { extruder: { value: 245, type: 'heater', active: true }, heater_bed: { value: 75, type: 'heater', active: true }, chamber: { value: 0, type: 'heater', active: false } },
    },
    {
      id: '6f1c0b52-6a0e-4d55-9a8e-2f6d5b1c9e03',
      name: 'ABS',
      values: { extruder: { value: 255, type: 'heater', active: true }, heater_bed: { value: 100, type: 'heater', active: true }, chamber: { value: 50, type: 'heater', active: true } },
      gcode: 'M106 P3 S0',
    },
  ]

  // ---------------- add-ons service ----------------
  const addons = { scenario: 'none', installed: {}, job: null, printerBusy: false, absent: false, jobs: 0 }

  // An installed add-on: its version, whether it was installed outside Valve Oil, and its service's own state.
  const fresh = (id) =>
    ({
      octoeverywhere: { connected: true, linked: false },
      obico: { server: 'https://app.obico.io', linked: false, discovering: true, code: null, codeAt: now() + 3 },
      tailscale: { state: 'NeedsLogin', login_url: null },
      zerotier: { online: true, networks: [] },
    })[id]

  function applyAddons(name) {
    Object.assign(addons, { scenario: name, installed: {}, job: null, printerBusy: false, absent: false })
    const put = (id, extra = {}) => (addons.installed[id] = { version: ADDONS[id].available_version, foreign: false, active: 'active', ...fresh(id), ...extra })
    if (name === 'installed') {
      put('octoeverywhere')
      put('tailscale', { state: 'Running' })
      put('zerotier', {
        networks: [
          { id: HOME_NET, name: 'home', status: 'OK', addresses: ['10.147.17.12/24', 'fd80:56c2:e21c::1/88'] },
          { id: '1c33c1ced0123456', name: 'workshop', status: 'ACCESS_DENIED', addresses: [] },
        ],
      })
    } else if (name === 'linking') {
      put('obico', { code: 'K7Q2XZ', codeAt: 0 })
      put('tailscale', { state: 'NeedsLogin', login_url: 'https://login.tailscale.com/a/fake0123456789' })
      put('zerotier', { version: '1.14.2' })
    } else if (name === 'busy') {
      addons.printerBusy = true
      put('tailscale', { state: 'Stopped', active: 'inactive' })
    } else if (name === 'foreign') {
      put('octoeverywhere', { foreign: true, version: null })
    } else if (name === 'failed') {
      addons.jobs++
      addons.job = {
        id: `job-${addons.jobs}`,
        addon: 'zerotier',
        action: 'install',
        state: 'failed',
        started: now() - 40,
        finished: now() - 12,
        message: "ZeroTier's package couldn't be downloaded: download.zerotier.com didn't answer.",
        log: ['Installing ZeroTier 1.16.2', 'Downloading zerotier-one_1.16.2_arm64.deb', 'Download failed: timed out after 30 s', "ZeroTier's package couldn't be downloaded: download.zerotier.com didn't answer."],
      }
    } else if (name === 'absent') {
      addons.absent = true
    }
  }
  applyAddons(fake.args.addons ?? 'none')

  const status = (id, a) => {
    if (a.foreign) return null
    switch (id) {
      case 'octoeverywhere':
        return { connected: a.connected, printer_connected: true, linked: a.linked, printer_id: 'FAKEPRINTERID0001', link_url: 'https://octoeverywhere.com/getstarted?printerid=FAKEPRINTERID0001' }
      case 'obico': {
        const out = { server: a.server, cloud: a.server === 'https://app.obico.io', linked: a.linked, discovering: false }
        if (!a.linked) {
          out.discovering = a.discovering
          // The plugin gets its code from Obico's server a few seconds after it starts waiting.
          if (a.discovering && (a.code || now() >= a.codeAt)) out.link_code = a.code ??= 'R4M8TB'
        }
        return out
      }
      case 'tailscale': {
        const running = a.state === 'Running'
        const out = {
          state: a.state,
          connected: running,
          name: running ? 'qidi-max4.tail1234.ts.net' : '',
          addresses: running ? ['100.101.102.103', 'fd7a:115c:a1e0::6b01:6667'] : [],
          tailnet: running ? 'example.com' : '',
          key_expiry: running ? new Date(Date.now() + 180 * 86400e3).toISOString() : null,
          problems: [],
        }
        if (a.state === 'NeedsLogin') out.login_url = a.login_url
        return out
      }
      case 'zerotier':
        return { node_id: NODE_ID, online: a.online, networks: a.networks.map((n) => ({ ...n, status: n.status.toLowerCase() })) }
    }
    return null
  }

  function list() {
    const installedGroups = Object.entries(addons.installed).map(([id]) => ADDONS[id])
    return {
      busy: addons.job?.state === 'running',
      printer_busy: addons.printerBusy || state.status.print_stats?.state === 'printing',
      addons: Object.values(ADDONS).map((d) => {
        const a = addons.installed[d.id]
        const st = !a ? 'not_installed' : a.foreign ? 'foreign' : a.version !== d.available_version ? 'update_available' : 'installed'
        const blockers = !a && d.group ? installedGroups.filter((o) => o.group === d.group && o.id !== d.id).map((o) => o.name) : []
        return {
          id: d.id,
          name: d.name,
          description: d.description,
          homepage: d.homepage,
          available_version: d.available_version,
          installed_version: a?.version ?? null,
          state: st,
          service: a ? { name: d.service, active: a.active } : null,
          blocked_by: blockers,
          status: a && (st === 'installed' || st === 'update_available') ? status(d.id, a) : null,
        }
      }),
      job: addons.job,
    }
  }

  // A job logs a few lines over a few seconds, then changes the add-on.
  function startJob(id, action, body) {
    const d = ADDONS[id]
    const verb = { install: 'Installing', update: 'Updating', remove: 'Removing', relink: 'Restarting' }[action]
    const steps =
      action === 'remove'
        ? [`Stopping ${d.service}`, `Removing ${d.name}`, `${d.name} removed`]
        : action === 'relink'
          ? [`Stopping ${d.service}`, body.server ? `Setting the server to ${body.server}` : 'Clearing the link', `Starting ${d.service}`]
          : [`Unpacking ${d.name} ${d.available_version}`, `Writing ${d.service}.service`, `Starting ${d.service}`, `${d.name} ${d.available_version} is running`]
    addons.jobs++
    const job = { id: `job-${addons.jobs}`, addon: id, action, state: 'running', started: now(), finished: null, message: steps[0], log: [`${verb} ${d.name}`, steps[0]] }
    addons.job = job
    let i = 0
    const tick = setInterval(() => {
      if (addons.job !== job) return clearInterval(tick)
      i++
      if (i < steps.length) {
        job.message = steps[i]
        job.log = [...job.log, steps[i]]
        return
      }
      clearInterval(tick)
      if (action === 'remove') delete addons.installed[id]
      else if (action === 'relink') Object.assign(addons.installed[id], { linked: false, discovering: true, code: null, codeAt: now() + 3, ...(body.server ? { server: body.server.replace(/\/+$/, '') } : {}) })
      else addons.installed[id] = { ...addons.installed[id], version: d.available_version, foreign: false, active: 'active', ...(action === 'install' ? fresh(id) : {}) }
      Object.assign(job, { state: 'succeeded', finished: now(), message: '' })
    }, 1200)
    return job
  }

  // Actions the service answers at once rather than running as jobs.
  function extra(id, action, body) {
    const a = addons.installed[id]
    if (id === 'octoeverywhere' && action === 'link-code') return { code: '482913', url: CODE_PAGE, expires_in: 600 }
    if (id === 'tailscale' && action === 'login') {
      if (a.state === 'NeedsLogin') {
        a.login_url = `https://login.tailscale.com/a/fake${Math.random().toString(16).slice(2, 12)}`
        // Signing in elsewhere connects it a little later.
        setTimeout(() => a.state === 'NeedsLogin' && a.login_url && Object.assign(a, { state: 'Running', login_url: null }), 20000)
      } else a.state = 'Running'
      return status(id, a)
    }
    if (id === 'tailscale' && action === 'logout') return status(id, Object.assign(a, { state: 'NeedsLogin', login_url: null }))
    if (id === 'zerotier' && (action === 'join' || action === 'leave')) {
      const net = typeof body.network === 'string' ? body.network.trim().toLowerCase() : ''
      if (!/^[0-9a-f]{16}$/.test(net)) throw Object.assign(new Error('A ZeroTier network ID is 16 characters, 0-9 and a-f.'), { status: 400 })
      if (action === 'leave') a.networks = a.networks.filter((n) => n.id !== net)
      else if (!a.networks.some((n) => n.id === net)) {
        const n = { id: net, name: '', status: 'REQUESTING_CONFIGURATION', addresses: [] }
        a.networks.push(n)
        // The home network authorizes it; any other waits for approval.
        setTimeout(() => Object.assign(n, net === HOME_NET ? { name: 'home', status: 'OK', addresses: ['10.147.17.12/24'] } : { name: 'other', status: 'ACCESS_DENIED' }), 4000)
      }
      return status(id, a)
    }
    return undefined
  }

  const send = (res, code, body) => {
    res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
    res.end(JSON.stringify(body))
    return true
  }

  fake.http(async (req, res, path) => {
    if (!path.startsWith('/tltg-addons/')) return false
    if (addons.absent) return send(res, 404, { error: 'Not found.' })
    const route = path.slice('/tltg-addons/'.length).split('/').filter(Boolean)
    if (route[0] !== 'api') return send(res, 404, { error: 'Not found.' })
    if (req.method === 'GET') return route.length === 2 && route[1] === 'addons' ? send(res, 200, list()) : send(res, 404, { error: 'Not found.' })
    if (req.method !== 'POST') return send(res, 405, { error: 'Method not allowed.' })
    if (req.headers['x-valve-oil'] !== 'addons') return send(res, 403, { error: 'Missing X-Valve-Oil header.' })
    let raw = ''
    for await (const chunk of req) raw += chunk
    let body = {}
    try {
      body = raw ? JSON.parse(raw) : {}
    } catch {
      return send(res, 400, { error: 'Expected a JSON request.' })
    }
    const [, kind, id, action] = route
    if (kind !== 'addons' || !ADDONS[id] || !action) return send(res, 404, { error: 'Not found.' })
    try {
      if (addons.installed[id] && !addons.installed[id].foreign) {
        const answer = extra(id, action, body)
        if (answer !== undefined) return send(res, 200, answer)
      }
    } catch (e) {
      return send(res, e.status ?? 502, { error: e.message })
    }
    if (!['install', 'update', 'remove', 'relink'].includes(action)) return send(res, 404, { error: 'Not found.' })
    if (addons.job?.state === 'running') return send(res, 409, { error: 'Another add-on change is running.' })
    if (action !== 'remove' && list().printer_busy) return send(res, 409, { error: "Add-ons can be changed only while the printer isn't printing or calibrating." })
    if (action === 'install' && list().addons.find((x) => x.id === id).blocked_by.length) return send(res, 409, { error: 'Only one remote access add-on can be installed.' })
    return send(res, 202, { job: startJob(id, action, body) })
  })

  fake.gcode((C, p) => {
    if (C !== 'VO_ADDONS') return false
    applyAddons((p.NAME ?? 'none').toLowerCase())
    fake.pushConsole(`// Add-ons: ${addons.scenario}`)
    return true
  })
  fake.commands({ VO_ADDONS: 'Fake: switch the add-ons service to a scenario (NAME=none|installed|linking|busy|foreign|failed|absent)' })
}

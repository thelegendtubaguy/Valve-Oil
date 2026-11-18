// History screen fakes: print history with QIDI-shaped metadata, Moonraker's job queue, and moonraker-timelapse (its
// settings, frames during a print, renders, saved frames, and a `timelapse` folder of videos and thumbnails).
//
// Moonraker on the printers loads job_queue and timelapse; this adds them to server.info's components. The timelapse
// folders are listed through server.files.list and get_directory and served at /server/files/timelapse/; other roots
// fall through to the handlers this replaces.

// A 2 s, 128x72 H.264 test pattern, so the player has something real to play.
const VIDEO = Buffer.from('AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAP1bW9vdgAAAGxtdmhkAAAAAAAAAAAAAAAAAAAD6AAAB9AAAQAAAQAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAgAAAyB0cmFrAAAAXHRraGQAAAADAAAAAAAAAAAAAAABAAAAAAAAB9AAAAAAAAAAAAAAAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAABAAAAAAIAAAABIAAAAAAAkZWR0cwAAABxlbHN0AAAAAAAAAAEAAAfQAAAQAAABAAAAAAKYbWRpYQAAACBtZGhkAAAAAAAAAAAAAAAAAABAAAAAgABVxAAAAAAALWhkbHIAAAAAAAAAAHZpZGUAAAAAAAAAAAAAAABWaWRlb0hhbmRsZXIAAAACQ21pbmYAAAAUdm1oZAAAAAEAAAAAAAAAAAAAACRkaW5mAAAAHGRyZWYAAAAAAAAAAQAAAAx1cmwgAAAAAQAAAgNzdGJsAAAAw3N0c2QAAAAAAAAAAQAAALNhdmMxAAAAAAAAAAEAAAAAAAAAAAAAAAAAAAAAAIAASABIAAAASAAAAAAAAAABFExhdmM2My4xLjEwMiBsaWJ4MjY0AAAAAAAAAAAAAAAAGP//AAAAOWF2Y0MBZAAL/+EAG2dkAAuscgRCC/lwEQAAAwABAAADABAPFCmEYAEAB2joQ4E0siz9+PgAAAAAEHBhc3AAAAABAAAAAQAAABRidHJ0AAAAAAAANoQAAAAAAAAAGHN0dHMAAAAAAAAAAQAAABAAAAgAAAAAFHN0c3MAAAAAAAAAAQAAAAEAAACIY3R0cwAAAAAAAAAPAAAAAQAAEAAAAAABAAAgAAAAAAIAAAgAAAAAAQAAKAAAAAABAAAQAAAAAAEAAAAAAAAAAQAACAAAAAABAAAoAAAAAAEAABAAAAAAAQAAAAAAAAABAAAIAAAAAAEAACgAAAAAAQAAEAAAAAABAAAAAAAAAAEAAAgAAAAAHHN0c2MAAAAAAAAAAQAAAAEAAAAQAAAAAQAAAFRzdHN6AAAAAAAAAAAAAAAQAAAE6wAAASkAAAA/AAAAQwAAATQAAAB7AAAAUgAAAEkAAAE6AAAAgQAAAEwAAABOAAAAxwAAAKUAAABmAAAAmgAAABRzdGNvAAAAAAAAAAEAAAQlAAAAYXVkdGEAAABZbWV0YQAAAAAAAAAhaGRscgAAAAAAAAAAbWRpcmFwcGwAAAAAAAAAAAAAAAAsaWxzdAAAACSpdG9vAAAAHGRhdGEAAAABAAAAAExhdmY2My4xLjEwMgAAAAhmcmVlAAANqW1kYXQAAAKvBgX//6vcRem95tlIt5Ys2CDZI+7veDI2NCAtIGNvcmUgMTY1IHIzMjIyIGIzNTYwNWEgLSBILjI2NC9NUEVHLTQgQVZDIGNvZGVjIC0gQ29weWxlZnQgMjAwMy0yMDI1IC0gaHR0cDovL3d3dy52aWRlb2xhbi5vcmcveDI2NC5odG1sIC0gb3B0aW9uczogY2FiYWM9MSByZWY9MTYgZGVibG9jaz0xOjA6MCBhbmFseXNlPTB4MzoweDEzMyBtZT11bWggc3VibWU9MTAgcHN5PTEgcHN5X3JkPTEuMDA6MC4wMCBtaXhlZF9yZWY9MSBtZV9yYW5nZT0yNCBjaHJvbWFfbWU9MSB0cmVsbGlzPTIgOHg4ZGN0PTEgY3FtPTAgZGVhZHpvbmU9MjEsMTEgZmFzdF9wc2tpcD0xIGNocm9tYV9xcF9vZmZzZXQ9LTIgdGhyZWFkcz0yIGxvb2thaGVhZF90aHJlYWRzPTEgc2xpY2VkX3RocmVhZHM9MCBucj0wIGRlY2ltYXRlPTEgaW50ZXJsYWNlZD0wIGJsdXJheV9jb21wYXQ9MCBjb25zdHJhaW5lZF9pbnRyYT0wIGJmcmFtZXM9OCBiX3B5cmFtaWQ9MiBiX2FkYXB0PTIgYl9iaWFzPTAgZGlyZWN0PTMgd2VpZ2h0Yj0xIG9wZW5fZ29wPTAgd2VpZ2h0cD0yIGtleWludD0yNTAga2V5aW50X21pbj04IHNjZW5lY3V0PTQwIGludHJhX3JlZnJlc2g9MCByY19sb29rYWhlYWQ9NjAgcmM9Y3JmIG1idHJlZT0xIGNyZj00NS4wIHFjb21wPTAuNjAgcXBtaW49MCBxcG1heD02OSBxcHN0ZXA9NCBpcF9yYXRpbz0xLjQwIGFxPTE6MS4wMACAAAACNGWIgQA/ip+cjQ570i7ILToBlV6WAMQrURNZV2arR4fvalki6BMgD7iYLUis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', 'base64')

const PRINTS = [
  { path: 'planter_ribbed.gcode.3mf', est: 7260, types: 'PLA;PLA', colors: ['#F2EFE8', '#2F6BFF'], weight: 36.4, length: 12100 },
  { path: 'cable_clip_x12.gcode', est: 2280, types: 'PETG', colors: ['#FF7A1A'], weight: 9.8, length: 3270 },
  { path: 'gear_m2_32t.gcode.3mf', est: 4320, types: 'ABS', colors: ['#8A8F98'], weight: 21.2, length: 7400 },
  { path: 'wall_hook.gcode', est: 1320, types: 'PLA', colors: ['#1D1D1F'], weight: 6.1, length: 2030 },
  { path: 'enclosure_lid.gcode.3mf', est: 16800, types: 'PETG', colors: ['#FF7A1A'], weight: 142.7, length: 47600 },
  { path: 'spool_holder.gcode', est: 6600, types: 'PLA', colors: ['#F2EFE8'], weight: 48.3, length: 16100 },
  { path: 'phone_stand.gcode.3mf', est: 3480, types: 'PLA;PLA;PLA', colors: ['#1D1D1F', '#F2EFE8', '#2F6BFF'], weight: 27.5, length: 9200 },
  { path: 'calibration/flow_cube.gcode', est: 900, types: 'PLA', colors: ['#2F6BFF'], weight: 3.2, length: 1060 },
  // Deleted since they were printed.
  { path: 'benchy.gcode.3mf', est: 2940, types: 'PLA', colors: ['#E5484D'], weight: 14.9, length: 4970, gone: true },
  { path: 'old/bracket_v1.gcode', est: 5100, types: 'PETG', colors: ['#2F6BFF'], weight: 31, length: 10300, gone: true },
]

// Deterministic, so every run shows the same history.
let seed = 7
const rand = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646

function meta(f, now) {
  const name = f.path.split('/').pop().replace(/\.gcode(\.3mf)?$/, '')
  const n = f.types.split(';').length
  return {
    size: 400000 + Math.round(f.est * 61),
    modified: now - 86400 * 70,
    uuid: `fake-${name}`,
    slicer: 'OrcaSlicer',
    slicer_version: '2.3.1',
    gcode_start_byte: 4709,
    gcode_end_byte: 380000 + f.est * 60,
    layer_height: '0.2',
    first_layer_height: 0.2,
    object_height: 20 + (f.est % 70),
    nozzle_diameter: 0.4,
    estimated_time: f.est,
    filament_total: f.length,
    filament_weight_total: f.weight,
    filament_type: f.types,
    filament_name: f.types
      .split(';')
      .map((t) => `QIDI ${t} Basic @Qidi X-Max 4 0.4 nozzle`)
      .join(';'),
    // QIDI's Moonraker sends the slicer's four preset colors and the extruders the file uses.
    preset_colours: [...f.colors, '#FFE6A6', '#80591A', '#898F9B'].slice(0, 4),
    used_extruders: Array.from({ length: n }, (_, i) => i),
    nozzle_temperature_initial_layer: null,
    thumbnails: [
      { width: 32, height: 32, size: 900, relative_path: `.thumbs/${name}-32x32.png` },
      { width: 300, height: 300, size: 30000, relative_path: `.thumbs/${name}-300x300.png` },
    ],
  }
}

export function install(fake) {
  const { state, now, broadcast, set } = fake
  const t0 = now()

  // ---------------- history ----------------
  // About forty jobs over two months, newest first, with varied outcomes. state.history is shared with the core
  // fake (Home's history card reads it too), so it's refilled in place.
  const jobs = []
  let nextId = 2000
  let t = t0 - 3600 * 3
  while (jobs.length < 42) {
    const f = PRINTS[Math.floor(rand() * PRINTS.length)]
    t -= 3600 * (8 + rand() * 30)
    const r = rand()
    const status = r < 0.72 ? 'completed' : r < 0.84 ? 'cancelled' : r < 0.91 ? 'error' : r < 0.95 ? 'klippy_shutdown' : r < 0.98 ? 'interrupted' : 'server_exit'
    const done = status === 'completed' ? 1 : 0.05 + rand() * 0.8
    const print = Math.round(f.est * (0.92 + rand() * 0.18) * done)
    const job = {
      job_id: (nextId++).toString(16).padStart(6, '0'),
      user: '_TRUSTED_USER_',
      filename: f.path,
      status,
      start_time: t,
      end_time: t + print + 240 + Math.round(rand() * 300),
      print_duration: print,
      total_duration: print + 240 + Math.round(rand() * 300),
      filament_used: Math.round(f.length * done * (0.97 + rand() * 0.06) * 100) / 100,
      metadata: meta(f, t0),
      // Moonraker's sensor component adds fields like this one to jobs when configured.
      auxiliary_data: jobs.length % 9 === 4 ? [{ provider: 'sensor power_meter', name: 'energy', value: Math.round(print * 0.00009 * 100) / 100, description: 'Printer energy', units: 'kWh' }] : [],
      exists: !f.gone,
    }
    jobs.push(job)
  }
  state.history.length = 0
  state.history.push(...jobs)

  let totalsSince = 0
  const totals = () => {
    const js = state.history.filter((j) => j.start_time >= totalsSince && j.status !== 'in_progress')
    const sum = (k) => js.reduce((a, j) => a + j[k], 0)
    return {
      total_jobs: js.length,
      total_time: sum('total_duration'),
      total_print_time: sum('print_duration'),
      total_filament_used: sum('filament_used'),
      longest_job: Math.max(0, ...js.map((j) => j.total_duration)),
      longest_print: Math.max(0, ...js.map((j) => j.print_duration)),
    }
  }

  fake.handle({
    'server.history.list': (p) => {
      let list = [...state.history]
      if (p.since != null) list = list.filter((j) => j.start_time > p.since)
      if (p.before != null) list = list.filter((j) => j.start_time < p.before)
      if (p.order === 'asc') list.reverse()
      const start = p.start ?? 0
      const limit = p.limit ?? 50
      return { count: list.length, jobs: limit === 0 ? list.slice(start) : list.slice(start, start + limit) }
    },
    'server.history.get_job': (p) => {
      const job = state.history.find((j) => j.job_id === p.uid)
      if (!job) throw Object.assign(fake.rpcError(`Invalid job uid: ${p.uid}`), { code: 404 })
      return { job }
    },
    'server.history.delete_job': (p) => {
      if (p.all) {
        const ids = state.history.map((j) => j.job_id)
        state.history.length = 0
        return { deleted_jobs: ids }
      }
      const i = state.history.findIndex((j) => j.job_id === p.uid)
      if (i < 0) throw Object.assign(fake.rpcError(`Invalid job uid: ${p.uid}`), { code: 404 })
      state.history.splice(i, 1)
      return { deleted_jobs: [p.uid] }
    },
    'server.history.totals': () => ({ job_totals: totals(), auxiliary_totals: [] }),
    'server.history.reset_totals': () => {
      const last = totals()
      totalsSince = now()
      return { last_totals: last, last_auxiliary_totals: [] }
    },
  })

  // ---------------- job queue ----------------
  const q = { state: 'paused', jobs: [] }
  let qid = 0x4a10
  const queued = (filename, ago = 0) => ({ filename, job_id: (qid++).toString(16).toUpperCase().padStart(10, '0'), time_added: now() - ago, time_in_queue: ago })
  q.jobs.push(queued('spool_holder.gcode', 3600), queued('cable_clip_x12.gcode', 3500), queued('wall_hook.gcode', 1200), queued('old/bracket_v1.gcode', 900))
  const qStatus = () => {
    for (const j of q.jobs) j.time_in_queue = now() - j.time_added
    return { queued_jobs: q.jobs, queue_state: q.state }
  }
  const qChanged = (action) => {
    broadcast('notify_job_queue_changed', [{ action, updated_queue: action === 'state_changed' ? null : qStatus().queued_jobs, queue_state: q.state }])
    return qStatus()
  }
  const idle = () => !['printing', 'paused'].includes(state.status.print_stats?.state)
  const startPrint = (filename) => {
    set('print_stats', { filename, state: 'printing', print_duration: 0, total_duration: 0, filament_used: 0, message: '', info: { current_layer: 1, total_layer: 240 } })
    set('virtual_sdcard', { progress: 0, is_active: true })
    set('display_status', { progress: 0 })
    set('idle_timeout', { state: 'Printing' })
  }
  // Moonraker loads the next job when the queue is started with the printer idle, and after each print while ready.
  let loading = null
  const loadNext = () => {
    if (!q.jobs.length || !idle() || loading) return
    q.state = 'loading'
    qChanged('state_changed')
    loading = setTimeout(() => {
      loading = null
      const j = q.jobs.shift()
      q.state = 'ready'
      qChanged('job_loaded')
      if (j) startPrint(j.filename)
    }, 1200)
  }
  fake.handle({
    'server.job_queue.status': qStatus,
    'server.job_queue.post_job': (p) => {
      const names = p.filenames ?? (p.filename ? [p.filename] : [])
      if (p.reset) {
        // Reordering re-posts the same files; keep each file's queued time.
        const old = [...q.jobs]
        q.jobs = names.map((n) => {
          const i = old.findIndex((j) => j.filename === n)
          return i >= 0 ? old.splice(i, 1)[0] : queued(n)
        })
      } else q.jobs.push(...names.map((n) => queued(n)))
      return qChanged('jobs_added')
    },
    'server.job_queue.delete_job': (p) => {
      if (p.all) q.jobs = []
      else {
        const ids = new Set(p.job_ids ?? [p.job_id])
        q.jobs = q.jobs.filter((j) => !ids.has(j.job_id))
      }
      return qChanged('jobs_removed')
    },
    'server.job_queue.pause': () => ((q.state = 'paused'), qChanged('state_changed')),
    'server.job_queue.start': () => {
      if (loading) return qStatus()
      q.state = 'ready'
      qChanged('state_changed')
      loadNext()
      return qStatus()
    },
    'server.job_queue.jump': (p) => {
      const i = q.jobs.findIndex((j) => j.job_id === p.job_id)
      if (i < 0) throw fake.rpcError(`Invalid job id: ${p.job_id}`)
      q.jobs.unshift(...q.jobs.splice(i, 1))
      return qChanged('jobs_added')
    },
  })

  // ---------------- timelapse ----------------
  // The Max 4's moonraker-timelapse settings, as Moonraker reports them.
  const tl = {
    settings: { enabled: true, mode: 'layermacro', camera: '', snapshoturl: 'http://localhost/webcam/?action=snapshot', stream_delay_compensation: 0.05, gcode_verbose: false, parkhead: false, parkpos: 'back_left', park_custom_pos_x: 10.0, park_custom_pos_y: 10.0, park_custom_pos_dz: 0.0, park_travel_speed: 100, park_retract_speed: 15, park_extrude_speed: 15, park_retract_distance: 1.0, park_extrude_distance: 1.0, park_time: 0.1, fw_retract: false, hyperlapse_cycle: 30, autorender: true, constant_rate_factor: 23, output_framerate: 30, pixelformat: 'yuv420p', time_format_code: '%Y%m%d_%H%M', extraoutputparams: '', variable_fps: false, targetlength: 10, variable_fps_min: 5, variable_fps_max: 60, rotation: 0, flip_x: false, flip_y: false, duplicatelastframe: 5, previewimage: true, saveframes: false, blockedsettings: [] },
    frames: 0,
    rendering: false,
    files: [],
  }
  const stamp = (secs) => {
    const d = new Date(secs * 1000)
    const p = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}`
  }
  const base = (print, secs) => `timelapse_${print.split('/').pop()}_${stamp(secs)}`
  const addRender = (print, secs, size) => {
    const b = base(print, secs)
    tl.files.push({ path: `${b}.mp4`, modified: secs, size, permissions: 'rw' }, { path: `${b}.jpg`, modified: secs + 0.3, size: 151000 + Math.round(rand() * 30000), permissions: 'rw' })
    return b
  }
  for (const j of state.history.filter((j) => j.status === 'completed' && j.exists).slice(0, 4)) addRender(j.filename, j.end_time, 240000 + Math.round(rand() * 8e6))
  // A thumbnail whose video was deleted, as on the Max 4.
  tl.files.push({ path: `timelapse_benchy.gcode.3mf_${stamp(t0 - 86400 * 40)}.jpg`, modified: t0 - 86400 * 40, size: 148000, permissions: 'rw' })

  const fileEvent = (action, path) => broadcast('notify_filelist_changed', [{ action, item: { root: 'timelapse', path, modified: now(), size: 0, permissions: 'rw' } }])

  const render = async () => {
    if (tl.rendering) return { action: 'render', status: 'error', msg: 'Rendering already in progress' }
    if (!tl.frames) return { action: 'render', status: 'error', msg: 'no frames to render, skip' }
    tl.rendering = true
    const print = state.status.print_stats?.filename || 'print.gcode'
    const started = { action: 'render', status: 'started', msg: 'Rendering Video...', framecount: tl.frames, settings: { framerate: tl.settings.output_framerate, crf: tl.settings.constant_rate_factor, pixelformat: tl.settings.pixelformat } }
    broadcast('notify_timelapse_event', [started])
    ;(async () => {
      for (let p = 0; p <= 100; p += 10) {
        await new Promise((r) => setTimeout(r, 450))
        broadcast('notify_timelapse_event', [{ action: 'render', status: 'running', progress: p }])
      }
      const b = addRender(print, now(), 180000 + tl.frames * 3000)
      fileEvent('create_file', `${b}.mp4`)
      fileEvent('create_file', `${b}.jpg`)
      tl.rendering = false
      broadcast('notify_timelapse_event', [{ action: 'render', status: 'success', msg: `Rendering Video successful: ${b}.mp4`, filename: `${b}.mp4`, printfile: print, previewimage: `${b}.jpg`, framecount: tl.frames }])
    })()
    return started
  }

  fake.handle({
    'machine.timelapse.get_settings': () => tl.settings,
    'machine.timelapse.post_settings': (p) => {
      for (const [k, v] of Object.entries(p)) if (k in tl.settings && !tl.settings.blockedsettings.includes(k) && k !== 'blockedsettings') tl.settings[k] = v
      return tl.settings
    },
    'machine.timelapse.lastframeinfo': () => ({ framecount: tl.frames, lastframefile: tl.frames ? `frame${String(tl.frames).padStart(6, '0')}.jpg` : '' }),
    'machine.timelapse.render': render,
    'machine.timelapse.saveframes': () => {
      if (!tl.frames) return { action: 'saveframes', status: 'skipped' }
      const b = `${base(state.status.print_stats?.filename || 'print.gcode', now())}_frames.zip`
      tl.files.push({ path: b, modified: now(), size: tl.frames * 140000, permissions: 'rw' })
      fileEvent('create_file', b)
      return { action: 'saveframes', status: 'finished', zipfile: `timelapse/${b}` }
    },
  })

  // The timelapse folder and its files.
  const listTimelapse = () => tl.files.map((f) => ({ ...f }))
  const deleteTimelapse = (rel) => {
    const i = tl.files.findIndex((f) => f.path === rel)
    if (i < 0) throw Object.assign(fake.rpcError(`File ${rel} does not exist`), { code: 404 })
    tl.files.splice(i, 1)
    fileEvent('delete_file', rel)
    return { item: { root: 'timelapse', path: rel }, action: 'delete_file' }
  }
  // Chain onto whatever answers these for other roots (the core, or the Files plugin loaded earlier).
  const handlers = fake.handlers
  const prev = (m) => handlers[m]
  const listPrev = prev('server.files.list')
  const dirPrev = prev('server.files.get_directory')
  const deletePrev = prev('server.files.delete_file')
  const infoPrev = prev('server.info')
  fake.handle({
    'server.files.list': (p, ws) => (p.root === 'timelapse' ? listTimelapse() : p.root === 'timelapse_frames' ? frameList() : listPrev(p, ws)),
    'server.files.get_directory': (p, ws) => {
      const root = String(p.path ?? '').split('/')[0]
      if (root !== 'timelapse' && root !== 'timelapse_frames') return dirPrev(p, ws)
      const files = (root === 'timelapse' ? listTimelapse() : frameList()).map(({ path, ...f }) => ({ filename: path, ...f }))
      return { dirs: [], files, disk_usage: { total: 7.2e9, used: 3.1e9, free: 4.1e9 }, root_info: { name: root, permissions: root === 'timelapse' ? 'rw' : 'r' } }
    },
    'server.files.delete_file': (p, ws) => (String(p.path).startsWith('timelapse/') ? deleteTimelapse(p.path.slice(10)) : deletePrev(p, ws)),
    // Moonraker on the printers loads job_queue and timelapse, with their folders.
    'server.info': async (p, ws) => {
      const info = await infoPrev(p, ws)
      return { ...info, components: [...new Set([...(info.components ?? []), 'job_queue', 'timelapse'])], registered_directories: [...new Set([...(info.registered_directories ?? []), 'timelapse', 'timelapse_frames'])] }
    },
  })
  function frameList() {
    return Array.from({ length: tl.frames }, (_, i) => ({ path: `frame${String(i + 1).padStart(6, '0')}.jpg`, modified: now() - (tl.frames - i) * 10, size: 140000, permissions: 'r' }))
  }

  fake.http(async (req, res, path) => {
    const m = /^\/server\/files\/(timelapse|timelapse_frames)\/(.+)$/.exec(path)
    if (!m) return false
    const name = decodeURIComponent(m[2])
    if (m[1] === 'timelapse_frames') {
      const n = Number(/(\d+)/.exec(name)?.[1] ?? 0)
      res.writeHead(200, { 'Content-Type': 'image/svg+xml', 'Cache-Control': 'max-age=3600' })
      res.end(frameSvg(n, 240))
      return true
    }
    const f = tl.files.find((x) => x.path === name)
    if (!f) {
      res.writeHead(404).end('not found')
      return true
    }
    if (/\.mp4$/.test(name)) {
      res.writeHead(200, { 'Content-Type': 'video/mp4', 'Content-Length': VIDEO.length, 'Accept-Ranges': 'none' })
      res.end(VIDEO)
    } else if (/\.jpg$/.test(name)) {
      res.writeHead(200, { 'Content-Type': 'image/svg+xml' })
      res.end(frameSvg(240, 240, name))
    } else {
      res.writeHead(200, { 'Content-Type': 'application/zip', 'Content-Disposition': `attachment; filename="${name}"` })
      res.end(Buffer.concat([Buffer.from('PK\x05\x06'), Buffer.alloc(18)]))
    }
    return true
  })

  // ---------------- live: jobs, queue, and frames follow the simulated print ----------------
  let current = null
  let last = null
  setInterval(() => {
    const ps = state.status.print_stats
    if (!ps) return
    const active = ps.state === 'printing' || ps.state === 'paused'
    if (active && ps.filename && (!current || current.filename !== ps.filename)) {
      const f = PRINTS.find((x) => x.path === ps.filename)
      current = {
        job_id: (nextId++).toString(16).padStart(6, '0'),
        user: '_TRUSTED_USER_',
        filename: ps.filename,
        status: 'in_progress',
        start_time: now() - (ps.total_duration ?? 0),
        end_time: null,
        print_duration: 0,
        total_duration: 0,
        filament_used: 0,
        metadata: f ? meta(f, t0) : {},
        auxiliary_data: [],
        exists: true,
      }
      state.history.unshift(current)
      broadcast('notify_history_changed', [{ action: 'added', job: current }])
      // A new print starts a new timelapse; frames so far match its layer.
      tl.frames = Math.max(0, ps.info?.current_layer ?? 0)
      if (tl.frames) broadcast('notify_timelapse_event', [{ action: 'newframe', frame: String(tl.frames), framefile: `frame${String(tl.frames).padStart(6, '0')}.jpg` }])
    }
    if (current) Object.assign(current, { print_duration: ps.print_duration ?? 0, total_duration: ps.total_duration ?? 0, filament_used: ps.filament_used ?? 0 })
    if (active && tl.settings.enabled) {
      const layer = ps.info?.current_layer ?? 0
      if (layer > tl.frames) {
        tl.frames = layer
        broadcast('notify_timelapse_event', [{ action: 'newframe', frame: String(layer), framefile: `frame${String(layer).padStart(6, '0')}.jpg` }])
      }
    }
    if (!active && current && last !== ps.state) {
      current.status = ps.state === 'complete' ? 'completed' : ps.state === 'error' ? 'error' : 'cancelled'
      current.end_time = now()
      broadcast('notify_history_changed', [{ action: 'finished', job: current }])
      const finished = current
      current = null
      if (finished.status === 'completed' && tl.settings.enabled && tl.settings.autorender) render()
      if (q.state === 'ready') setTimeout(loadNext, 2000)
    }
    last = ps.state
  }, 1000)
}

/** A stand-in camera frame: the print growing with the frame number. */
function frameSvg(n, of, label) {
  const p = Math.min(1, n / of)
  const h = Math.round(200 * p)
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <defs><radialGradient id="r" cx=".5" cy=".1" r=".9"><stop offset="0" stop-color="#4a505c"/><stop offset=".7" stop-color="#15171c"/></radialGradient></defs>
  <rect width="1280" height="720" fill="url(#r)"/>
  <path d="M140 680 L1140 680 L980 470 L300 470Z" fill="#2a2e36"/>
  <rect x="540" y="${470 - h}" width="200" height="${h}" fill="#f2b05a" opacity=".9"/>
  <text x="30" y="700" fill="#fff8" font-family="monospace" font-size="22">${label ? label.replace(/[<&>]/g, '') : `frame ${n}`}</text>
</svg>`
}

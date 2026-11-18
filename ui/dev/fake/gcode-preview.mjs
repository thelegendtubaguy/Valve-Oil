// G-code preview: serves generated G-code for the fake's files at GET /server/files/gcodes/<path>, and the
// exclude_object objects of the file being printed.
//
// Each file is generated from its name, so it's the same on every request: two to four objects (a ribbed cylinder,
// a box, a hexagon), 100-200 layers (240 for the printing scenario's planter, matching its total_layer), a skirt on
// the first layer, walls, solid and sparse infill, arcs, retractions, fan changes, and on multi-color files a tool
// change and prime tower. `.gcode` files are written as OrcaSlicer writes them (;TYPE:, ;LAYER_CHANGE); `.gcode.3mf`
// files as QIDI Studio does (; FEATURE:, ; CHANGE_LAYER), inside a zip as Metadata/plate_1.gcode.
//
// While the printing scenario runs, virtual_sdcard.file_position follows its progress through the planter's G-code,
// and print_stats.info.current_layer, the toolhead, and exclude_object.current_object follow the line at that
// position. EXCLUDE_OBJECT NAME=<name> excludes an object. `--gcode-kbps <n>` limits file downloads to n KB/s.
import { crc32, deflateRawSync } from 'node:zlib'

const BED = { x: 390, y: 390 }
const PLANTER = 'planter_ribbed.gcode.3mf'
// Tools each file uses; the rest use one.
const TOOLS = { [PLANTER]: 2, 'phone_stand.gcode.3mf': 3 }

function hash(s) {
  let h = 2166136261
  for (const c of s) h = Math.imul(h ^ c.charCodeAt(0), 16777619)
  return h >>> 0
}

const r3 = (v) => Math.round(v * 1000) / 1000
const fmt = (v) => String(r3(v)).replace(/^(-?)0\./, '$1.')

// ---- shapes: closed outlines (counter-clockwise points) at a height ----
function circle(cx, cy, r, n = 72, rib = 0) {
  return Array.from({ length: n }, (_, i) => {
    const a = (i / n) * Math.PI * 2
    const rr = r + rib * Math.cos(a * 12)
    return [cx + rr * Math.cos(a), cy + rr * Math.sin(a)]
  })
}
const rect = (cx, cy, w, h) => [
  [cx - w / 2, cy - h / 2],
  [cx + w / 2, cy - h / 2],
  [cx + w / 2, cy + h / 2],
  [cx - w / 2, cy + h / 2],
]
const shrink = (pts, cx, cy, d) =>
  pts.map(([x, y]) => {
    const len = Math.hypot(x - cx, y - cy) || 1
    return [x - ((x - cx) / len) * d, y - ((y - cy) / len) * d]
  })

/** Lines filling a convex outline at an angle, every `step` mm, alternating direction (a zigzag). */
function fill(pts, angle, step) {
  const c = Math.cos(angle)
  const s = Math.sin(angle)
  const rot = pts.map(([x, y]) => [x * c + y * s, -x * s + y * c])
  const ys = rot.map((p) => p[1])
  const lines = []
  let flip = false
  for (let y = Math.min(...ys) + step / 2; y < Math.max(...ys); y += step) {
    const xs = []
    for (let i = 0; i < rot.length; i++) {
      const [x1, y1] = rot[i]
      const [x2, y2] = rot[(i + 1) % rot.length]
      if ((y1 <= y && y2 > y) || (y2 <= y && y1 > y)) xs.push(x1 + ((y - y1) / (y2 - y1)) * (x2 - x1))
    }
    if (xs.length < 2) continue
    let a = Math.min(...xs)
    let b = Math.max(...xs)
    if (b - a < 0.5) continue
    if (flip) [a, b] = [b, a]
    flip = !flip
    lines.push([
      [a * c - y * s, a * s + y * c],
      [b * c - y * s, b * s + y * c],
    ])
  }
  return lines
}

function objectsFor(path, h) {
  const base = path.split('/').pop().replace(/\.gcode(\.3mf)?$/, '')
  const count = path === PLANTER ? 3 : 2 + (h % 3)
  const kinds = ['ribbed', 'box', 'hex']
  const sizes = Array.from({ length: count }, (_, i) => 36 + ((h >>> (i * 3)) % 5) * 8)
  const width = sizes.reduce((a, b) => a + b, 0) + 20 * (count - 1)
  const out = []
  let left = BED.x / 2 - width / 2
  for (let i = 0; i < count; i++) {
    const size = sizes[i]
    out.push({ name: `${base}.stl_id_${i}_copy_0`, kind: kinds[(i + h) % 3], cx: left + size / 2, cy: BED.y / 2 + (i % 2 ? 18 : -12), size })
    left += size + 20
  }
  return out
}

/** The outline of an object at height fraction t (0 at the bed, 1 at the top). */
function outline(o, t) {
  const r = o.size / 2
  if (o.kind === 'ribbed') return circle(o.cx, o.cy, r * (0.82 + 0.18 * t), 72, 1.2)
  if (o.kind === 'hex') return circle(o.cx, o.cy, r * (1 - 0.35 * t), 6)
  return rect(o.cx, o.cy, o.size, o.size * 0.7)
}

function generate(path) {
  const h = hash(path)
  const studio = /\.3mf$/i.test(path)
  const layers = path === PLANTER ? 240 : 100 + (h % 101)
  const tools = TOOLS[path] ?? 1
  const objects = objectsFor(path, h)
  const lh = 0.2
  const feature = (name) => (studio ? `; FEATURE: ${name}` : `;TYPE:${name}`)

  const lines = []
  // For the live print: [filePosition, x, y, z, object] of each move, and each layer's starting position.
  const track = []
  const layerAt = []
  let pos = 0
  let x = 0
  let y = 0
  let z = 0
  let object = null
  const out = (l) => {
    lines.push(l)
    pos += Buffer.byteLength(l) + 1
  }
  const at = () => track.push([pos, x, y, z, object])
  const travel = (tx, ty) => {
    out('G1 E-.8 F2100')
    at()
    out(`G1 X${fmt(tx)} Y${fmt(ty)} F30000`)
    x = tx
    y = ty
    at()
    out('G1 E.8 F2100')
  }
  const loop = (pts, speed) => {
    travel(pts[0][0], pts[0][1])
    out(`G1 F${speed}`)
    for (const [px, py] of [...pts.slice(1), pts[0]]) {
      const e = Math.hypot(px - x, py - y) * 0.0333
      at()
      out(`G1 X${fmt(px)} Y${fmt(py)} E${fmt(e)}`)
      x = px
      y = py
    }
  }
  const arcCircle = (cx, cy, r, speed) => {
    travel(cx + r, cy)
    out(`G1 F${speed}`)
    const e = Math.PI * r * 0.0333
    at()
    out(`G3 X${fmt(cx - r)} Y${fmt(cy)} I${fmt(-r)} J0 E${fmt(e)}`)
    x = cx - r
    at()
    out(`G3 X${fmt(cx + r)} Y${fmt(cy)} I${fmt(r)} J0 E${fmt(e)}`)
    x = cx + r
  }
  const lines2 = (segs, speed) => {
    for (const [[ax, ay], [bx, by]] of segs) {
      if (Math.hypot(ax - x, ay - y) > 2) travel(ax, ay)
      else {
        at()
        out(`G1 X${fmt(ax)} Y${fmt(ay)} E${fmt(Math.hypot(ax - x, ay - y) * 0.0333)} F${speed}`)
        x = ax
        y = ay
      }
      at()
      out(`G1 X${fmt(bx)} Y${fmt(by)} E${fmt(Math.hypot(bx - ax, by - ay) * 0.0333)} F${speed}`)
      x = bx
      y = by
    }
  }

  out('; HEADER_BLOCK_START')
  out(studio ? '; QIDIStudio 02.03.00.70' : '; generated by OrcaSlicer 2.3.1')
  out(`; total layer number: ${layers}`)
  out('; HEADER_BLOCK_END')
  out('')
  for (const o of objects) {
    const poly = outline(o, 0).filter((_, i, a) => a.length < 20 || i % 4 === 0)
    out(`EXCLUDE_OBJECT_DEFINE NAME=${o.name} CENTER=${fmt(o.cx)},${fmt(o.cy)} POLYGON=${JSON.stringify(poly.map(([px, py]) => [r3(px), r3(py)]))}`)
  }
  out(feature('Custom'))
  out('PRINT_START BED=60 HOTEND=220 CHAMBER=0')
  out(`SET_PRINT_STATS_INFO TOTAL_LAYER=${layers}`)
  out('G90')
  out('M83 ; use relative distances for extrusion')
  out('T0')
  // Prime line along the front of the bed.
  out('G1 X20 Y4 Z.3 F30000')
  x = 20
  y = 4
  z = 0.3
  at()
  out('G1 X150 E12 F1800')
  x = 150

  let tool = 0
  const prime = tools > 1 ? rect(BED.x / 2, BED.y / 2 + 90, 30, 12) : null
  for (let n = 1; n <= layers; n++) {
    const t = n / layers
    z = r3(n * lh)
    out(studio ? '; CHANGE_LAYER' : ';LAYER_CHANGE')
    out(studio ? `; Z_HEIGHT: ${fmt(z)}` : `;Z:${fmt(z)}`)
    out(studio ? `; LAYER_HEIGHT: ${lh}` : `;HEIGHT:${lh}`)
    out('G1 E-.8 F2100')
    layerAt.push(pos)
    out(`SET_PRINT_STATS_INFO CURRENT_LAYER=${n}`)
    out(`G1 Z${fmt(z)} F600`)
    const fan = n === 1 ? 'M106 S0' : n === 2 ? 'M106 S153' : n % 40 === 0 ? 'M106 S204' : n % 40 === 3 ? 'M106 S255' : null
    if (fan) out(fan)

    // A colour band through the middle of multi-color prints.
    const want = tools > 1 && t > 0.45 && t < 0.55 ? 1 + (n % (tools - 1)) : 0
    if (want !== tool) {
      tool = want
      out(`T${tool}`)
    }
    if (prime) {
      out(feature('Prime tower'))
      lines2(fill(prime, n % 2 ? Math.PI / 4 : -Math.PI / 4, 1.2), 9000)
    }

    if (n === 1) {
      out(feature('Skirt'))
      const xs = objects.flatMap((o) => [o.cx - o.size / 2 - 6, o.cx + o.size / 2 + 6])
      const ys = objects.flatMap((o) => [o.cy - o.size / 2 - 6, o.cy + o.size / 2 + 6])
      loop(rect((Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)), 3000)
    }

    for (const o of objects) {
      const pts = outline(o, t)
      const first = n === 1
      object = o.name
      out(`EXCLUDE_OBJECT_START NAME=${o.name}`)
      out(feature('Outer wall'))
      if (o.kind === 'hex' && !first) arcCircle(o.cx, o.cy, o.size * 0.12, 9000)
      loop(pts, first ? 3000 : 12000)
      out(feature('Inner wall'))
      loop(shrink(pts, o.cx, o.cy, 0.45), first ? 3000 : 18000)
      if (o.kind === 'ribbed' && n % 30 > 26) {
        out(feature('Overhang wall'))
        loop(shrink(pts, o.cx, o.cy, -0.2), 1800)
      }
      const inner = shrink(pts, o.cx, o.cy, 0.9)
      const solid = n <= 3 || n > layers - 3
      out(feature(solid ? (n <= 3 ? 'Bottom surface' : 'Top surface') : 'Sparse infill'))
      lines2(fill(inner, n % 2 ? Math.PI / 4 : -Math.PI / 4, solid ? 0.45 : 6), first ? 3000 : solid ? 12000 : 16200)
      if (!solid && n % 7 === 0) {
        out(feature('Gap infill'))
        lines2(fill(shrink(pts, o.cx, o.cy, o.size * 0.3), 0, 3), 3000)
      }
      out(`EXCLUDE_OBJECT_END NAME=${o.name}`)
      object = null
    }
  }
  out(feature('Custom'))
  out('M107')
  out('PRINT_END')
  out('; EXECUTABLE_BLOCK_END')
  return { text: lines.join('\n') + '\n', track, layerAt, objects, size: pos }
}

// ---- a minimal zip, as a sliced 3MF ----
function zip(entries) {
  const locals = []
  const centrals = []
  let offset = 0
  for (const { name, data, deflate } of entries) {
    const n = Buffer.from(name)
    const stored = deflate ? deflateRawSync(data, { level: 6 }) : data
    const crc = crc32(data)
    const local = Buffer.alloc(30)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(20, 4)
    local.writeUInt16LE(deflate ? 8 : 0, 8)
    local.writeUInt32LE(crc, 14)
    local.writeUInt32LE(stored.length, 18)
    local.writeUInt32LE(data.length, 22)
    local.writeUInt16LE(n.length, 26)
    const central = Buffer.alloc(46)
    central.writeUInt32LE(0x02014b50, 0)
    central.writeUInt16LE(20, 4)
    central.writeUInt16LE(20, 6)
    central.writeUInt16LE(deflate ? 8 : 0, 10)
    central.writeUInt32LE(crc, 16)
    central.writeUInt32LE(stored.length, 20)
    central.writeUInt32LE(data.length, 24)
    central.writeUInt16LE(n.length, 28)
    central.writeUInt32LE(offset, 42)
    locals.push(local, n, stored)
    centrals.push(central, n)
    offset += 30 + n.length + stored.length
  }
  const dir = Buffer.concat(centrals)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(entries.length, 8)
  end.writeUInt16LE(entries.length, 10)
  end.writeUInt32LE(dir.length, 12)
  end.writeUInt32LE(offset, 16)
  return Buffer.concat([...locals, dir, end])
}

const cache = new Map()
function file(path) {
  let f = cache.get(path)
  if (!f) {
    const g = generate(path)
    const text = Buffer.from(g.text)
    const body = /\.3mf$/i.test(path)
      ? zip([
          { name: '[Content_Types].xml', data: Buffer.from('<?xml version="1.0" encoding="UTF-8"?>\n<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>\n') },
          { name: 'Metadata/plate_1.gcode', data: text, deflate: true },
        ])
      : text
    f = { ...g, body }
    cache.set(path, f)
  }
  return f
}

export function install(fake) {
  const kbps = Number(fake.args['gcode-kbps']) || 0
  fake.http(async (req, res, path) => {
    if (req.method !== 'GET' || !path.startsWith('/server/files/gcodes/') || path.includes('/.thumbs/')) return false
    const name = path.slice('/server/files/gcodes/'.length)
    if (!/\.gcode(\.3mf)?$/i.test(name)) return false
    const { body } = file(name)
    res.writeHead(200, { 'Content-Type': 'application/octet-stream', 'Content-Length': body.length, 'Access-Control-Expose-Headers': 'Content-Length' })
    // Sent in pieces with short pauses, so the download progress shows as it would over Wi-Fi; --gcode-kbps slows it
    // further to look at the progress.
    const chunk = 64 * 1024
    const pause = kbps ? (chunk / 1024 / kbps) * 1000 : 10
    for (let i = 0; i < body.length; i += chunk) {
      if (res.destroyed) break
      res.write(body.subarray(i, i + chunk))
      await new Promise((r) => setTimeout(r, pause))
    }
    res.end()
    return true
  })

  // The Max 4's X and Y travel, which the preview draws as the bed.
  fake.settings({
    exclude_object: {},
    stepper_x: { position_min: -2, position_max: 392, position_endstop: 392 },
    stepper_y: { position_min: -5, position_max: 410, position_endstop: -1 },
  })
  fake.commands({
    EXCLUDE_OBJECT: 'Cancel moves for an object',
    EXCLUDE_OBJECT_DEFINE: 'Provides a summary of an object',
    EXCLUDE_OBJECT_START: 'Marks the beginning the current object as labeled',
    EXCLUDE_OBJECT_END: 'Marks the end the current object',
  })

  const objectsOf = (path) =>
    path && /\.gcode(\.3mf)?$/i.test(path)
      ? file(path).objects.map((o) => {
          const poly = outline(o, 0).filter((_, i, a) => a.length < 20 || i % 4 === 0)
          return { name: o.name.toUpperCase(), center: [r3(o.cx), r3(o.cy)], polygon: poly.map(([px, py]) => [r3(px), r3(py)]) }
        })
      : []

  fake.status((st, scenario) => {
    st.exclude_object = scenario === 'printing' ? { objects: objectsOf(st.print_stats.filename), excluded_objects: [], current_object: null } : { objects: [], excluded_objects: [], current_object: null }
  })

  fake.gcode((C, p) => {
    if (C === 'EXCLUDE_OBJECT') {
      const ex = fake.state.status.exclude_object
      const name = (p.NAME ?? '').replace(/^"|"$/g, '').toUpperCase()
      if (!ex.objects.some((o) => o.name === name)) throw fake.rpcError(`Unknown object: ${name}`)
      if (!ex.excluded_objects.includes(name)) fake.set('exclude_object', { excluded_objects: [...ex.excluded_objects, name], current_object: ex.current_object === name ? null : ex.current_object })
      fake.pushConsole(`// Excluding object ${name}`)
      return true
    }
    return false
  })

  // Follow the print through its G-code, once a second after the core's own tick.
  let lastFile = ''
  setInterval(() => {
    const st = fake.state.status
    const ps = st.print_stats
    if (!ps || !ps.filename || !/\.gcode(\.3mf)?$/i.test(ps.filename)) return
    // A print started from the UI: its objects, as Klipper reads them from the file's EXCLUDE_OBJECT_DEFINE lines.
    if (ps.filename !== lastFile) {
      if (lastFile) fake.set('exclude_object', { objects: objectsOf(ps.filename), excluded_objects: [], current_object: null })
      lastFile = ps.filename
    }
    if (ps.state !== 'printing' && ps.state !== 'paused') return
    const f = file(ps.filename)
    const position = Math.round((st.virtual_sdcard.progress ?? 0) * f.size)
    let lo = 0
    let hi = f.track.length - 1
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1
      if (f.track[mid][0] <= position) lo = mid
      else hi = mid - 1
    }
    const [, x, y, z, object] = f.track[lo] ?? [0, 0, 0, 0, null]
    let layer = 0
    while (layer < f.layerAt.length && f.layerAt[layer] <= position) layer++
    fake.set('virtual_sdcard', { file_position: position })
    fake.set('print_stats', { info: { current_layer: Math.max(1, layer), total_layer: f.layerAt.length } })
    fake.set('toolhead', { position: [x, y, z, 0] })
    fake.set('gcode_move', { gcode_position: [x, y, z, 0] })
    fake.set('motion_report', { live_position: [x, y, z, 0] })
    const excluded = st.exclude_object?.excluded_objects ?? []
    const current = object ? object.toUpperCase() : null
    fake.set('exclude_object', { current_object: current && !excluded.includes(current) ? current : null })
  }, 1000)
}

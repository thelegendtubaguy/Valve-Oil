// A QIDI Box sketched in pencil for the spool tag writer: the lid opens, the view swings to the front right corner and
// then looks down into the Box, and the RFID reader the slot uses lights up. A Box has two readers, on the divider
// between slots A and B and on the one between C and D, and a spool's tag must be on the side facing its reader.
//
// It's a small hand-rolled renderer, not a 3D library: polygons projected through a camera and drawn back to front,
// solid by solid, each filled with the paper color so it hides what's behind it, outlined in graphite, and hatched
// where it faces away from the light. A turbulence filter makes the lines wobble like a hand-drawn sketch. Units are
// millimetre-ish, x to the right, y up, and z toward the front of the Box.
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { RotateCcw } from 'lucide-react'
import type { QidiBoxSlot } from '../../state/box'
import { t } from '../../i18n'

type V3 = [number, number, number]
/** fill: 'auto' shades by the light; 'paper', 'none', or a pattern key. stroke: 'ink', 'faint', 'accent', or 'none'. */
type Face = { pts: V3[]; n: V3; fill?: string; stroke?: 'ink' | 'faint' | 'accent' | 'none'; cull?: boolean; cls?: string }
type Solid = { center: V3; layer?: number; faces: (camPos: V3) => Face[] }

const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
const mul = (a: V3, k: number): V3 => [a[0] * k, a[1] * k, a[2] * k]
const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
const norm = (a: V3): V3 => mul(a, 1 / (Math.hypot(...a) || 1))
const mean = (ps: V3[]): V3 => mul(ps.reduce(add, [0, 0, 0] as V3), 1 / ps.length)
const fixed = (faces: Face[]) => () => faces

// ---- the Box's shape ----
const W = 200 // half width
const FRONT = 110
const BACK = -120
const BASE_H = 105
const REAR_H = 266
const SLOT_X = [-144, -48, 48, 144]
const DIVIDER_X = [-192, -96, 0, 96, 192]
const READER_X = [-96, 96] // between A and B, and between C and D
const SPOOL_R = 70
const CORE_R = 60
const SPOOL_HALF = 22
const SPOOL_Y = BASE_H + SPOOL_R + 2
const HINGE: [number, number] = [-110, 270] // z, y of the lid's hinge along the top of the rear wall

/** An axis-aligned box's six faces. */
function cuboid(x0: number, x1: number, y0: number, y1: number, z0: number, z1: number, extra: Partial<Face> = {}): Face[] {
  const p = (x: number, y: number, z: number): V3 => [x, y, z]
  return [
    { pts: [p(x0, y0, z1), p(x1, y0, z1), p(x1, y1, z1), p(x0, y1, z1)], n: [0, 0, 1], ...extra },
    { pts: [p(x1, y0, z0), p(x0, y0, z0), p(x0, y1, z0), p(x1, y1, z0)], n: [0, 0, -1], ...extra },
    { pts: [p(x1, y0, z1), p(x1, y0, z0), p(x1, y1, z0), p(x1, y1, z1)], n: [1, 0, 0], ...extra },
    { pts: [p(x0, y0, z0), p(x0, y0, z1), p(x0, y1, z1), p(x0, y1, z0)], n: [-1, 0, 0], ...extra },
    { pts: [p(x0, y1, z1), p(x1, y1, z1), p(x1, y1, z0), p(x0, y1, z0)], n: [0, 1, 0], ...extra },
    { pts: [p(x0, y0, z0), p(x1, y0, z0), p(x1, y0, z1), p(x0, y0, z1)], n: [0, -1, 0], ...extra },
  ]
}

/** A disc across the x axis, as a polygon. */
function disc(x: number, cy: number, cz: number, r: number, sign: 1 | -1, n = 30, extra: Partial<Face> = {}): Face {
  const pts: V3[] = Array.from({ length: n }, (_, i) => {
    const a = (i / n) * Math.PI * 2
    return [x, cy + Math.sin(a) * r, cz + Math.cos(a) * r]
  })
  return { pts, n: [sign, 0, 0], ...extra }
}

/**
 * A spool on its side along x. Its far flange draws first, then the filament (only the quads facing the camera, unlined,
 * with its two outline edges), then the near flange, its hub, and the tag, so a flange's rim shows past the filament.
 */
function spool(cx: number, patterns: string[] | null): Solid {
  const n = 30
  const pt = (x: number, a: number, r = CORE_R): V3 => [x, SPOOL_Y + Math.sin(a) * r, Math.cos(a) * r]
  return {
    center: [cx, SPOOL_Y, 0],
    faces: (cam) => {
      const flanges = ([-1, 1] as const).map((s) => {
        const x = cx + s * (SPOOL_HALF + 1.5)
        const facing = dot([s, 0, 0], sub(cam, [x, SPOOL_Y, 0])) > 0
        const faces: Face[] = [disc(x, SPOOL_Y, 0, SPOOL_R, s, n, { fill: 'paper', cull: false })]
        if (facing) {
          faces.push(disc(x + s * 0.3, SPOOL_Y, 0, 15, s, 20, { fill: 'shade2', cull: false }))
          faces.push(disc(x + s * 0.3, SPOOL_Y, 0, CORE_R, s, n, { fill: 'none', stroke: 'faint', cull: false }))
        }
        return { facing, faces }
      })
      const bands = patterns?.length ?? 1
      const body: Face[] = []
      for (let b = 0; b < bands; b++) {
        const x0 = cx - SPOOL_HALF + (b * 2 * SPOOL_HALF) / bands
        const x1 = cx - SPOOL_HALF + ((b + 1) * 2 * SPOOL_HALF) / bands
        for (let i = 0; i < n; i++) {
          const a0 = (i / n) * Math.PI * 2
          const a1 = ((i + 1) / n) * Math.PI * 2
          const am = (a0 + a1) / 2
          body.push({ pts: [pt(x0, a0), pt(x1, a0), pt(x1, a1), pt(x0, a1)], n: [0, Math.sin(am), Math.cos(am)], fill: patterns ? patterns[b] : 'auto', stroke: 'none' })
        }
        // Where the bands of a multicolor spool meet.
        if (b > 0) body.push(...silhouette(x0, x0, cam).map((f) => ({ ...f, stroke: 'faint' as const })))
      }
      body.push(...silhouette(cx - SPOOL_HALF, cx + SPOOL_HALF, cam))
      const far = flanges.find((f) => !f.facing)?.faces ?? []
      const near = flanges.find((f) => f.facing)?.faces ?? []
      return [...far, ...body, ...near]
    },
  }
  /** The filament's outline: the two lines along it where its surface turns away from the camera. */
  function silhouette(x0: number, x1: number, cam: V3): Face[] {
    const v = sub(cam, [cx, SPOOL_Y, 0])
    const a = Math.atan2(-v[2], v[1])
    if (x0 === x1) {
      // A ring around the filament at x0, the visible half.
      const pts: V3[] = Array.from({ length: n / 2 + 1 }, (_, i) => pt(x0, a + (i / (n / 2)) * Math.PI))
      return [{ pts, n: [0, 0, 0], fill: 'none', stroke: 'ink', cull: false }]
    }
    return [a, a + Math.PI].map((s) => ({ pts: [pt(x0, s), pt(x1, s)], n: [0, 0, 0] as V3, fill: 'none', stroke: 'ink' as const, cull: false }))
  }
}

/** The lid: a glass shell (front, top, and sides) hinged along the top of the rear wall, opened by `open` radians. */
function lid(open: number): Solid[] {
  const profile: [number, number][] = [
    [FRONT, BASE_H],
    [FRONT, 240],
    [HINGE[0], HINGE[1]],
    [HINGE[0], BASE_H],
  ]
  const turn = ([z, y]: [number, number]): [number, number] => {
    const dz = z - HINGE[0]
    const dy = y - HINGE[1]
    return [HINGE[0] + dz * Math.cos(open) - dy * Math.sin(open), HINGE[1] + dz * Math.sin(open) + dy * Math.cos(open)]
  }
  const pr = profile.map(turn)
  const at = (x: number, i: number): V3 => [x, pr[i][1], pr[i][0]]
  const glass = { fill: 'none', stroke: 'faint' as const, cull: false }
  const faces: Face[] = [
    { pts: [at(-W, 0), at(W, 0), at(W, 1), at(-W, 1)], n: [0, 0, 1], ...glass },
    { pts: [at(-W, 1), at(W, 1), at(W, 2), at(-W, 2)], n: [0, 1, 0], ...glass },
    { pts: [at(W, 0), at(W, 3), at(W, 2), at(W, 1)], n: [1, 0, 0], ...glass },
    { pts: [at(-W, 0), at(-W, 1), at(-W, 2), at(-W, 3)], n: [-1, 0, 0], ...glass },
  ]
  return [{ center: mean(faces.flatMap((f) => f.pts)), layer: 3, faces: fixed(faces) }]
}

function buildScene(slots: (string[] | null)[], target: number, open: number): Solid[] {
  const readerOf = target < 2 ? 0 : 1
  const solids: Solid[] = [
    { center: [0, BASE_H / 2, 0], layer: 0, faces: fixed(cuboid(-W, W, 0, BASE_H, BACK, FRONT, { fill: 'auto' })) },
    { center: [0, 185, BACK], layer: 0, faces: fixed(cuboid(-W, W, BASE_H, REAR_H, BACK, BACK + 10, { fill: 'auto' })) },
    { center: [0, BASE_H, FRONT], layer: 0, faces: fixed(cuboid(-20, 20, BASE_H - 12, BASE_H + 2, FRONT, FRONT + 4, { fill: 'paper' })) },
    { center: [0, BASE_H, 0], layer: 1, faces: fixed([{ pts: [[-192, BASE_H + 0.5, 100], [192, BASE_H + 0.5, 100], [192, BASE_H + 0.5, -108], [-192, BASE_H + 0.5, -108]], n: [0, 1, 0], fill: 'shade1' }]) },
  ]
  for (const x of DIVIDER_X) {
    solids.push({ center: [x, 140, 0], faces: fixed(cuboid(x - 2, x + 2, BASE_H, 168, -72, 72, { fill: 'auto' })) })
  }
  slots.forEach((patterns, i) => {
    if (patterns !== undefined) solids.push(spool(SLOT_X[i], patterns))
  })
  solids.push(...lid(open))
  // The readers and the tag draw last, over whatever hides them, as a drawing shows a hidden part that matters: the
  // slot's reader solid, the other faint, and the tag on the flange facing the reader (+x for A and C, -x for B and D).
  READER_X.forEach((x, r) => {
    const on = r === readerOf
    solids.push({ center: [x, 160, 0], layer: 4, faces: fixed(cuboid(x - 7, x + 7, 130, 178, -24, 24, on ? { fill: 'accent', stroke: 'accent', cls: 'bs-reader' } : { fill: 'none', stroke: 'faint' })) })
  })
  const side = target % 2 === 0 ? 1 : -1
  const fx = SLOT_X[target] + side * (SPOOL_HALF + 2.2)
  solids.push({ center: [fx, SPOOL_Y, 0], layer: 4, faces: fixed([disc(fx, SPOOL_Y + 22, 24, 10, side, 16, { fill: 'accent', stroke: 'accent', cull: false, cls: 'bs-tag' })]) })
  return solids
}

// ---- camera and drawing ----
type Cam = { pos: V3; right: V3; up: V3; fwd: V3 }
function camera(yaw: number, pitch: number, dist: number, target: V3): Cam {
  const pos = add(target, mul([Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), Math.cos(yaw) * Math.cos(pitch)], dist))
  const fwd = norm(sub(target, pos))
  const right = norm(cross(fwd, [0, 1, 0]))
  return { pos, fwd, right, up: cross(right, fwd) }
}

const VW = 560
const VH = 260
const FOCAL = 640
const LIGHT = norm([-0.3, 1, 0.6])

type Drawn = { d: string; fill: string; stroke: Face['stroke']; cls?: string }

function render(solids: Solid[], cam: Cam): Drawn[] {
  const proj = (p: V3) => {
    const d = sub(p, cam.pos)
    const z = Math.max(1, dot(d, cam.fwd))
    return [VW / 2 + (FOCAL * dot(d, cam.right)) / z, VH / 2 - (FOCAL * dot(d, cam.up)) / z]
  }
  const depth = (p: V3) => dot(sub(p, cam.pos), cam.fwd)
  const ordered = [...solids].sort((a, b) => (a.layer ?? 2) - (b.layer ?? 2) || ((a.layer ?? 2) === 2 || a.layer === 4 ? depth(b.center) - depth(a.center) : 0))
  const out: Drawn[] = []
  for (const s of ordered) {
    for (const f of s.faces(cam.pos)) {
      if (f.cull !== false && dot(f.n, sub(cam.pos, mean(f.pts))) <= 0) continue
      const open = f.pts.length === 2 || (f.fill === 'none' && f.stroke === 'ink')
      const d = 'M' + f.pts.map((p) => proj(p).map((v) => v.toFixed(1)).join(' ')).join(' L') + (open ? '' : 'Z')
      let fill = f.fill ?? 'paper'
      if (fill === 'auto') {
        const k = dot(f.n, LIGHT)
        fill = k > 0.55 ? 'paper' : k > 0.05 ? 'shade1' : 'shade2'
      }
      out.push({ d, fill, stroke: f.stroke ?? 'ink', cls: f.cls })
    }
  }
  return out
}

// ---- the animation ----
const ease = (t: number) => (t < 0 ? 0 : t > 1 ? 1 : t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2)
const span = (t: number, a: number, b: number) => ease((t - a) / (b - a))
const DEG = Math.PI / 180
const END = 3600

function pose(t: number) {
  // The view swings to the front right corner and rises to look down into the Box in one move.
  const move = span(t, 1600, 3500)
  return {
    open: span(t, 500, 1800) * 112 * DEG,
    cam: camera(move * 34 * DEG, (10 + move * 44) * DEG, 1010 + move * 30, [move * 10, 140 - move * 5, -move * 10]),
  }
}

/** The Box holding `slot`, its lid opening and the view swinging round to the reader that slot's tag passes. */
export function BoxScene({ slots, slot, colors }: { slots: QidiBoxSlot[]; slot: QidiBoxSlot; colors: string[] }) {
  const id = useId().replace(/:/g, '')
  const boxNo = Math.floor(slot.index / 4)
  const target = slot.index % 4
  const spoolColors = colors.length ? colors : ['#FFFFFF']
  // The slot being written is in its new colors; the others are plain outlines, or nothing when empty.
  const inBox = useMemo(
    () =>
      [0, 1, 2, 3].map((i) => {
        if (i === target) return spoolColors.map((_, k) => `c${k}`)
        return slots.find((x) => x.index === boxNo * 4 + i)?.present ? null : undefined
      }) as (string[] | null)[],
    [slots, boxNo, target, spoolColors],
  )
  const [ms, setMs] = useState(0)
  const [run, setRun] = useState(0)
  const raf = useRef(0)
  useEffect(() => {
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return void setMs(END)
    const start = performance.now()
    const step = (now: number) => {
      const e = now - start
      setMs(Math.min(e, END))
      if (e < END) raf.current = requestAnimationFrame(step)
    }
    raf.current = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf.current)
  }, [run])

  const p = pose(ms)
  const drawn = render(buildScene(inBox, target, p.open), p.cam)
  const fillOf = (k: string) => (k === 'paper' ? 'var(--bs-paper)' : k === 'none' ? 'none' : `url(#${id}-${k})`)
  const strokeOf = { ink: 'var(--bs-ink)', faint: 'var(--bs-faint)', accent: 'var(--accent)', none: 'none' }
  const hatch = (key: string, color: string, gap: number, angle: number, width = 0.9) => (
    <pattern key={key} id={`${id}-${key}`} patternUnits="userSpaceOnUse" width={gap} height={gap} patternTransform={`rotate(${angle})`}>
      <rect width={gap} height={gap} style={{ fill: 'var(--bs-paper)' }} />
      <line x1={0} y1={0} x2={0} y2={gap} style={{ stroke: color, strokeWidth: width }} />
    </pattern>
  )

  return (
    <div className="bs">
      <svg viewBox={`0 0 ${VW} ${VH}`} role="img" aria-label={target < 2 ? t('box.scene.sketch_ab', { slot: slot.label }) : t('box.scene.sketch_cd', { slot: slot.label })}>
        <defs>
          <filter id={`${id}-pencil`} x="-5%" y="-5%" width="110%" height="110%">
            <feTurbulence type="fractalNoise" baseFrequency="0.035" numOctaves="2" seed="7" result="noise" />
            <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.6" xChannelSelector="R" yChannelSelector="G" />
          </filter>
          {hatch('shade1', 'var(--bs-faint)', 5, 35)}
          {hatch('shade2', 'var(--bs-faint)', 3, 35)}
          {hatch('accent', 'var(--accent)', 2.2, -30, 1.4)}
          {spoolColors.map((c, k) => hatch(`c${k}`, c, 3.2, -30 + k * 25, 1.3))}
        </defs>
        <g filter={`url(#${id}-pencil)`} strokeLinejoin="round" strokeLinecap="round">
          {drawn.map((f, i) => (
            <path key={i} d={f.d} className={f.cls} style={{ fill: fillOf(f.fill), stroke: strokeOf[f.stroke ?? 'ink'], strokeWidth: f.stroke === 'faint' ? 0.8 : 1.15 }} />
          ))}
        </g>
      </svg>
      <button type="button" className="iconbtn bs-replay" onClick={() => setRun((r) => r + 1)} title={t('box.scene.replay')} aria-label={t('box.scene.replay')}>
        <RotateCcw size={14} />
      </button>
    </div>
  )
}

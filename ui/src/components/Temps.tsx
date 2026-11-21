import { useState } from 'react'
import { Dialog, Sparkline } from './ui'
import { attempt, toast } from './toast'
import { runGcode, usePrinter } from '../state/printer'
import { prettyName } from '../lib/format'
import { targetError, targetRange } from './control/heaters'
import { gcode } from './control/gcode'
import { t, type MessageKey } from '../i18n'

// m106 is the fan's M106 P index on QIDI printers, so speed changes go through the printer's own fan macro.
export type Tile = { obj: string; label: string; kind: 'heater' | 'sensor' | 'fan'; m106?: number }

/** Heaters worth a tile: nozzle, bed, and any generic heater that isn't a Box heater (those live on the Box page). */
export function useTiles(): Tile[] {
  return usePrinter((s) => {
    const tiles: Tile[] = []
    if (s.objects.includes('extruder')) tiles.push({ obj: 'extruder', label: t('app.heaters.nozzle'), kind: 'heater' })
    if (s.objects.includes('heater_bed')) tiles.push({ obj: 'heater_bed', label: t('app.heaters.bed'), kind: 'heater' })
    for (const o of s.objects) if (o.startsWith('heater_generic ') && !o.startsWith('heater_generic heater_box')) tiles.push({ obj: o, label: prettyName(o), kind: 'heater' })
    if (!tiles.some((t) => /chamber/i.test(t.obj))) {
      const sensor = s.objects.find((o) => o.startsWith('temperature_sensor ') && /chamber/i.test(o))
      if (sensor) tiles.push({ obj: sensor, label: t('app.heaters.chamber'), kind: 'sensor' })
    }
    if (s.objects.includes('fan')) tiles.push({ obj: 'fan', label: t('app.heaters.part_fan'), kind: 'fan' })
    for (const [obj, label, m106] of QIDI_FANS) if (s.objects.includes(obj)) tiles.push({ obj, label: t(label), kind: 'fan', m106 })
    return tiles
  })
}

// QIDI's fans, their labels' message keys, and their M106 P index; P2 drives both auxiliary fans together.
const QIDI_FANS: [string, MessageKey, number][] = [
  ['fan_generic cooling_fan', 'app.heaters.part_fan', 0],
  ['fan_generic auxiliary_cooling_fan', 'app.heaters.aux_fan', 2],
  ['fan_generic chamber_circulation_fan', 'app.heaters.chamber_exhaust', 3],
]

export function TempTiles({ compact = false }: { compact?: boolean }) {
  const tiles = useTiles()
  const [open, setOpen] = useState<Tile | null>(null)
  return (
    <>
      <div className={`grid gap-2.5 ${compact ? 'grid-cols-3' : 'grid-cols-2 sm:grid-cols-3 xl:grid-cols-[repeat(auto-fit,minmax(150px,1fr))]'}`}>
        {(compact ? tiles.filter((t) => t.kind !== 'fan').slice(0, 3) : tiles).map((t) => (
          <TileView key={t.obj} tile={t} compact={compact} onOpen={() => setOpen(t)} />
        ))}
      </div>
      {open && <TargetDialog tile={open} onClose={() => setOpen(null)} />}
    </>
  )
}

function TileView({ tile, compact, onOpen }: { tile: Tile; compact: boolean; onOpen: () => void }) {
  const st = usePrinter((s) => s.status[tile.obj] ?? {})
  const series = usePrinter((s) => s.temps[tile.obj])
  if (tile.kind === 'fan') {
    const speed = Math.round(((st.speed as number) ?? 0) * 100)
    return (
      <button className="tile" onClick={onOpen}>
        <span className="lbl">{tile.label}</span>
        <span className="v num">
          {speed}
          <small>%</small>
        </span>
        <span className="lbl">{speed > 0 ? t('app.temps.running') : t('app.temps.off')}</span>
      </button>
    )
  }
  const temp = st.temperature as number | undefined
  const target = st.target as number | undefined
  const heating = !!target && target > 0
  return (
    <button className="tile" onClick={onOpen} disabled={tile.kind === 'sensor'}>
      <span className="lbl">{tile.label}</span>
      <span className="v num">
        {temp != null ? Math.round(temp) : '—'}
        <small>°</small>
      </span>
      {!compact && <span className="lbl num">{tile.kind === 'sensor' ? t('app.temps.sensor') : heating ? `→ ${Math.round(target!)} °C` : t('app.temps.off')}</span>}
      {!compact && series && (
        <span className="absolute right-2.5 bottom-2.5">
          <Sparkline values={series.temperatures} target={heating ? target : undefined} width={70} height={24} color={heating ? 'var(--orange)' : 'var(--ink-3)'} />
        </span>
      )}
    </button>
  )
}

const PRESETS: Record<string, number[]> = { extruder: [200, 220, 240, 260], heater_bed: [55, 60, 80, 100] }

export function TargetDialog({ tile, onClose }: { tile: Tile; onClose: () => void }) {
  const st = usePrinter((s) => s.status[tile.obj] ?? {})
  const cfg = usePrinter((s) => s.heaterSettings[tile.obj])
  const [value, setValue] = useState('')
  if (tile.kind === 'fan') {
    const generic = tile.obj.startsWith('fan_generic ')
    const name = tile.obj.replace(/^fan_generic /, '')
    const command = (pct: number) => {
      const s255 = Math.round((pct / 100) * 255)
      if (tile.m106 != null) return gcode`M106 P${tile.m106} S${s255}`
      if (generic) return `SET_FAN_SPEED FAN=${name} SPEED=${(pct / 100).toFixed(2)}`
      return pct === 0 ? 'M107' : gcode`M106 S${s255}`
    }
    const set = (pct: number) => attempt(() => runGcode(command(pct))).then(onClose)
    return (
      <Dialog open onClose={onClose} title={tile.label}>
        <div className="mb-3 text-ink-2">{t('app.temps.fan_now', { pct: Math.round(((st.speed as number) ?? 0) * 100) })}</div>
        <div className="flex flex-wrap gap-2">
          {[0, 25, 50, 75, 100].map((p) => (
            <button key={p} className="btn" onClick={() => set(p)}>
              {p === 0 ? t('common.off') : `${p}%`}
            </button>
          ))}
        </div>
      </Dialog>
    )
  }
  // Within max_temp and QIDI's target_max_temp, whichever is lower: QIDI reports 999 for the nozzle and bed.
  const range = targetRange(cfg)
  const max = range.max
  const heater = tile.obj.replace(/^heater_generic /, '')
  const presets = (PRESETS[tile.obj] ?? [45, 55, 65]).filter((t) => t <= max && t >= range.min)
  const set = (t: number) => attempt(() => runGcode(gcode`SET_HEATER_TEMPERATURE HEATER=${heater} TARGET=${t}`)).then(onClose)
  return (
    <Dialog open onClose={onClose} title={t('app.temps.target_title', { name: tile.label })}>
      <div className="mb-3 text-ink-2">
        {st.target
          ? t('app.temps.now_heating', { temp: Math.round((st.temperature as number) ?? 0), target: Math.round(st.target), max })
          : t('app.temps.now', { temp: Math.round((st.temperature as number) ?? 0), max })}
      </div>
      <div className="flex flex-wrap gap-2">
        <button className="btn" onClick={() => set(0)}>
          {t('common.off')}
        </button>
        {presets.map((p) => (
          <button key={p} className="btn" onClick={() => set(p)}>
            {p} °C
          </button>
        ))}
      </div>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          const temp = Math.round(Number(value))
          const err = targetError(temp, range)
          if (err) return toast(err, 'error')
          set(temp)
        }}
      >
        <input className="field w-28" inputMode="numeric" placeholder="°C" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
        <button className="btn btn-primary" disabled={!value}>
          {t('app.temps.set')}
        </button>
      </form>
    </Dialog>
  )
}

import { Fan, Lightbulb, ToggleRight } from 'lucide-react'
import { Card, Empty, Toggle } from '../ui'
import { ColorInput, Columns, Note, Slider, objectLabel, send } from './common'
import { encodeGcodeParamValue, gcode } from './gcode'
import { usePrinter } from '../../state/printer'
import { t, type MessageKey } from '../../i18n'

// Fans, output pins, and LEDs, as Fluidd's Outputs card lists them (getOutputs): names starting with `_` are hidden.

export const FAN_TYPES = ['fan', 'fan_generic', 'heater_fan', 'controller_fan', 'temperature_fan']
export const PIN_TYPES = ['output_pin', 'pwm_tool', 'pwm_cycle_time']
export const LED_TYPES = ['led', 'neopixel', 'dotstar', 'pca9533', 'pca9632']

export const typeOf = (obj: string) => obj.split(' ')[0]
export const nameOf = (obj: string) => (obj.includes(' ') ? obj.slice(obj.indexOf(' ') + 1) : obj)

/** A fan's name as the Fluidd fork shows it: the part fan, and QIDI's chamber_circulation_fan as the exhaust fan. */
export function fanLabel(obj: string) {
  if (obj === 'fan') return t('control.outputs.fan_names.part_fan')
  const key = QIDI_FAN_NAMES[nameOf(obj)]
  return key ? t(key) : objectLabel(obj)
}

// QIDI's fan names, as Valve Oil's touchscreen labels them.
const QIDI_FAN_NAMES: Record<string, MessageKey> = {
  cooling_fan: 'control.outputs.fan_names.part_fan',
  auxiliary_cooling_fan: 'control.outputs.fan_names.auxiliary_cooling_fan',
  auxiliary_cooling_fan2: 'control.outputs.fan_names.auxiliary_cooling_fan2',
  chamber_circulation_fan: 'control.outputs.fan_names.chamber_circulation_fan',
}
export const QIDI_FAN_ORDER = Object.keys(QIDI_FAN_NAMES)

const percentOrOff = (v: number) => (v > 0 ? `${v}%` : t('common.off'))

export function useOutputs(types: string[]) {
  return usePrinter((s) =>
    s.objects
      .filter((o) => types.includes(typeOf(o)) && (o === 'fan' || !nameOf(o).startsWith('_')))
      .sort((a, b) => types.indexOf(typeOf(a)) - types.indexOf(typeOf(b)) || a.localeCompare(b)),
  )
}

/** The Outputs tab: fans, then pins, then lights. */
export function OutputsTab() {
  return (
    <Columns left={<FansCard />}>
      <PinsCard />
      <LedsCard />
    </Columns>
  )
}

function FansCard() {
  const fans = useOutputs(FAN_TYPES)
  const rank = (o: string) => (QIDI_FAN_ORDER.includes(nameOf(o)) ? QIDI_FAN_ORDER.indexOf(nameOf(o)) : 99)
  const settable = fans.filter((o) => ['fan', 'fan_generic'].includes(typeOf(o))).sort((a, b) => rank(a) - rank(b))
  const auto = fans.filter((o) => !settable.includes(o))
  return (
    <Card title={t('control.outputs.fans')} icon={<Fan size={14} />}>
      {fans.length ? (
        <div className="grid gap-2.5">
          {settable.map((o) => (
            <FanRow key={o} obj={o} />
          ))}
          {auto.length > 0 && (
            <>
              <div className="section-label mt-2">{t('control.outputs.run_by_klipper')}</div>
              <div className="-mt-1 grid">
                {auto.map((o) => (
                  <FanRow key={o} obj={o} />
                ))}
              </div>
            </>
          )}
        </div>
      ) : (
        <Empty>{t('control.outputs.no_fans')}</Empty>
      )}
      <div className="mt-3">
        <Note>{t('control.outputs.fans_note')}</Note>
      </div>
    </Card>
  )
}

export function FanRow({ obj, inline }: { obj: string; inline?: boolean }) {
  const st = usePrinter((s) => s.status[obj] ?? {})
  const cfg = usePrinter((s) => s.settings[obj.toLowerCase()] ?? {})
  const hasM106Macro = usePrinter((s) => s.objects.includes('gcode_macro M106'))
  const type = typeOf(obj)
  const name = nameOf(obj)
  const controllable = type === 'fan' || type === 'fan_generic'
  const value = Math.round((((st.speed as number | undefined) ?? 0) / ((cfg.max_power as number | undefined) || 1)) * 100)
  const rpm = typeof st.rpm === 'number' && st.rpm > 0 ? t('control.outputs.rpm', { rpm: st.rpm.toFixed(0) }) : null
  const offBelow = ((cfg.off_below as number | undefined) ?? 0) * 100
  const set = (pct: number) => {
    if (offBelow && pct > 0 && pct < offBelow) pct = offBelow
    if (type === 'fan') return send(gcode`M106 S${Math.ceil(pct * 2.55)}`)
    // QIDI's chamber exhaust goes through the printer's M106 macro, which tells the chamber guard and warns when it
    // fights a chamber target.
    if (name === 'chamber_circulation_fan' && hasM106Macro) return send(gcode`M106 P3 S${Math.ceil(pct * 2.55)}`)
    send(gcode`SET_FAN_SPEED FAN=${encodeGcodeParamValue(name)} SPEED=${pct / 100}`)
  }
  if (inline && controllable)
    return (
      <div className="grid grid-cols-[minmax(0,150px)_minmax(0,1fr)] items-center gap-3">
        <span className="min-w-0">
          <span className="block truncate font-semibold">{fanLabel(obj)}</span>
          {rpm && <span className="num block text-[11px] text-ink-3">{rpm}</span>}
        </span>
        <Slider label={fanLabel(obj)} value={value} onCommit={set} format={percentOrOff} />
      </div>
    )
  return (
    <div className={controllable ? 'grid gap-0.5' : 'row-k'}>
      <div className={`flex items-baseline gap-2 ${controllable ? '' : 'col-span-2'}`}>
        <span className={`min-w-0 flex-1 truncate ${controllable ? 'font-semibold' : 'text-ink-2'}`}>{fanLabel(obj)}</span>
        {rpm && <span className="num text-[11.5px] text-ink-3">{rpm}</span>}
        {!controllable && <span className={`num font-semibold ${value > 0 ? 'text-accent-ink' : 'text-ink-3'}`}>{value > 0 ? `${value}%` : t('common.off')}</span>}
      </div>
      {controllable && <Slider label={fanLabel(obj)} value={value} onCommit={set} format={percentOrOff} />}
    </div>
  )
}

function PinsCard() {
  const pins = useOutputs(PIN_TYPES)
  if (!pins.length) return null
  return (
    <Card title={t('control.outputs.output_pins')} icon={<ToggleRight size={14} />}>
      <div className="grid gap-2.5">
        {pins.map((o) => (
          <PinRow key={o} obj={o} />
        ))}
      </div>
    </Card>
  )
}

export function PinRow({ obj }: { obj: string }) {
  const value = usePrinter((s) => (s.status[obj]?.value as number | undefined) ?? 0)
  const cfg = usePrinter((s) => s.settings[obj.toLowerCase()] ?? {})
  const type = typeOf(obj)
  const name = nameOf(obj)
  const scale = (cfg.scale as number | undefined) ?? 1
  const pwm = cfg.pwm === true || type === 'pwm_tool' || type === 'pwm_cycle_time'
  const controllable = !cfg.static_value
  const set = (v: number) => send(gcode`SET_PIN PIN=${encodeGcodeParamValue(name)} VALUE=${v}`)
  const label = objectLabel(obj)
  if (pwm)
    return (
      <div className="grid gap-0.5">
        <span className="font-semibold">{label}</span>
        <Slider label={label} value={Math.round(value * 100)} disabled={!controllable} onCommit={(pct) => set(Math.round(pct * scale) / 100)} format={percentOrOff} />
      </div>
    )
  return (
    <div className="flex items-center gap-3">
      <span className="min-w-0 flex-1 truncate font-semibold">{label}</span>
      <span className={`text-[12.5px] font-semibold ${value > 0 ? 'text-accent-ink' : 'text-ink-3'}`}>{value > 0 ? t('common.on') : t('common.off')}</span>
      <Toggle label={label} checked={value > 0} disabled={!controllable} onChange={(on) => set(on ? scale : 0)} />
    </div>
  )
}

function LedsCard() {
  const leds = useOutputs(LED_TYPES)
  if (!leds.length) return null
  return (
    <Card title={t('control.outputs.lights')} icon={<Lightbulb size={14} />}>
      <div className="grid gap-4">
        {leds.map((o) => (
          <LedRow key={o} obj={o} />
        ))}
      </div>
    </Card>
  )
}

const SWATCHES = ['#ffffff', '#ffd9a0', '#f2b05a', '#ff3b30', '#34c759', '#2a78d6', '#af52de']
const CHANNEL: Record<string, string> = { r: 'RED', g: 'GREEN', b: 'BLUE', w: 'WHITE' }
type Rgbw = { r: number; g: number; b: number; w: number }

/** The channels an LED takes, from its color_order (or pins), as Fluidd works them out. */
export function ledChannels(type: string, config: Record<string, unknown> | undefined): string {
  if (config) {
    if ('color_order' in config) {
      const order = Array.isArray(config.color_order) ? config.color_order[0] : config.color_order
      if (typeof order === 'string') return order
    }
    if (type === 'dotstar') return 'RGB'
    if (type === 'led') return ['red_pin', 'green_pin', 'blue_pin', 'white_pin'].filter((p) => p in config).map((p) => p[0].toUpperCase()).join('')
  }
  return 'RBGW'
}

/** A color's channels scaled so the brightest is 1: its hue at full brightness. */
function hue(h: string) {
  const c = [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
  const max = Math.max(...c) || 1
  return c.map((v) => v / max)
}
const hueHex = (h: string) => '#' + hue(h).map((v) => Math.round(v * 255).toString(16).padStart(2, '0')).join('')

/** SET_LED for a color in 0–1 channels, sending only the channels the LED has. */
export function setLedGcode(name: string, channels: string, c: Rgbw) {
  const parts = channels
    .toLowerCase()
    .split('')
    .filter((ch): ch is keyof Rgbw => ch in CHANNEL)
    .map((ch) => gcode` ${CHANNEL[ch]}=${Math.round(Math.min(1, Math.max(0, c[ch])) * 1000) / 1000}`)
    .join('')
  return `SET_LED LED=${encodeGcodeParamValue(name)}${parts}`
}

export function LedRow({ obj }: { obj: string }) {
  const data = usePrinter((s) => (s.status[obj]?.color_data?.[0] as number[] | undefined) ?? [0, 0, 0, 0])
  const cfg = usePrinter((s) => s.settings[obj.toLowerCase()])
  const channels = ledChannels(typeOf(obj), cfg).toLowerCase()
  const [r, g, b, w] = [data[0] ?? 0, data[1] ?? 0, data[2] ?? 0, data[3] ?? 0]
  const color: Rgbw = { r, g, b, w }
  const hasRgb = /[rgb]/.test(channels)
  const hasW = channels.includes('w')
  const brightness = Math.max(r, g, b)
  const hex = '#' + [r, g, b].map((v) => Math.round((brightness ? v / brightness : 1) * 255).toString(16).padStart(2, '0')).join('')
  const name = nameOf(obj)
  const sendColor = (c: Rgbw) => send(setLedGcode(name, channels, c))
  // A picked color sets the hue; brightness stays as it was (full when the light was off).
  const pick = (h: string) => {
    const [pr, pg, pb] = hue(h)
    const level = brightness || 1
    sendColor({ ...color, r: pr * level, g: pg * level, b: pb * level })
  }
  const setBrightness = (pct: number) => {
    const base = brightness ? { r: r / brightness, g: g / brightness, b: b / brightness } : { r: 1, g: 1, b: 1 }
    sendColor({ ...color, r: base.r * (pct / 100), g: base.g * (pct / 100), b: base.b * (pct / 100) })
  }
  const on = brightness > 0 || w > 0
  return (
    <div className="grid gap-2">
      <div className="flex items-center gap-2">
        <span className="h-4 w-4 flex-none rounded-full shadow-[0_0_0_1px_var(--line-strong)]" style={{ background: on ? `rgb(${r * 255} ${g * 255} ${b * 255})` : 'transparent' }} />
        <span className="min-w-0 flex-1 truncate font-semibold">{objectLabel(obj)}</span>
        <button className="btn btn-sm" disabled={!on} onClick={() => sendColor({ r: 0, g: 0, b: 0, w: 0 })}>
          {t('common.off')}
        </button>
      </div>
      {hasRgb && (
        <>
          <div className="flex flex-wrap items-center gap-1.5">
            {SWATCHES.map((s) => (
              <button key={s} type="button" className={`led-swatch ${on && hex === hueHex(s) ? 'on' : ''}`} style={{ background: s }} onClick={() => pick(s)} aria-label={t('control.outputs.set_color', { name: objectLabel(obj), color: s })} title={s} />
            ))}
            <label className="btn btn-sm relative ml-1 cursor-pointer overflow-hidden">
              {t('control.outputs.custom')}
              <ColorInput value={hex} onPick={pick} label={t('control.outputs.pick_color')} />
            </label>
          </div>
          <div className="grid gap-0.5">
            <span className="section-label">{t('control.outputs.brightness')}</span>
            <Slider label={t('control.outputs.brightness_named', { name: objectLabel(obj) })} value={Math.round(brightness * 100)} onCommit={setBrightness} format={percentOrOff} />
          </div>
        </>
      )}
      {hasW && (
        <div className="grid gap-0.5">
          <span className="section-label">{t('control.outputs.white')}</span>
          <Slider label={t('control.outputs.white_named', { name: objectLabel(obj) })} value={Math.round(w * 100)} onCommit={(pct) => sendColor({ ...color, w: pct / 100 })} format={percentOrOff} />
        </div>
      )}
    </div>
  )
}

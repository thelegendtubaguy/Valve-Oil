/* eslint-disable @typescript-eslint/no-explicit-any */
// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/util/qidi-box.ts at
// github.com/thelegendtubaguy/fluidd qidi@1ad1676. Keep behavior in step with the fork.
// QIDI Box slots: Box 1 holds slots 0-3 (1A-1D), Box 2 slots 4-7 (2A-2D), and so on; slot 16 is the external spool.

export const QIDI_BOX_EXTERNAL_SLOT = 16

export interface QidiBoxSlot {
  index: number;
  label: string;
  present: boolean;
  filamentId: string | null;
  name: string | null;
  type: string | null;
  vendor: string | null;
  color: string | null;
  minTemp: number | null;
  maxTemp: number | null;
  boxMaxTemp: number | null;
  reading: boolean;
}

// QIDI's officiall_filas_list.cfg: [filaN] filament entries, [colordict] and [vendor_list] ID maps.
export type QidiFilamentList = Record<string, Record<string, string>>

// An entry of the firmware filament database (01.01.07.01 filament-0.4.json), keyed by its QF filament ID.
export interface QidiFilament {
  id: string;
  name: string;
  type: string;
  vendor: string;
}

// Loaded Box slots that reload into each other, lettered in slot order.
export interface QidiBoxReloadGroup {
  letter: string;
  color: string | null;
  members: number[];
}

export type QidiBoxReloadGroups = Record<number, QidiBoxReloadGroup>

// A Box's aht20_f humidity and temperature, and its heater_generic target while drying.
export interface QidiBoxClimate {
  humidity: number | null;
  temperature: number | null;
  target: number | null;
}

export interface QidiBoxTool {
  tool: number;
  type: string;
  color: string;
}

export interface QidiBoxFileTools {
  tools: QidiBoxTool[];
  named: boolean;
}

// File metadata naming the tools a print uses, from QIDI's Moonraker (used_extruders, preset_colours) or upstream.
export interface QidiBoxFileMetadata {
  used_extruders?: number[];
  referenced_tools?: number[];
  preset_colours?: string[];
  filament_colors?: string[];
  extruder_colors?: string[];
  filament_type?: string[];
}

// The tools a file prints with. A file whose metadata names none can select any filament of its slicer project,
// so all of them are listed, or T0 when the project lists none.
export const qidiBoxFileTools = (file: QidiBoxFileMetadata): QidiBoxFileTools => {
  const colors = file.preset_colours ?? file.filament_colors ?? file.extruder_colors ?? []
  const types = file.filament_type ?? []
  const listed = [...new Set(file.used_extruders ?? file.referenced_tools ?? [])]
  const named = listed.length > 0
  const tools = named ? listed : [...Array(Math.max(colors.length, types.length, 1)).keys()]

  return {
    tools: tools.map(tool => ({
      tool,
      type: types[tool] ?? '',
      color: colors[tool] ?? ''
    })),
    named
  }
}

export const qidiBoxSlotLabel = (index: number): string => index === QIDI_BOX_EXTERNAL_SLOT
  ? 'Ext'
  : `${Math.floor(index / 4) + 1}${String.fromCharCode(65 + (index % 4))}`

// Saved slot references such as value_tN and slot_sync read 'slotN'.
export const qidiBoxSlotNumber = (value: unknown): number | null => {
  const match = /^slot(\d+)$/.exec(String(value ?? ''))

  return match ? parseInt(match[1], 10) : null
}

export const parseQidiFilamentList = (text: string): QidiFilamentList => {
  const list: QidiFilamentList = {}
  let section: Record<string, string> | undefined

  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim()

    if (!line || line.startsWith('#')) continue

    const header = /^\[(.+)\]$/.exec(line)

    if (header) {
      section = list[header[1].trim()] = {}
    } else if (section) {
      const separator = line.indexOf('=')

      if (separator > 0) {
        section[line.slice(0, separator).trim()] = line.slice(separator + 1).trim().replace(/['"]/g, '')
      }
    }
  }

  return list
}

export const parseQidiFilamentDatabase = (data: unknown): QidiFilament[] => {
  if (data == null || typeof data !== 'object') return []

  return Object.entries(data as Record<string, Record<string, unknown>>)
    .filter(([, entry]) => typeof entry?.filament_id === 'string' && /^[A-Z]+\d+$/.test(entry.filament_id))
    .map(([name, entry]) => ({
      id: String(entry.filament_id),
      name,
      type: String(entry.type ?? ''),
      vendor: String(entry.filament_vendor ?? '')
    }))
}

export const normalizeQidiColor = (value: unknown): string | null => {
  const color = Array.isArray(value) ? value[0] : value
  const match = typeof color === 'string' ? /^#?([0-9a-f]{6})$/i.exec(color.trim()) : null

  return match ? `#${match[1].toUpperCase()}` : null
}

// Black or white, whichever reads better on the color.
export const qidiTextColor = (color: string | null): string => {
  const hex = normalizeQidiColor(color)

  if (hex == null) return 'inherit'

  const [r, g, b] = [1, 3, 5].map(offset => parseInt(hex.substring(offset, offset + 2), 16))

  return (r * 299 + g * 587 + b * 114) / 1000 > 125 ? '#000000' : '#FFFFFF'
}

// A filament close to the card background, black on the dark theme or white on the light one, needs an outline to
// stay visible.
export const qidiColorNearBackground = (color: string | null, dark: boolean): boolean => {
  const hex = normalizeQidiColor(color)

  if (hex == null) return false

  const [r, g, b] = [1, 3, 5].map(offset => parseInt(hex.substring(offset, offset + 2), 16))
  const luma = (r * 299 + g * 587 + b * 114) / 255000

  return dark ? luma < 0.25 : luma > 0.85
}

const toNumber = (value: unknown): number | null => {
  const number = typeof value === 'number' ? value : parseFloat(String(value))

  return Number.isFinite(number) ? number : null
}

const toText = (value: unknown): string | null => typeof value === 'string' && value.trim() !== '' ? value.trim() : null

// Firmware 01.01.07.01 publishes every slot's resolved filament as box_extras.boxes[].filament_info[];
// these arrays arrive whole in status updates.
const resolvedFilament = (printer: Record<string, any>, index: number) => {
  if (index === QIDI_BOX_EXTERNAL_SLOT) {
    return printer.multi_color_controller?.extra?.filament_info
  }

  const infos = printer.box_extras?.boxes?.[Math.floor(index / 4)]?.filament_info

  if (Array.isArray(infos)) {
    return infos.find(info => info?.index === index % 4) ?? infos[index % 4]
  }
}

// Earlier firmware saves numeric IDs that resolve through officiall_filas_list.cfg.
const legacyFilament = (variables: Record<string, any>, list: QidiFilamentList | null, index: number) => {
  const filamentId = variables[`filament_slot${index}`]
  const filament = list?.[`fila${filamentId}`]
  const colorId = variables[`color_slot${index}`]

  return {
    filament_id: filamentId != null ? String(filamentId) : null,
    filament_str: filament?.filament,
    filament_type: filament?.type,
    vendor_str: list?.vendor_list?.[variables[`vendor_slot${index}`]],
    color_rgb_str: typeof colorId === 'number' || /^\d+$/.test(String(colorId))
      ? list?.colordict?.[colorId]
      : colorId,
    min_temp: filament?.min_temp,
    max_temp: filament?.max_temp,
    box_max_temp: filament?.box_max_temp
  }
}

export const qidiBoxSlots = (printer: Record<string, any>, list: QidiFilamentList | null): QidiBoxSlot[] => {
  const variables: Record<string, any> = printer.save_variables?.variables ?? {}
  const boxCount = Math.max(0, Math.min(4, toNumber(variables.box_count) ?? 0))
  const indexes = [...Array.from({ length: boxCount * 4 }, (_, index) => index), QIDI_BOX_EXTERNAL_SLOT]

  return indexes.map(index => {
    const info = resolvedFilament(printer, index) ?? legacyFilament(variables, list, index)
    const stepper = printer[`box_stepper slot${index}`]

    return {
      index,
      label: qidiBoxSlotLabel(index),
      // A Box slot's runout_button reads 0 while filament is at its inlet.
      present: index === QIDI_BOX_EXTERNAL_SLOT || stepper?.runout_button === 0,
      filamentId: info?.filament_id != null ? String(info.filament_id) : null,
      name: toText(info?.filament_str),
      type: toText(info?.filament_type),
      vendor: toText(info?.vendor_str),
      color: normalizeQidiColor(info?.color_rgb_str),
      minTemp: toNumber(info?.min_temp),
      maxTemp: toNumber(info?.max_temp),
      boxMaxTemp: toNumber(info?.box_max_temp),
      reading: stepper?.rfid_state === 1
    }
  })
}

// Loaded Box slots with the same color, filament, and vendor reload into each other when one runs out.
// Groups are lettered in slot order.
export const qidiBoxReloadGroups = (slots: QidiBoxSlot[]): QidiBoxReloadGroups => {
  const byKey = new Map<string, number[]>()

  for (const slot of slots) {
    if (slot.index === QIDI_BOX_EXTERNAL_SLOT || !slot.present) continue

    const key = `${slot.color}-${slot.name}-${slot.vendor}`

    byKey.set(key, [...(byKey.get(key) ?? []), slot.index])
  }

  const groups: QidiBoxReloadGroups = {}

  Array.from(byKey.values())
    .filter(members => members.length > 1)
    .forEach((members, i) => {
      const group = {
        letter: String.fromCharCode(65 + i),
        color: slots.find(slot => slot.index === members[0])?.color ?? null,
        members
      }

      members.forEach(member => { groups[member] = group })
    })

  return groups
}

const sameType = (a: string | null, b: string) => a != null && a.toUpperCase() === b.trim().toUpperCase()

const colorDistance = (a: string | null, b: string | null): number => {
  const ca = normalizeQidiColor(a)
  const cb = normalizeQidiColor(b)

  if (ca == null || cb == null) return Number.MAX_SAFE_INTEGER

  return [1, 3, 5].reduce((sum, offset) => {
    const delta = parseInt(ca.substring(offset, offset + 2), 16) - parseInt(cb.substring(offset, offset + 2), 16)

    return sum + delta * delta
  }, 0)
}

// Loaded Box slots a tool can print from: those holding the same filament type.
export const qidiBoxSlotsForTool = (tool: QidiBoxTool, slots: QidiBoxSlot[]): QidiBoxSlot[] => slots
  .filter(slot => slot.index !== QIDI_BOX_EXTERNAL_SLOT && slot.present && sameType(slot.type, tool.type))

// Matches each tool, in order, to the nearest-colored unused slot of its type, as QIDI's Fluidd did.
// A tool left without an unused slot stays unassigned; it can still share a slot when chosen by hand.
export const matchQidiBoxSlots = (tools: QidiBoxTool[], slots: QidiBoxSlot[]): Record<number, number | null> => {
  const used = new Set<number>()
  const result: Record<number, number | null> = {}

  for (const tool of tools) {
    const slot = qidiBoxSlotsForTool(tool, slots)
      .filter(slot => !used.has(slot.index))
      .sort((a, b) => colorDistance(tool.color, a.color) - colorDistance(tool.color, b.color))[0]

    result[tool.tool] = slot?.index ?? null

    if (slot) used.add(slot.index)
  }

  return result
}

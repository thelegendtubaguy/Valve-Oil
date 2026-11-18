// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/util/qidi-filament-config.ts at
// github.com/thelegendtubaguy/fluidd qidi@6405679. Keep behavior in step with the fork.
// QIDI firmware 01.01.07.01's filament database, filament-0.4.json: a JSON object keyed by display name
// ("<vendor> <filament>"), in list order. Klipper and the touchscreen read it when they start. The installer links
// QIDI's filament_config folder into the config root as qidi_filament_config.

export const QIDI_FILAMENT_CONFIG_PATH = 'qidi_filament_config'
export const QIDI_FILAMENT_CONFIG_FILENAME = 'filament-0.4.json'
export const QIDI_FILAMENT_CONFIG_FILE = `${QIDI_FILAMENT_CONFIG_PATH}/${QIDI_FILAMENT_CONFIG_FILENAME}`

export type QidiFilamentConfigEntry = Record<string, unknown>

export type QidiFilamentConfig = Record<string, QidiFilamentConfigEntry>

// The fields the page edits. Anything else in an entry, such as QIDI's drying presets, is kept as it is.
export interface QidiFilamentDraft {
  vendor: string;
  name: string;
  id: string;
  type: string;
  nozzleMin: number | null;
  nozzleMax: number | null;
  boxMax: number | null;
  chamber: number | null;
  support: boolean;
}

export interface QidiFilamentRow extends QidiFilamentDraft {
  key: string;
}

export type QidiFilamentImportStatus = 'new' | 'changed' | 'conflict'

export interface QidiFilamentImportItem {
  key: string;
  id: string;
  status: QidiFilamentImportStatus;
  // For a conflict, the current filament that already uses the ID.
  conflictKey?: string;
}

// QIDI's IDs are QF, a family letter, and two digits; box2base rejects any other shape.
export const QIDI_FILAMENT_ID_PATTERN = /^QF[A-Z]\d{2}$/

const isEntry = (value: unknown): value is QidiFilamentConfigEntry => (
  value != null && typeof value === 'object' && !Array.isArray(value)
)

// Null unless the text is a filament database: an object of entries that each carry a filament_id.
export const parseQidiFilamentConfig = (text: string): QidiFilamentConfig | null => {
  let data: unknown

  try {
    data = JSON.parse(text)
  } catch {
    return null
  }

  if (!isEntry(data)) return null

  const entries = Object.values(data)

  return entries.length > 0 && entries.every(entry => isEntry(entry) && typeof entry.filament_id === 'string')
    ? data as QidiFilamentConfig
    : null
}

export const serializeQidiFilamentConfig = (config: QidiFilamentConfig): string => `${JSON.stringify(config, null, 2)}\n`

const readNumber = (value: unknown): number | null => {
  const raw = Array.isArray(value) ? value[0] : value
  const number = typeof raw === 'string' && raw.trim() !== '' ? Number(raw) : raw

  return typeof number === 'number' && Number.isFinite(number) ? number : null
}

// Writes a number in the shape QIDI used for the field: a number, a numeric string, or a list holding either.
const writeNumber = (previous: unknown, value: number): unknown => {
  if (Array.isArray(previous)) {
    return previous.length ? [writeNumber(previous[0], value), ...previous.slice(1)] : [String(value)]
  }

  return typeof previous === 'number' ? value : String(value)
}

// The display name without its vendor prefix; QIDI resolves a vendor and filament pair by that name.
const filamentName = (key: string, vendor: string) => vendor && key.startsWith(`${vendor} `)
  ? key.slice(vendor.length + 1)
  : key

export const qidiFilamentKey = (draft: Pick<QidiFilamentDraft, 'vendor' | 'name'>) => `${draft.vendor.trim()} ${draft.name.trim()}`

export const qidiFilamentDraft = (key: string, entry: QidiFilamentConfigEntry): QidiFilamentDraft => {
  const vendor = String(entry.filament_vendor ?? '')
  const nozzle = Array.isArray(entry.nozzle_temperature) ? entry.nozzle_temperature : []

  return {
    vendor,
    name: filamentName(key, vendor),
    id: String(entry.filament_id ?? ''),
    type: String(entry.type ?? ''),
    nozzleMin: readNumber(nozzle[0]),
    nozzleMax: readNumber(nozzle[1]),
    boxMax: readNumber(entry.box_temperature_range_high),
    chamber: readNumber(entry.chamber_temperatures),
    support: readNumber(entry.filament_is_support) === 1
  }
}

export const qidiFilamentRows = (config: QidiFilamentConfig): QidiFilamentRow[] => Object.entries(config)
  .map(([key, entry]) => ({ key, ...qidiFilamentDraft(key, entry) }))

const applyDraft = (entry: QidiFilamentConfigEntry, draft: QidiFilamentDraft): QidiFilamentConfigEntry => {
  const nozzle = Array.isArray(entry.nozzle_temperature) ? entry.nozzle_temperature : [0, 0]
  const result: QidiFilamentConfigEntry = {
    ...entry,
    type: draft.type.trim(),
    filament_id: draft.id,
    nozzle_temperature: [
      writeNumber(nozzle[0], draft.nozzleMin ?? 0),
      writeNumber(nozzle[1], draft.nozzleMax ?? 0),
      ...nozzle.slice(2)
    ],
    box_temperature_range_high: writeNumber(entry.box_temperature_range_high ?? ['0'], draft.boxMax ?? 0),
    filament_vendor: draft.vendor.trim(),
    filament_is_support: writeNumber(entry.filament_is_support ?? '0', draft.support ? 1 : 0),
    chamber_temperatures: writeNumber(entry.chamber_temperatures ?? '0', draft.chamber ?? 0)
  }

  if (!('box_temperature_range_low' in result)) result.box_temperature_range_low = ['0']

  return result
}

// The entry a new filament copies its other settings from: Generic's filament of the same type, else another
// filament of that type, else the first in the list.
export const qidiFilamentBaseKey = (config: QidiFilamentConfig, type: string): string | undefined => {
  const entries = Object.entries(config)
  const sameType = entries.filter(([, entry]) => String(entry.type ?? '').toLowerCase() === type.trim().toLowerCase())

  return (
    sameType.find(([, entry]) => entry.filament_vendor === 'Generic') ??
    sameType[0] ??
    entries[0]
  )?.[0]
}

// Saves a filament, replacing previousKey in place or, for a new filament, adding one based on baseKey.
export const setQidiFilament = (
  config: QidiFilamentConfig,
  draft: QidiFilamentDraft,
  options: { previousKey?: string, baseKey?: string }
): QidiFilamentConfig => {
  const key = qidiFilamentKey(draft)
  const { previousKey, baseKey } = options

  if (key !== previousKey && key in config) throw new Error(`Filament already listed: ${key}`)

  const idOwner = qidiFilamentIdOwner(config, draft.id, previousKey)

  if (idOwner != null) throw new Error(`Filament ID ${draft.id} already used by ${idOwner}`)

  if (previousKey != null) {
    const previous = config[previousKey]

    if (previous == null) throw new Error(`Filament not found: ${previousKey}`)

    return Object.fromEntries(Object.entries(config)
      .map(([entryKey, entry]) => entryKey === previousKey
        ? [key, applyDraft(previous, draft)]
        : [entryKey, entry]
      )
    )
  }

  const base = baseKey != null ? config[baseKey] : undefined

  return { ...config, [key]: applyDraft(structuredClone(base ?? {}), draft) }
}

export const removeQidiFilament = (config: QidiFilamentConfig, key: string): QidiFilamentConfig => {
  const { [key]: removed, ...rest } = config

  if (removed == null) throw new Error(`Filament not found: ${key}`)

  return rest
}

export const qidiFilamentIdOwner = (config: QidiFilamentConfig, id: string, exceptKey?: string): string | undefined => (
  Object.entries(config).find(([key, entry]) => key !== exceptKey && entry.filament_id === id)?.[0]
)

// The first unused ID in the family of fromId, counting up from fromId.
export const nextQidiFilamentId = (config: QidiFilamentConfig, fromId: string): string | undefined => {
  const match = /^QF([A-Z])(\d{2})$/.exec(fromId)

  if (!match) return undefined

  const used = new Set(Object.values(config).map(entry => entry.filament_id))
  const start = parseInt(match[2], 10)

  for (let offset = 1; offset < 100; offset++) {
    const id = `QF${match[1]}${String((start + offset) % 100).padStart(2, '0')}`

    if (!used.has(id)) return id
  }

  return undefined
}

const sameEntry = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

// The imported filaments that differ from the current list. A new filament whose ID another current filament
// uses is a conflict, as is a changed one whose ID moved onto another filament.
export const diffQidiFilamentImport = (current: QidiFilamentConfig, imported: QidiFilamentConfig): QidiFilamentImportItem[] => (
  Object.entries(imported)
    .filter(([key, entry]) => !sameEntry(current[key], entry))
    .map(([key, entry]): QidiFilamentImportItem => {
      const id = String(entry.filament_id)
      const conflictKey = qidiFilamentIdOwner(current, id, key)

      return conflictKey != null
        ? { key, id, status: 'conflict', conflictKey }
        : { key, id, status: key in current ? 'changed' : 'new' }
    })
)

// Adds or replaces the chosen imported filaments; replaced ones keep their place in the list.
export const applyQidiFilamentImport = (current: QidiFilamentConfig, imported: QidiFilamentConfig, keys: string[]): QidiFilamentConfig => {
  const applied = diffQidiFilamentImport(current, imported)
    .filter(item => item.status !== 'conflict' && keys.includes(item.key))
  const result = { ...current }

  for (const { key } of applied) {
    result[key] = imported[key]
  }

  return result
}

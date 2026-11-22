// QIDI Box state for the UI, derived with the logic ported from the Fluidd fork (src/qidi/). The derivation and the
// commands are in box-model.ts, which has no import-time side effects; this file adds the stores and hooks.
import { useEffect } from 'react'
import { createStore, useStore } from '../lib/store'
import { client, onKlippyReady, printer, usePrinter } from './printer'
import { QIDI_BOX_EXTERNAL_SLOT, parseQidiFilamentDatabase, parseQidiFilamentList, qidiBoxSlotLabel, type QidiBoxToolButton, type QidiBoxSlot, type QidiFilament } from '../qidi/qidi-box'
import { qidiBoxTask } from '../qidi/qidi-box-task'
import { deriveBox, type BoxState, type Lists } from './box-model'

export { QIDI_BOX_EXTERNAL_SLOT, qidiBoxSlotLabel }
export type { QidiBoxSlot, QidiFilament, QidiBoxToolButton }
export { boxCommands, boxHeaterMax, boxHeatLimiters, deriveBox, dryBlocker, reloadText, slotActions, slotByIndex, slotOverline, slotStatus } from './box-model'
export type { BoxClimate, BoxSetting, BoxState, SlotActions, SpoolRoll } from './box-model'

// ---- QIDI's filament lists, read through Moonraker's file API ----
export const LEGACY_LIST_PATH = 'officiall_filas_list.cfg'
export const DATABASE_PATH = 'qidi_filament_config/filament-0.4.json'

export const filamentLists = createStore<Lists>({ legacy: null, database: null, databaseError: false })

async function fetchText(path: string): Promise<string | null> {
  try {
    const r = await fetch(client.fileUrl('config', path), { cache: 'no-store' })
    return r.ok ? await r.text() : null
  } catch {
    return null
  }
}

// Reads that came back empty are tried again a few times, as Moonraker can refuse files for a moment after a boot.
const RETRY_MS = 10_000
const RETRIES = 6
let loading: Promise<void> | null = null
let loaded = false
let retryTimer: ReturnType<typeof setTimeout> | null = null

export async function loadFilamentLists(attempt = 0) {
  if (retryTimer) clearTimeout(retryTimer)
  retryTimer = null
  const hasController = printer.get().status.multi_color_controller != null
  const legacyText = await fetchText(LEGACY_LIST_PATH)
  const legacy = legacyText ? parseQidiFilamentList(legacyText) : null
  let database: QidiFilament[] | null = null
  let databaseError = false
  let unread = !legacyText && !hasController
  if (hasController) {
    const text = await fetchText(DATABASE_PATH)
    unread = text == null
    try {
      database = text ? parseQidiFilamentDatabase(JSON.parse(text)) : null
    } catch {
      database = null
    }
    databaseError = !database?.length
  }
  filamentLists.set({ legacy, database, databaseError })
  loaded = !unread
  if (unread && attempt < RETRIES) retryTimer = setTimeout(() => void loadFilamentLists(attempt + 1), RETRY_MS)
}

/** Loads the filament lists once if nothing has yet, as when this page opened after Klipper was ready. */
function ensureFilamentLists() {
  if (loaded || loading || retryTimer) return
  loading = loadFilamentLists().finally(() => (loading = null))
}

// Klipper and the touchscreen read the filament files when they start, so they're read again after every Klipper start.
onKlippyReady(() => {
  const st = printer.get().status
  if (Number(st.save_variables?.variables?.box_count ?? 0) > 0 || st.multi_color_controller != null) {
    loaded = false
    loading = loadFilamentLists().finally(() => (loading = null))
  }
})

export function useBox(): BoxState {
  const s = usePrinter((x) => x)
  const lists = useStore(filamentLists, (l) => l)
  const wanted = s.klippy === 'ready' && (Number(s.status.save_variables?.variables?.box_count ?? 0) > 0 || s.status.multi_color_controller != null)
  useEffect(() => {
    if (wanted) ensureFilamentLists()
  }, [wanted])
  return deriveBox(s, lists)
}

export function useBoxPresent() {
  return usePrinter((s) => Number(s.status.save_variables?.variables?.box_count ?? 0) > 0)
}

export function useBoxBusy() {
  return usePrinter((s) => {
    if (s.klippy !== 'ready') return false
    const st = s.status
    return !!st.box_extras?.box_button_state || qidiBoxTask(st) != null
  })
}

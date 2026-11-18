// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/util/qidi-lan-mode.ts at
// github.com/thelegendtubaguy/fluidd qidi@6405679. Unchanged from the fork; keep it in step.
//
// QIDI's LAN-only mode is `general.lan_mode` in QIDI Moonraker's settings table, "1" when on. Turning it on through
// that table disconnects QIDI's cloud connection and stops its remote-access tunnel; turning it off starts them again.
// QIDI's touchscreen app reads the same value and treats it as on only when it is "1".
export const QIDI_LAN_MODE_SECTION = 'general'
export const QIDI_LAN_MODE_KEY = 'lan_mode'

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** Whether LAN-only mode is on, from QIDI Moonraker's answer, or null when the answer isn't QIDI's. */
export const qidiLanModeFromConfig = (response: unknown): boolean | null => {
  if (!isRecord(response) || response.status !== 'success' || !isRecord(response.result)) {
    return null
  }

  const value = response.result[`${QIDI_LAN_MODE_SECTION}.${QIDI_LAN_MODE_KEY}`]

  if (typeof value !== 'string' && typeof value !== 'number') {
    return null
  }

  return String(value) === '1'
}

/** The value QIDI's touchscreen app writes for LAN-only mode. */
export const qidiLanModeValue = (on: boolean): string => on ? '1' : '0'

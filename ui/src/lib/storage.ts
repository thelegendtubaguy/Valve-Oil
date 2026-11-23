// Browser storage under keys Valve Oil UI has renamed. A value saved under the old key moves to the new one the first
// time it's read, so operators keep what they saved (closed update results, an update in progress) across the rename.

/**
 * The value under `key`. When there is none, the value under `oldKey` is moved to `key` and returned. Throws only when
 * the browser refuses to read storage; if it refuses the move, the old value is still returned.
 */
export function getRenamedItem(storage: Storage, key: string, oldKey: string): string | null {
  const value = storage.getItem(key)
  if (value !== null) return value
  const old = storage.getItem(oldKey)
  if (old === null) return null
  try {
    storage.setItem(key, old)
    storage.removeItem(oldKey)
  } catch {
    // Private browsing can refuse storage; the old value still counts for this page.
  }
  return old
}

import { useCallback, useEffect, useRef, useState } from 'react'
import { LoaderCircle } from 'lucide-react'
import { Toggle } from '../ui'
import { client, usePrinter } from '../../state/printer'
import { QIDI_LAN_MODE_KEY, QIDI_LAN_MODE_SECTION, qidiLanModeFromConfig, qidiLanModeValue } from '../../qidi/qidi-lan-mode'
import { Notice } from './common'

/**
 * QIDI's LAN only mode, the same setting as the touchscreen's LAN Only switch, as the Fluidd fork's TltgQidiLanOnly.vue
 * has it. Shown only when the printer's Moonraker has QIDI's settings table.
 */
export function LanOnly({ className = '' }: { className?: string }) {
  const open = usePrinter((s) => s.conn === 'open')
  const [lanOnly, setLanOnly] = useState<boolean | null>(null)
  const [request, setRequest] = useState<boolean | null>(null)
  const [error, setError] = useState('')
  const requesting = useRef(false)

  const load = useCallback(async () => {
    if (client.state !== 'open' || requesting.current) return
    try {
      const response = await client.call('server.database.config.select', { section: QIDI_LAN_MODE_SECTION, key: QIDI_LAN_MODE_KEY, default: qidiLanModeValue(false) })
      if (!requesting.current) setLanOnly(qidiLanModeFromConfig(response))
    } catch {
      // Upstream Moonraker has no settings table, so there's no switch to show.
      setLanOnly(null)
    }
  }, [])

  useEffect(() => {
    if (open) load()
  }, [open, load])

  // The touchscreen can change it too.
  useEffect(() => {
    const onVisibility = () => !document.hidden && load()
    document.addEventListener('visibilitychange', onVisibility)
    return () => document.removeEventListener('visibilitychange', onVisibility)
  }, [load])

  const set = async (on: boolean) => {
    if (requesting.current || on === lanOnly) return
    requesting.current = true
    setRequest(on)
    setError('')
    try {
      const response = await client.call<{ message?: string; status?: string }>('server.database.config.update', { section: QIDI_LAN_MODE_SECTION, key: QIDI_LAN_MODE_KEY, value: qidiLanModeValue(on) })
      if (qidiLanModeFromConfig(response) == null) throw new Error(response?.message ?? response?.status ?? 'Unknown answer')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      requesting.current = false
      setRequest(null)
    }
    load()
  }

  if (lanOnly == null || !open) return null
  return (
    <div className={className}>
      <label className="flex items-center gap-3 py-1">
        <div className="min-w-0 flex-1">
          <div className="font-medium">LAN only mode</div>
          <div className="text-[12.5px] text-ink-3">
            Turn off QIDI's cloud connection and remote access, as the touchscreen's LAN Only switch does. The Qidi Joy app and QIDI account sign-in don't work while it's on. Fluidd and the
            slicers keep working on your network.
          </div>
        </div>
        {request != null && <LoaderCircle size={15} className="spin flex-none text-ink-3" />}
        <Toggle checked={request ?? lanOnly === true} disabled={request != null} label="LAN only mode" onChange={set} />
      </label>
      {error && (
        <Notice tone="bad" className="mt-1" onClose={() => setError('')}>
          LAN only mode couldn't be changed: {error}
        </Notice>
      )}
    </div>
  )
}

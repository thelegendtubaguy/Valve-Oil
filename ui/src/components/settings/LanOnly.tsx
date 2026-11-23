import { useCallback, useEffect, useRef, useState } from 'react'
import { LoaderCircle } from 'lucide-react'
import { Toggle, Notice } from '../ui'
import { client, usePrinter } from '../../state/printer'
import { QIDI_LAN_MODE_KEY, QIDI_LAN_MODE_SECTION, qidiLanModeFromConfig, qidiLanModeValue } from '../../qidi/qidi-lan-mode'
import { t } from '../../i18n'

/**
 * QIDI's LAN only mode, the same setting as the touchscreen's LAN Only switch, as the Fluidd fork's VoQidiLanOnly.vue
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
      if (qidiLanModeFromConfig(response) == null) throw new Error(response?.message ?? response?.status ?? t('settings.lan_only.unknown_answer'))
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
          <div className="font-medium">{t('settings.lan_only.title')}</div>
          <div className="text-[12.5px] text-ink-3">{t('settings.lan_only.sub')}</div>
        </div>
        {request != null && <LoaderCircle size={15} className="spin flex-none text-ink-3" />}
        <Toggle checked={request ?? lanOnly === true} disabled={request != null} label={t('settings.lan_only.title')} onChange={set} />
      </label>
      {error && (
        <Notice tone="bad" className="mt-1" onClose={() => setError('')}>
          {t('settings.lan_only.change_failed', { error })}
        </Notice>
      )}
    </div>
  )
}

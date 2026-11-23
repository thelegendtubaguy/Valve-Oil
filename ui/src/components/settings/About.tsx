import { useEffect, useState } from 'react'
import { ExternalLink, Info } from 'lucide-react'
import { Card, KV } from '../ui'
import { fluiddUrl } from '../../lib/links'
import { client, useConnected } from '../../state/printer'
import { useValveOil, useValveOilView, watchValveOil } from '../../state/valve-oil'
import { href } from '../../lib/router'
import { t } from '../../i18n'

type Versions = { klipper?: string; moonraker?: string; hostname?: string; fluidd?: string }

function useVersions(): Versions {
  const open = useConnected()
  const [v, setV] = useState<Versions>({})
  useEffect(() => {
    if (!open) return
    client
      .call<{ software_version?: string; hostname?: string }>('printer.info')
      .then((r) => setV((x) => ({ ...x, klipper: r.software_version, hostname: r.hostname })))
      .catch(() => {})
    client
      .call<{ moonraker_version?: string }>('server.info')
      .then((r) => setV((x) => ({ ...x, moonraker: r.moonraker_version })))
      .catch(() => {})
  }, [open])
  // Fluidd's build writes release_info.json beside its index; Valve Oil installs its fork's build at the web root.
  useEffect(() => {
    fetch(fluiddUrl('release_info.json'), { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((r: { version?: unknown } | null) => typeof r?.version === 'string' && setV((x) => ({ ...x, fluidd: r.version as string })))
      .catch(() => {})
  }, [])
  return v
}

export function AboutSettings() {
  useEffect(watchValveOil, [])
  const s = useValveOil()
  const view = useValveOilView()
  const v = useVersions()
  // [id, label, value, note]
  const rows: [string, string, string | undefined, string?][] = [
    ['valve_oil_ui', 'Valve Oil UI', __APP_VERSION__],
    ['valve_oil', 'Valve Oil', view?.release ?? undefined, view && view.devBuild != null ? (view.devBuild ? t('settings.about.dev_build_n', { n: view.devBuild }) : t('settings.about.dev_build')) : undefined],
    ['firmware', t('settings.about.qidi_firmware'), view?.firmware ?? undefined],
    ['fluidd', 'Fluidd', v.fluidd],
    ['klipper', 'Klipper', v.klipper],
    ['moonraker', 'Moonraker', v.moonraker],
    ['hostname', t('settings.about.host_name'), v.hostname],
  ]
  return (
    <Card title={t('settings.about.title')} icon={<Info size={14} />}>
      <div className="grid">
        {rows.filter(([id, , value]) => id !== 'fluidd' || value).map(([id, k, value, note]) => (
          <KV key={id} k={k} vClassName="[overflow-wrap:anywhere]">
            {value || <span className="font-normal text-ink-3">{id === 'valve_oil' && !s.unavailable && !s.status ? '…' : t('settings.about.unknown')}</span>}
            {note && <span className="block text-[12px] font-normal text-ink-3">{note}</span>}
          </KV>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <a className="btn btn-sm" href={href('updates')}>
          {t('settings.about.updates')}
        </a>
        <a className="btn btn-sm" href="https://github.com/thelegendtubaguy/Valve-Oil" target="_blank" rel="noopener noreferrer">
          {t('settings.about.github')} <ExternalLink size={13} />
        </a>
        <a className="btn btn-sm" href={fluiddUrl()} target="_blank" rel="noopener noreferrer">
          {t('settings.about.open_fluidd')} <ExternalLink size={13} />
        </a>
      </div>
    </Card>
  )
}

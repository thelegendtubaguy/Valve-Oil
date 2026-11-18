import { useEffect, useState } from 'react'
import { ExternalLink, Info } from 'lucide-react'
import { Card } from '../ui'
import { fluiddUrl } from '../../lib/links'
import { client, usePrinter } from '../../state/printer'
import { useValveOil, useValveOilView, watchValveOil } from '../../state/valve-oil'
import { href } from '../../lib/router'

type Versions = { klipper?: string; moonraker?: string; hostname?: string; fluidd?: string }

function useVersions(): Versions {
  const open = usePrinter((s) => s.conn === 'open')
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
  const rows: [string, string | undefined, string?][] = [
    ['Valve Oil UI', __APP_VERSION__],
    ['Valve Oil', view?.release ?? undefined, view && view.devBuild != null ? (view.devBuild ? `Dev build ${view.devBuild}` : 'Dev build') : undefined],
    ['QIDI firmware', view?.firmware ?? undefined],
    ['Fluidd', v.fluidd],
    ['Klipper', v.klipper],
    ['Moonraker', v.moonraker],
    ['Host name', v.hostname],
  ]
  return (
    <Card title="Version information" icon={<Info size={14} />}>
      <div className="grid">
        {rows.filter(([k, value]) => k !== 'Fluidd' || value).map(([k, value, note]) => (
          <div key={k} className="row-k">
            <span className="k">{k}</span>
            <span className="v [overflow-wrap:anywhere]">
              {value || <span className="font-normal text-ink-3">{k === 'Valve Oil' && !s.unavailable && !s.status ? '…' : 'Unknown'}</span>}
              {note && <span className="block text-[12px] font-normal text-ink-3">{note}</span>}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <a className="btn btn-sm" href={href('updates')}>
          Valve Oil updates
        </a>
        <a className="btn btn-sm" href="https://github.com/thelegendtubaguy/Valve-Oil" target="_blank" rel="noopener noreferrer">
          Valve Oil on GitHub <ExternalLink size={13} />
        </a>
        <a className="btn btn-sm" href={fluiddUrl()} target="_blank" rel="noopener noreferrer">
          Open Fluidd <ExternalLink size={13} />
        </a>
      </div>
    </Card>
  )
}

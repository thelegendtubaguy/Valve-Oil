import { useCallback, useEffect, useState } from 'react'
import { Cable, Cpu, Database, HardDrive, MemoryStick, RefreshCw, RotateCcw, Server, Trash2, TriangleAlert, Usb } from 'lucide-react'
import { Card, Dialog, Empty, Segmented, Spinner, confirm } from '../ui'
import { attempt, toast } from '../toast'
import { client, usePrinter } from '../../state/printer'
import { Row, fmtFreq, fmtSize, mcuLabel, timestampName } from './bits'
import { useSystemInfo, type SystemInfo } from './system-info'
import { UsageCard } from './Usage'
import { t } from '../../i18n'
import { tr } from '../../i18n/rich'

export function Overview() {
  const { system } = useSystemInfo()
  return (
    <div className="grid grid-cols-1 items-start gap-3.5 lg:grid-cols-2">
      <div className="grid min-w-0 grid-cols-1 gap-3.5">
        <WarningsCard />
        <HostCard />
        <SdCard info={system} />
        <DiskCard />
        <DatabaseCard />
      </div>
      <div className="grid min-w-0 grid-cols-1 gap-3.5">
        <UsageCard cores={system?.cpu_info?.cpu_count || 1} />
        <McuCards />
      </div>
    </div>
  )
}

/** Moonraker's failed components and warnings, and Klipper's config warnings, as Fluidd lists them. */
function WarningsCard() {
  const { server } = useSystemInfo()
  const klipper = usePrinter((s) => (s.status.configfile?.warnings ?? []) as { message?: string }[])
  const failed = server?.failed_components ?? []
  const warnings = server?.warnings ?? []
  const missing = server?.missing_klippy_requirements ?? []
  if (!failed.length && !warnings.length && !missing.length && !klipper.length) return null
  return (
    <Card title={t('system.overview.warnings.title')} icon={<TriangleAlert size={15} />}>
      <div className="grid gap-2">
        {failed.length > 0 && (
          <div className="tone-bad rounded-lg px-3 py-2">
            {tr('system.overview.warnings.failed_plugins', { list: failed.join(', ') }, { b: (c) => <b>{c}</b> })}
          </div>
        )}
        {missing.length > 0 && <div className="tone-warn rounded-lg px-3 py-2">{t('system.overview.warnings.missing', { list: missing.join(', ') })}</div>}
        {warnings.map((w, i) => (
          <div key={`m${i}`} className="tone-warn rounded-lg px-3 py-2 break-words">
            <span className="font-semibold">Moonraker:</span> {w}
          </div>
        ))}
        {klipper.map((w, i) => (
          <div key={`k${i}`} className="tone-warn rounded-lg px-3 py-2 break-words">
            <span className="font-semibold">Klipper:</span> {w.message}
          </div>
        ))}
      </div>
    </Card>
  )
}

function distributionName(d: SystemInfo['distribution']) {
  if (!d?.name || d.name.startsWith('0.')) return undefined
  return `${d.name.startsWith('#') ? d.id : d.name} ${d.release_info?.version_id ?? ''}`.trim()
}

function HostCard() {
  const { system, server, printer, loading } = useSystemInfo()
  const [devices, setDevices] = useState(false)
  const cpu = system?.cpu_info
  const dist = system?.distribution
  const network = Object.entries(system?.network ?? {})
    .map(([name, n]) => {
      const v4 = n.ip_addresses?.filter((a) => a.family === 'ipv4')
      const ips = v4?.length ? v4 : n.ip_addresses?.filter((a) => !a.is_link_local)
      return ips?.length ? `${name} (${ips.map((a) => a.address).join(', ')})` : name
    })
    .join(', ')
  const virt = system?.virtualization
  const distName = distributionName(dist)
  return (
    <Card
      title={t('system.overview.host.title')}
      icon={<Server size={15} />}
      aside={
        <span className="flex items-center gap-1">
          {loading && <Spinner size={14} />}
          <button className="btn btn-ghost btn-sm" onClick={() => setDevices(true)}>
            <Usb size={14} /> {t('system.overview.devices.title')}
          </button>
        </span>
      }
    >
      {!system && !loading ? (
        <Empty>{t('system.overview.host.no_details')}</Empty>
      ) : (
        <div>
          <Row k={t('system.overview.host.hostname')}>{printer?.hostname}</Row>
          <Row k="Klipper">{printer?.software_version}</Row>
          <Row k="Moonraker">{server?.moonraker_version && server.moonraker_version !== '?' ? server.moonraker_version : server?.api_version_string ? `API ${server.api_version_string}` : undefined}</Row>
          <Row k={t('system.overview.host.cpu_model')}>{cpu?.model}</Row>
          <Row k={t('system.overview.host.cpu_desc')}>{cpu?.cpu_desc}</Row>
          <Row k={t('system.overview.host.hardware')}>{cpu?.hardware_desc}</Row>
          <Row k={t('system.overview.host.processor')}>{cpu?.bits && cpu.processor && cpu.cpu_count ? t('system.overview.host.processor_value', { bits: cpu.bits, processor: cpu.processor, count: cpu.cpu_count }) : undefined}</Row>
          <Row k={t('system.overview.host.total_memory')}>{cpu?.total_memory ? fmtSize(cpu.total_memory * 1024) : undefined}</Row>
          <Row k={t('system.overview.host.os')}>{dist?.name}</Row>
          {distName !== dist?.name && <Row k={t('system.overview.host.distribution')}>{distName}</Row>}
          <Row k={t('system.overview.host.distribution_like')}>{dist?.like}</Row>
          <Row k={t('system.overview.host.codename')}>{dist?.codename}</Row>
          <Row k={t('system.overview.host.kernel')}>{dist?.kernel_version}</Row>
          <Row k="Python">{system?.python?.version_string?.split(' ')[0]}</Row>
          <Row k={t('system.overview.host.network')}>{network}</Row>
          <Row k={t('system.overview.host.virtualization')}>{virt?.virt_type && virt.virt_type !== 'none' ? `${virt.virt_type} (${virt.virt_identifier})` : undefined}</Row>
        </div>
      )}
      <DevicesDialog open={devices} onClose={() => setDevices(false)} />
    </Card>
  )
}

function SdCard({ info }: { info: SystemInfo | null }) {
  const sd = info?.sd_info
  if (!sd || !Object.keys(sd).length) return null
  return (
    <Card title={t('system.overview.sd.title')} icon={<MemoryStick size={15} />}>
      <Row k={t('system.overview.sd.manufacturer')}>{sd.manufacturer}</Row>
      <Row k={t('system.overview.sd.manufactured')}>{sd.manufacturer_date}</Row>
      <Row k={t('system.overview.sd.product_name')}>{sd.product_name ? `${sd.product_name} ${sd.product_revision ?? ''}`.trim() : undefined}</Row>
      <Row k={t('system.overview.sd.capacity')}>{sd.capacity}</Row>
      <Row k={t('system.overview.sd.serial')}>{sd.serial_number}</Row>
    </Card>
  )
}

type Disk = { total: number; used: number; free: number }
function DiskCard() {
  const roots = usePrinter((s) => (s.server?.registered_directories ?? []).filter((r) => !r.startsWith('.')))
  const [root, setRoot] = useState('gcodes')
  const [disk, setDisk] = useState<Record<string, Disk | null>>({})
  const [loading, setLoading] = useState(false)
  const current = roots.includes(root) ? root : roots[0]
  const load = useCallback(async (r: string) => {
    setLoading(true)
    try {
      const res = await client.call<{ disk_usage?: Disk }>('server.files.get_directory', { path: r, extended: false })
      setDisk((d) => ({ ...d, [r]: res.disk_usage ?? null }))
    } catch {
      setDisk((d) => ({ ...d, [r]: null }))
    }
    setLoading(false)
  }, [])
  useEffect(() => {
    if (current && !(current in disk)) load(current)
  }, [current, disk, load])
  if (!roots.length) return null
  const d = current ? disk[current] : null
  const pct = d && d.total ? (d.used / d.total) * 100 : 0
  const low = d ? d.free / d.total < 0.1 : false
  return (
    <Card
      title={t('system.overview.disk.title')}
      icon={<HardDrive size={15} />}
      aside={
        <span className="flex items-center gap-1">
          {roots.length > 1 && (
            <select className="field h-7 w-auto py-0 pl-2.5 text-[12.5px]" value={current} onChange={(e) => setRoot(e.target.value)} aria-label={t('system.overview.disk.folder')}>
              {roots.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          )}
          <button className="iconbtn h-7 w-7" onClick={() => current && load(current)} disabled={loading} aria-label={t('system.refresh')} title={t('system.refresh')}>
            <RefreshCw size={14} className={loading ? 'spin' : ''} />
          </button>
        </span>
      }
    >
      <div className="bar" style={{ height: 10 }}>
        <i style={{ width: `${pct}%`, ...(low ? { background: 'var(--red)' } : {}) }} />
      </div>
      <div className="mt-2 flex justify-between gap-3 text-[13px]">
        <span>{tr('system.overview.disk.used', { size: <b className="num">{d ? fmtSize(d.used) : '?'}</b> }, { m: (c) => <span className="text-ink-3">{c}</span> })}</span>
        <span className="text-ink-3">{d ? t('system.overview.disk.of_total', { pct: Math.round(pct), total: fmtSize(d.total) }) : ''}</span>
        <span>{tr('system.overview.disk.free', { size: <b className={`num ${low ? 'text-bad' : ''}`}>{d ? fmtSize(d.free) : '?'}</b> }, { m: (c) => <span className="text-ink-3">{c}</span> })}</span>
      </div>
    </Card>
  )
}

function DatabaseCard() {
  const printing = usePrinter((s) => s.status.print_stats?.state === 'printing' || s.status.print_stats?.state === 'paused')
  const [list, setList] = useState<{ namespaces: string[]; backups: string[] } | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const load = useCallback(() => {
    client
      .call<{ namespaces: string[]; backups?: string[] }>('server.database.list')
      .then((r) => setList({ namespaces: r.namespaces ?? [], backups: (r.backups ?? []).slice().sort().reverse() }))
      .catch(() => setList(null))
  }, [])
  useEffect(load, [load])
  const run = async (key: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(key)
    const ok = await attempt(async () => (await fn(), true))
    setBusy(null)
    if (ok && done) toast(done)
    load()
  }
  const restore = async (name: string) => {
    if (!(await confirm({ title: t('system.overview.database.restore_title'), body: <>{tr('system.overview.database.restore_body', { name }, { b: (c) => <b>{c}</b> })}</>, confirm: t('system.overview.database.restore'), danger: true }))) return
    run(`r:${name}`, () => client.call('server.database.restore', { filename: name }), t('system.overview.database.restored'))
  }
  const remove = async (name: string) => {
    if (!(await confirm({ title: t('system.overview.database.delete_title'), body: name, confirm: t('common.delete'), danger: true }))) return
    run(`d:${name}`, () => client.call('server.database.delete_backup', { filename: name }), t('system.overview.database.deleted'))
  }
  return (
    <Card title={t('system.overview.database.title')} icon={<Database size={15} />}>
      {list && list.namespaces.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1.5" title={t('system.overview.database.namespaces')}>
          {list.namespaces
            .slice()
            .sort()
            .map((n) => (
              <span key={n} className="chip code">
                {n}
              </span>
            ))}
        </div>
      )}
      {list && list.backups.length > 0 ? (
        <div className="-mx-1 overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>{t('system.overview.database.backup')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.backups.map((b) => (
                <tr key={b}>
                  <td className="code break-all">{b}</td>
                  <td className="text-right whitespace-nowrap">
                    <button className="btn btn-ghost btn-sm" disabled={printing || busy != null} onClick={() => restore(b)} title={printing ? t('system.not_while_printing') : t('system.overview.database.restore')}>
                      {busy === `r:${b}` ? <Spinner size={13} /> : <RotateCcw size={14} />} {t('system.overview.database.restore')}
                    </button>
                    <button className="iconbtn inline-grid h-7 w-7 align-middle" disabled={busy != null} onClick={() => remove(b)} aria-label={t('system.overview.database.delete_named', { name: b })} title={t('common.delete')}>
                      {busy === `d:${b}` ? <Spinner size={13} /> : <Trash2 size={14} />}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-0 text-ink-3">{list ? t('system.overview.database.none') : t('system.overview.database.not_listed')}</p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <button className="btn btn-sm" disabled={printing || busy != null} onClick={() => run('compact', () => client.call('server.database.compact'), t('system.overview.database.compacted'))} title={printing ? t('system.not_while_printing') : undefined}>
          {busy === 'compact' && <Spinner size={13} />} {t('system.overview.database.compact')}
        </button>
        <button className="btn btn-sm btn-primary" disabled={printing || busy != null} onClick={() => run('backup', () => client.call('server.database.post_backup', { filename: `${timestampName()}.db` }), t('system.overview.database.created'))} title={printing ? t('system.not_while_printing') : undefined}>
          {busy === 'backup' && <Spinner size={13} />} {t('system.overview.database.create_backup')}
        </button>
      </div>
    </Card>
  )
}

function McuCards() {
  const mcus = usePrinter((s) => s.objects.filter((o) => o === 'mcu' || o.startsWith('mcu ')))
  return (
    <>
      {mcus.map((m) => (
        <McuCard key={m} name={m} />
      ))}
    </>
  )
}

function McuCard({ name }: { name: string }) {
  const mcu = usePrinter((s) => s.status[name]) as McuStatus | undefined
  const [open, setOpen] = useState(false)
  if (!mcu) return null
  const c = mcu.mcu_constants ?? {}
  const st = mcu.last_stats ?? {}
  return (
    <Card
      title={t('system.mcu.title', { name: mcuLabel(name) })}
      icon={<Cpu size={15} />}
      aside={
        <button className="btn btn-ghost btn-sm" onClick={() => setOpen(true)}>
          {t('system.mcu.details')}
        </button>
      }
    >
      <Row k={t('system.mcu.klipper_name')} mono>
        {name}
      </Row>
      <Row k={t('system.mcu.micro_controller')}>{c.MCU}</Row>
      <Row k={t('system.mcu.frequency')}>{c.CLOCK_FREQ ? fmtFreq(+c.CLOCK_FREQ) : undefined}</Row>
      <Row k={t('system.mcu.version')} mono>
        {mcu.mcu_version}
      </Row>
      <Row k={t('system.mcu.application')}>{mcu.app}</Row>
      {mcu.non_critical_disconnected != null && <Row k={t('system.mcu.non_critical')}>{mcu.non_critical_disconnected ? <span className="pill tone-bad">{t('system.mcu.disconnected')}</span> : <span className="pill tone-good">{t('system.mcu.connected')}</span>}</Row>}
      <Row k={t('system.mcu.awake')}>{typeof st.mcu_awake === 'number' ? `${((st.mcu_awake / 5) * 100).toFixed(2)} %` : undefined}</Row>
      <McuDialog name={name} mcu={mcu} open={open} onClose={() => setOpen(false)} />
    </Card>
  )
}

type McuStatus = {
  mcu_version?: string
  mcu_build_versions?: string
  mcu_constants?: Record<string, string | number>
  mcu_kconfig?: string
  last_stats?: Record<string, number>
  app?: string
  non_critical_disconnected?: boolean
}

function McuDialog({ name, mcu, open, onClose }: { name: string; mcu: McuStatus; open: boolean; onClose: () => void }) {
  const table = (rows: Record<string, unknown>) => (
    <table className="table">
      <tbody>
        {Object.entries(rows)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([k, v]) => (
            <tr key={k}>
              <td className="code text-ink-2">{k}</td>
              <td className="code text-right break-all">{String(v)}</td>
            </tr>
          ))}
      </tbody>
    </table>
  )
  const kconfig = mcu.mcu_kconfig?.trim()
  return (
    <Dialog open={open} onClose={onClose} title={t('system.mcu.info_title', { name: mcuLabel(name) })} size="wide">
      <div className="grid gap-4">
        {mcu.mcu_version && (
          <div>
            <div className="section-label mb-1">{t('system.mcu.version')}</div>
            <div className="code break-all">{mcu.mcu_version}</div>
            {mcu.mcu_build_versions && <div className="code mt-1 break-all text-ink-3">{mcu.mcu_build_versions}</div>}
          </div>
        )}
        <div>
          <div className="section-label mb-1">{t('system.mcu.constants')}</div>
          {table(mcu.mcu_constants ?? {})}
        </div>
        <div>
          <div className="section-label mb-1">{t('system.mcu.last_stats')}</div>
          {table(mcu.last_stats ?? {})}
        </div>
        {kconfig && (
          <div>
            <div className="section-label mb-1">{t('system.mcu.build_config')}</div>
            <pre className="code m-0 max-h-80 overflow-auto rounded-lg bg-inset p-3">{kconfig}</pre>
          </div>
        )}
      </div>
    </Dialog>
  )
}

type Peripheral = Record<string, string | number | null | undefined>
const DEVICE_TABS = [
  { value: 'serial', method: 'machine.peripherals.serial', key: 'serial_devices' },
  { value: 'usb', method: 'machine.peripherals.usb', key: 'usb_devices' },
  { value: 'video', method: 'machine.peripherals.video', key: 'v4l2_devices' },
] as const
type DeviceTab = (typeof DEVICE_TABS)[number]['value']

/** Fluidd's Devices dialog: the serial, USB, and video devices Moonraker sees. */
function DevicesDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [tab, setTab] = useState<DeviceTab>('serial')
  const [found, setFound] = useState<Partial<Record<DeviceTab, Peripheral[] | string>>>({})
  const [loading, setLoading] = useState(false)
  const load = useCallback(async (which: DeviceTab) => {
    const def = DEVICE_TABS.find((d) => d.value === which)!
    setLoading(true)
    try {
      const r = await client.call<Record<string, Peripheral[]>>(def.method)
      setFound((f) => ({ ...f, [which]: which === 'video' ? [...(r.v4l2_devices ?? []), ...(r.libcamera_devices ?? [])] : (r[def.key] ?? []) }))
    } catch (e) {
      setFound((f) => ({ ...f, [which]: e instanceof Error ? e.message : String(e) }))
    }
    setLoading(false)
  }, [])
  useEffect(() => {
    if (open && !(tab in found)) load(tab)
  }, [open, tab, found, load])
  const list = found[tab]
  return (
    <Dialog open={open} onClose={onClose} title={t('system.overview.devices.title')} size="wide">
      <div className="mb-3 flex items-center gap-2">
        <Segmented value={tab} onChange={setTab} options={DEVICE_TABS.map((d) => ({ value: d.value, label: <>{d.value === 'usb' ? 'USB' : t(`system.overview.devices.${d.value}`)}{Array.isArray(found[d.value]) && <span className="chip px-1.5 py-0">{(found[d.value] as Peripheral[]).length}</span>}</> }))} label={t('system.overview.devices.type')} />
        <button className="iconbtn ml-auto" onClick={() => load(tab)} disabled={loading} aria-label={t('system.refresh')} title={t('system.refresh')}>
          <RefreshCw size={15} className={loading ? 'spin' : ''} />
        </button>
      </div>
      {typeof list === 'string' ? (
        <Empty>{list}</Empty>
      ) : !list ? (
        <div className="grid place-items-center p-6">
          <Spinner size={20} />
        </div>
      ) : list.length === 0 ? (
        <Empty>{t('system.overview.devices.none')}</Empty>
      ) : (
        <div className="grid gap-2.5">
          {list.map((d, i) => (
            <div key={i} className="rounded-xl bg-inset p-3" style={{ boxShadow: 'inset 0 0 0 1px var(--line)' }}>
              <div className="mb-1 flex items-center gap-2 font-semibold">
                <Cable size={14} className="text-accent-ink" />
                {String(d.product ?? d.camera_name ?? d.device_name ?? d.device_path ?? t('system.overview.devices.device'))}
              </div>
              {Object.entries(d)
                .filter(([, v]) => v != null && v !== '' && typeof v !== 'object')
                .map(([k, v]) => (
                  <Row key={k} k={<span className="code">{k}</span>} mono>
                    {String(v)}
                  </Row>
                ))}
            </div>
          ))}
        </div>
      )}
    </Dialog>
  )
}

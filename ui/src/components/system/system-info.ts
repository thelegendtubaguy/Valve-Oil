import { useEffect } from 'react'
import { client, usePrinter } from '../../state/printer'
import { createStore, useStore } from '../../lib/store'
import { setTotalMemory } from './metrics'

// machine.system_info, server.info, and printer.info for the System screen, loaded when it opens and on each
// reconnect. Service states follow notify_service_state_changed.

export type ServiceState = { active_state: string; sub_state: string }
export type NetworkInterface = { mac_address?: string; ip_addresses?: { family: string; address: string; is_link_local?: boolean }[] }
export type SystemInfo = {
  python?: { version_string?: string }
  cpu_info?: { cpu_count?: number; bits?: string; processor?: string; cpu_desc?: string; hardware_desc?: string; model?: string; total_memory?: number; memory_units?: string }
  sd_info?: { manufacturer?: string; manufacturer_date?: string; product_name?: string; product_revision?: string; capacity?: string; serial_number?: string }
  distribution?: { name?: string; id?: string; version?: string; like?: string; codename?: string; release_info?: { version_id?: string }; kernel_version?: string }
  virtualization?: { virt_type?: string; virt_identifier?: string }
  network?: Record<string, NetworkInterface>
  canbus?: Record<string, unknown>
  available_services?: string[]
  service_state?: Record<string, ServiceState>
  instance_ids?: { moonraker?: string; klipper?: string }
}
export type ServerInfo = {
  moonraker_version?: string
  api_version_string?: string
  failed_components?: string[]
  warnings?: string[]
  components?: string[]
  registered_directories?: string[]
  missing_klippy_requirements?: string[]
}
export type PrinterInfo = { hostname?: string; software_version?: string; state_message?: string; app?: string }

type Info = { system: SystemInfo | null; server: ServerInfo | null; printer: PrinterInfo | null; loading: boolean; error: string | null }
const info = createStore<Info>({ system: null, server: null, printer: null, loading: false, error: null })

export async function refreshSystemInfo() {
  info.set((s) => ({ ...s, loading: true }))
  const [sys, srv, pri] = await Promise.allSettled([
    client.call<{ system_info: SystemInfo }>('machine.system_info'),
    client.call<ServerInfo>('server.info'),
    client.call<PrinterInfo>('printer.info'),
  ])
  if (sys.status === 'fulfilled') setTotalMemory(sys.value.system_info.cpu_info?.total_memory)
  info.set((s) => ({
    system: sys.status === 'fulfilled' ? sys.value.system_info : s.system,
    server: srv.status === 'fulfilled' ? srv.value : s.server,
    printer: pri.status === 'fulfilled' ? pri.value : s.printer,
    loading: false,
    error: sys.status === 'rejected' ? (sys.reason instanceof Error ? sys.reason.message : String(sys.reason)) : null,
  }))
}

client.on('notify_service_state_changed', (p) => {
  const changes = p[0] as Record<string, Partial<ServiceState>> | undefined
  if (!changes) return
  info.set((s) => {
    if (!s.system) return s
    const service_state = { ...s.system.service_state }
    for (const [name, st] of Object.entries(changes)) service_state[name] = { ...service_state[name], ...st } as ServiceState
    return { ...s, system: { ...s.system, service_state } }
  })
})

/** Loads the info when the System screen opens and again whenever Moonraker or Klipper reconnects. */
export function useLoadSystemInfo() {
  const conn = usePrinter((s) => s.conn)
  const klippy = usePrinter((s) => s.klippy)
  useEffect(() => {
    if (conn === 'open') refreshSystemInfo()
  }, [conn, klippy])
}

export function useSystemInfo(): Info {
  return useStore(info, (s) => s)
}

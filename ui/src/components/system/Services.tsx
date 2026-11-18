import { useState, type ReactNode } from 'react'
import { Cog, Play, Power, PowerOff, RotateCw, Square, Zap } from 'lucide-react'
import { Card, Empty, Spinner, confirm } from '../ui'
import { attempt, toast } from '../toast'
import { client, usePrinter } from '../../state/printer'
import { useUpdateRunning } from '../../state/valve-oil'
import { serviceTone } from './bits'
import { refreshSystemInfo, useSystemInfo } from './system-info'

// What each service on a QIDI printer is, for people who don't know the unit names.
const ABOUT: Record<string, string> = {
  klipper: 'Runs the printer',
  moonraker: 'Klipper’s API server, which Fluidd and Valve Oil UI talk to',
  'klipper-mcu': 'Klipper’s host MCU, for pins on the printer’s computer',
  'qidi-client': 'QIDI’s touchscreen app',
  crowsnest: 'Streams the camera',
  'tltg-remote-screen': 'Streams the touchscreen to the Printer screen tab',
  'tltg-status': 'Valve Oil’s update status',
  'moonraker-obico': 'Obico',
  octoeverywhere: 'OctoEverywhere',
  tailscaled: 'Tailscale',
  'zerotier-one': 'ZeroTier',
}

const PRINT_STOPS = 'A print in progress stops.'

export function Services() {
  const { system, loading } = useSystemInfo()
  const printState = usePrinter((s) => s.status.print_stats?.state as string | undefined)
  const printing = printState === 'printing' || printState === 'paused'
  const updating = useUpdateRunning()
  const services = Object.entries(system?.service_state ?? {}).sort(([a], [b]) => order(a) - order(b) || a.localeCompare(b))
  const canHost = system?.virtualization?.virt_type !== 'container'
  return (
    <div className="grid grid-cols-1 items-start gap-3.5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <Card title="Services" icon={<Cog size={15} />} aside={loading ? <Spinner size={14} /> : undefined}>
        {services.length === 0 ? (
          <Empty>{loading ? 'Loading…' : 'Moonraker didn’t list any services.'}</Empty>
        ) : (
          <div className="grid gap-2">
            {services.map(([name, st]) => (
              <ServiceRow key={name} name={name} active={st.active_state} sub={st.sub_state} printing={printing} locked={updating && (name === 'klipper' || name === 'moonraker')} />
            ))}
          </div>
        )}
        {updating && <p className="mb-0 text-[12.5px] text-ink-3">Klipper and Moonraker can’t be restarted while Valve Oil updates the printer.</p>}
      </Card>
      <div className="grid min-w-0 grid-cols-1 gap-3.5">
        {!updating && <KlipperCard printing={printing} />}
        {canHost && <HostPowerCard printing={printing} />}
      </div>
    </div>
  )
}

const FIRST = ['klipper', 'moonraker', 'qidi-client', 'klipper-mcu', 'crowsnest']
const order = (n: string) => (FIRST.includes(n) ? FIRST.indexOf(n) : FIRST.length)

function ServiceRow({ name, active, sub, printing, locked }: { name: string; active: string; sub: string; printing: boolean; locked: boolean }) {
  const [busy, setBusy] = useState<string | null>(null)
  const stopped = active === 'inactive' || active === 'failed'
  const act = async (action: 'start' | 'stop' | 'restart') => {
    if (!(await ask(name, action, printing))) return
    setBusy(action)
    const ok = await attempt(async () => (await client.call(`machine.services.${action}`, { service: name }, 90000), true))
    setBusy(null)
    if (ok && name !== 'moonraker') toast(`${name} ${action === 'stop' ? 'stopped' : action === 'start' ? 'started' : 'restarted'}`)
    // Moonraker announces most changes; refresh in case this service isn't one it watches.
    refreshSystemInfo()
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl bg-inset px-3 py-2.5" style={{ boxShadow: 'inset 0 0 0 1px var(--line)' }}>
      <div className="min-w-0 flex-1 basis-48">
        <div className="flex flex-wrap items-center gap-2">
          <span className="code font-semibold text-ink">{name}</span>
          <span className={`pill ${serviceTone(active)} py-0.5 text-[11.5px]`}>
            {active}
            {sub && sub !== active ? ` (${sub})` : ''}
          </span>
        </div>
        {ABOUT[name] && <div className="mt-0.5 text-[12.5px] text-ink-3">{ABOUT[name]}</div>}
      </div>
      <div className="flex flex-none gap-1.5">
        {stopped ? (
          <button className="btn btn-sm" disabled={locked || busy != null} onClick={() => act('start')}>
            {busy === 'start' ? <Spinner size={13} /> : <Play size={14} />} Start
          </button>
        ) : (
          <button className="btn btn-sm" disabled={locked || busy != null} onClick={() => act('restart')}>
            {busy === 'restart' ? <Spinner size={13} /> : <RotateCw size={14} />} Restart
          </button>
        )}
        {/* Stopping Moonraker would take this page with it, with no way back but SSH, so Fluidd hides it too. */}
        {name !== 'moonraker' && (
          <button className="btn btn-sm btn-danger" disabled={locked || busy != null || stopped} onClick={() => act('stop')}>
            {busy === 'stop' ? <Spinner size={13} /> : <Square size={13} />} Stop
          </button>
        )}
      </div>
    </div>
  )
}

function ask(name: string, action: 'start' | 'stop' | 'restart', printing: boolean): Promise<boolean> {
  const verb = action === 'start' ? 'Start' : action === 'stop' ? 'Stop' : 'Restart'
  const bodies: ReactNode[] = []
  if (name === 'klipper' && action !== 'start') bodies.push(action === 'stop' ? 'The printer stops responding until Klipper is started again.' : 'Klipper reloads its configuration and reconnects to the printer’s boards.')
  if (name === 'moonraker') bodies.push('Fluidd and Valve Oil UI lose the printer for a few seconds, then reconnect.')
  if (name === 'qidi-client') bodies.push(action === 'stop' ? 'The touchscreen stays off until you start it again or the printer reboots.' : 'The touchscreen goes blank for a few seconds while its app restarts.')
  if (printing && (name === 'klipper' || (name === 'klipper-mcu' && action !== 'start'))) bodies.push(PRINT_STOPS)
  if (action === 'start' && !printing) return Promise.resolve(true)
  return confirm({ title: `${verb} ${name}?`, body: bodies.length ? bodies.join(' ') : `${verb} the ${name} service.`, confirm: verb, danger: action !== 'start' })
}

function KlipperCard({ printing }: { printing: boolean }) {
  const klippy = usePrinter((s) => s.klippy)
  const message = usePrinter((s) => s.klippyMessage)
  const connected = klippy !== 'disconnected' && klippy !== 'unknown'
  const [busy, setBusy] = useState<string | null>(null)
  const run = async (key: string, title: string, body: string, fn: () => Promise<unknown>) => {
    if (!(await confirm({ title, body: printing ? `${body} ${PRINT_STOPS}` : body, confirm: title.replace(/\?$/, ''), danger: true }))) return
    setBusy(key)
    await attempt(fn)
    setBusy(null)
  }
  return (
    <Card title="Klipper" icon={<Zap size={15} />} aside={<span className={`pill ${klippy === 'ready' ? 'tone-good' : klippy === 'startup' ? 'tone-warn' : 'tone-bad'} py-0.5 text-[11.5px]`}>{klippy}</span>}>
      {klippy !== 'ready' && message && <pre className="code m-0 mb-3 max-h-48 overflow-auto rounded-lg bg-inset p-2.5 whitespace-pre-wrap">{message}</pre>}
      <div className="grid gap-2 sm:grid-cols-2">
        {connected ? (
          <>
            <button
              className="btn"
              disabled={busy != null}
              title="Reloads Klipper’s configuration"
              onClick={() => run('restart', 'Restart Klipper?', 'Klipper reloads its configuration.', () => client.call('printer.restart'))}
            >
              {busy === 'restart' ? <Spinner size={14} /> : <RotateCw size={15} />} Restart Klipper
            </button>
            <button
              className="btn"
              disabled={busy != null}
              title="Reloads Klipper’s configuration and restarts the MCUs"
              onClick={() => run('fw', 'Firmware restart?', 'Klipper reloads its configuration and restarts the printer’s boards.', () => client.call('printer.firmware_restart'))}
            >
              {busy === 'fw' ? <Spinner size={14} /> : <Zap size={15} />} Firmware restart
            </button>
          </>
        ) : (
          <button className="btn" disabled={busy != null} title="Restarts the Klipper service" onClick={() => run('svc', 'Restart Klipper?', 'The Klipper service restarts.', () => client.call('machine.services.restart', { service: 'klipper' }, 90000))}>
            {busy === 'svc' ? <Spinner size={14} /> : <RotateCw size={15} />} Restart Klipper
          </button>
        )}
      </div>
    </Card>
  )
}

function HostPowerCard({ printing }: { printing: boolean }) {
  const go = async (what: 'reboot' | 'shutdown') => {
    const reboot = what === 'reboot'
    const ok = await confirm({
      title: reboot ? 'Reboot the printer?' : 'Shut down the printer?',
      body: `${reboot ? 'The printer’s computer restarts, and Valve Oil UI reconnects when it’s back, in about a minute.' : 'The printer’s computer shuts down. Turn the printer off and on again to start it.'}${printing ? ` ${PRINT_STOPS}` : ''}`,
      confirm: reboot ? 'Reboot' : 'Shut down',
      danger: true,
    })
    if (!ok) return
    // Moonraker goes away before it answers, so a lost connection here is expected.
    client.call(`machine.${what}`).catch(() => {})
    toast(reboot ? 'Rebooting the printer' : 'Shutting down the printer')
  }
  return (
    <Card title="Host" icon={<Power size={15} />}>
      <p className="mt-0 mb-3 text-ink-3">The printer’s computer, which runs Klipper, Moonraker, and the touchscreen.</p>
      <div className="grid gap-2 sm:grid-cols-2">
        <button className="btn btn-danger" onClick={() => go('reboot')}>
          <RotateCw size={15} /> Reboot
        </button>
        <button className="btn btn-danger" onClick={() => go('shutdown')}>
          <PowerOff size={15} /> Shut down
        </button>
      </div>
    </Card>
  )
}

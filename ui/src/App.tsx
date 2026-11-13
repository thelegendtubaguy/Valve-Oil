import { type ReactNode } from 'react'
import { Box, ExternalLink, Folder, House, LoaderCircle, Moon, Power, RefreshCw, Sun, TriangleAlert, X } from 'lucide-react'
import { href, useRoute, type Route } from './lib/router'
import { client, runGcode, usePrinter } from './state/printer'
import { useJob, jobProgress } from './state/job'
import { useBoxPresent, useBoxBusy } from './state/box'
import { useWarnings } from './state/warnings'
import { Toasts, attempt } from './components/toast'
import { HomeScreen } from './screens/Home'
import { FilesScreen } from './screens/Files'
import { BoxScreen } from './screens/Box'
import { DONT_POWER_OFF, UpdatesScreen } from './screens/Updates'
import { useUpdateRunning } from './state/valve-oil'
import { fluiddUrl } from './lib/links'
import { useTheme } from './lib/theme'

const NAV: { route: Route; label: string; icon: ReactNode }[] = [
  { route: 'home', label: 'Home', icon: <House size={16} /> },
  { route: 'files', label: 'Files', icon: <Folder size={16} /> },
  { route: 'box', label: 'QIDI Box', icon: <Box size={16} /> },
  { route: 'updates', label: 'Updates', icon: <RefreshCw size={16} /> },
]

export function App() {
  const route = useRoute()
  const conn = usePrinter((s) => s.conn)
  const klippy = usePrinter((s) => s.klippy)
  const boxPresent = useBoxPresent()
  const nav = NAV.filter((n) => n.route !== 'box' || boxPresent)

  let body: ReactNode
  // Updates talks to Valve Oil's status service, not Moonraker, so it stays up while an update restarts Moonraker.
  if (route === 'updates') body = <UpdatesScreen />
  else if (conn !== 'open') body = <NotReady title={conn === 'connecting' ? 'Connecting to the printer…' : 'Can’t reach the printer'} detail={`Trying ${client.wsUrl} again.`} />
  else if (klippy !== 'ready') body = <KlippyNotReady />
  else if (route === 'files') body = <FilesScreen />
  else if (route === 'box' && boxPresent) body = <BoxScreen />
  else body = <HomeScreen />

  return (
    <div className="flex h-full">
      <aside className="hidden w-[208px] flex-none flex-col gap-0.5 border-r border-line bg-page px-2.5 py-3.5 md:flex">
        <Brand />
        {nav.map((n) => (
          <a key={n.route} href={href(n.route)} className={`nav-item ${route === n.route ? 'active' : ''}`}>
            {n.icon}
            {n.label}
            {n.route === 'box' && <BoxBusyBadge />}
            {n.route === 'updates' && <UpdatingBadge />}
          </a>
        ))}
        <a href={fluiddUrl()} className="nav-item mt-auto border border-dashed border-line-strong text-[12.5px] text-ink-3">
          <ExternalLink size={14} /> Open Fluidd
        </a>
        <div className="px-2 pt-2 text-[11px] text-ink-3">Valve Oil UI {__APP_VERSION__}</div>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <UpdateBanner />
        <Warnings />
        <div className="min-h-0 flex-1 overflow-auto">
          <div className={route === 'home' && conn === 'open' && klippy === 'ready' ? 'h-full p-3.5 pb-24 md:p-[18px] md:pb-[18px]' : 'mx-auto grid max-w-[1500px] content-start gap-3.5 p-3.5 pb-24 md:p-[18px] md:pb-[18px]'}>{body}</div>
        </div>
        <nav className="flex justify-around border-t border-line bg-page px-1.5 pt-2 pb-[max(10px,env(safe-area-inset-bottom))] md:hidden">
          {nav.map((n) => (
            <a key={n.route} href={href(n.route)} className={`grid justify-items-center gap-0.5 px-3 text-[10.5px] ${route === n.route ? 'text-accent' : 'text-ink-3'}`}>
              {n.icon}
              {n.label}
            </a>
          ))}
          <a href={fluiddUrl()} className="grid justify-items-center gap-0.5 px-3 text-[10.5px] text-ink-3">
            <ExternalLink size={16} />
            Fluidd
          </a>
        </nav>
      </main>
      <Toasts />
    </div>
  )
}

function Brand() {
  const name = usePrinter((s) => s.status.tltg_printer?.name ?? null) as string | null
  return (
    <div className="flex items-center gap-2.5 px-2 pt-1 pb-3.5 font-semibold">
      <img src="./favicon.svg" width={26} height={26} alt="" />
      <div className="min-w-0">
        {name ?? 'Valve Oil'}
        <small className="block text-[11.5px] font-medium text-ink-3">{location.hostname}</small>
      </div>
    </div>
  )
}

function BoxBusyBadge() {
  const busy = useBoxBusy()
  return busy ? <span className="ml-auto rounded-full bg-warn-tint px-1.5 text-[11px] text-warn">busy</span> : null
}

function UpdatingBadge() {
  return useUpdateRunning() ? <span className="ml-auto rounded-full bg-warn-tint px-1.5 text-[11px] text-warn">updating</span> : null
}

/** While Valve Oil updates the printer, on every screen, and through Moonraker and Klipper restarting for it. */
function UpdateBanner() {
  const running = useUpdateRunning()
  const route = useRoute()
  // The Updates screen says the same in its own card.
  if (!running || route === 'updates') return null
  return (
    <div className="border-b border-line bg-page px-3.5 py-2 md:px-[18px]">
      <div className="tone-warn flex items-start gap-2.5 rounded-lg px-3 py-2">
        <LoaderCircle size={16} className="spin mt-0.5 flex-none" />
        <div className="min-w-0 flex-1 text-ink">
          <b>Valve Oil is updating the printer.</b> <span className="text-ink-2">{DONT_POWER_OFF}</span>
        </div>
        <a href={href('updates')} className="flex-none font-medium text-accent-ink">
          Details
        </a>
      </div>
    </div>
  )
}

function StatusPill() {
  const job = useJob()
  const conn = usePrinter((s) => s.conn)
  const klippy = usePrinter((s) => s.klippy)
  const busy = useBoxBusy()
  const idle = usePrinter((s) => s.status.idle_timeout?.state as string | undefined)
  if (conn !== 'open') return <span className="pill tone-bad"><span className="dot" />Offline</span>
  if (klippy !== 'ready') return <span className="pill tone-bad"><span className="dot" />Klipper {klippy}</span>
  if (job.state === 'printing') return <span className="pill tone-accent"><span className="dot" />Printing · {Math.floor(jobProgress(job) * 100)}%</span>
  if (job.state === 'paused') return <span className="pill tone-warn"><span className="dot" />Paused</span>
  if (busy) return <span className="pill tone-warn"><span className="dot" />Box busy</span>
  if (idle === 'Printing') return <span className="pill tone-accent"><span className="dot" />Busy</span>
  return <span className="pill tone-good"><span className="dot" />Ready</span>
}

function TopBar() {
  const [theme, toggleTheme] = useTheme()
  const conn = usePrinter((s) => s.conn)
  return (
    <div className="flex items-center gap-2.5 border-b border-line bg-page px-3.5 py-2.5 pt-[max(10px,env(safe-area-inset-top))] md:px-[18px]">
      <StatusPill />
      <div className="flex-1" />
      <button className="iconbtn" onClick={toggleTheme} aria-label="Switch theme" title="Switch theme">
        {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
      </button>
      <button
        className="estop"
        disabled={conn !== 'open'}
        onClick={() => attempt(() => client.call('printer.emergency_stop'))}
        title="Emergency stop: halts the printer immediately. Klipper must be restarted afterward."
      >
        <X size={14} /> <span className="hidden sm:inline">Emergency stop</span><span className="sm:hidden">Stop</span>
      </button>
    </div>
  )
}

function Warnings() {
  const warnings = useWarnings()
  const route = useRoute()
  if (!warnings.length) return null
  return (
    <div className="grid gap-1.5 border-b border-line bg-page px-3.5 py-2 md:px-[18px]">
      {warnings.map((w, i) => (
        <div key={i} className={`flex items-start gap-2.5 rounded-lg px-3 py-2 ${w.level === 'error' ? 'tone-bad' : 'tone-warn'}`}>
          <TriangleAlert size={16} className="mt-0.5 flex-none" />
          <div className="min-w-0 text-ink [overflow-wrap:anywhere]">
            <b>{w.title}</b>
            {w.message && <span className="text-ink-2"> {w.message}</span>}
            {/* A board's issue link (checked by tltgPrinterWarnings), and the Updates screen for Valve Oil's update warnings. */}
            {w.url && (
              <>
                {' '}
                <a href={w.url} target="_blank" rel="noopener noreferrer" className="font-medium whitespace-nowrap text-accent-ink underline">
                  Report this board
                </a>
              </>
            )}
            {w.id.startsWith('valve_oil_') && route !== 'updates' && (
              <>
                {' '}
                <a href={href('updates')} className="font-medium whitespace-nowrap text-accent-ink underline">
                  Details
                </a>
              </>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}

function NotReady({ title, detail, children }: { title: string; detail?: string; children?: ReactNode }) {
  return (
    <div className="card mx-auto mt-[10vh] grid w-full max-w-[520px] justify-items-center gap-2 p-8 text-center">
      <Power size={28} className="text-ink-3" />
      <div className="text-[16px] font-semibold">{title}</div>
      {detail && <div className="whitespace-pre-line text-ink-2">{detail}</div>}
      {children && <div className="mt-3 flex flex-wrap justify-center gap-2">{children}</div>}
    </div>
  )
}

function KlippyNotReady() {
  const klippy = usePrinter((s) => s.klippy)
  const message = usePrinter((s) => s.klippyMessage)
  // Valve Oil stops and restarts Klipper itself while it updates; a restart from here could interrupt a board flash.
  const updating = useUpdateRunning()
  const title = klippy === 'startup' ? 'Klipper is starting…' : klippy === 'shutdown' ? 'Klipper has shut down' : klippy === 'error' ? 'Klipper reported an error' : 'Klipper isn’t running'
  return (
    <NotReady title={title} detail={message}>
      {klippy !== 'startup' && !updating && (
        <>
          <button className="btn" onClick={() => attempt(() => client.call('printer.restart'))}>Restart Klipper</button>
          <button className="btn btn-primary" onClick={() => attempt(() => client.call('printer.firmware_restart'))}>Firmware restart</button>
        </>
      )}
      <a className="btn btn-ghost" href={fluiddUrl()}>Open Fluidd</a>
    </NotReady>
  )
}

// Re-exported for screens that need to send G-code with console logging.
export { runGcode }

import { lazy, Suspense, useState, type ReactNode } from 'react'
import {
  Cpu,
  Ellipsis,
  ExternalLink,
  Folder,
  Grid3x3,
  History,
  House,
  LayoutGrid,
  LoaderCircle,
  Moon,
  Move,
  RefreshCw,
  SlidersHorizontal,
  SquareTerminal,
  Sun,
  TriangleAlert,
  X,
} from 'lucide-react'
import { href, useRoute, type Route } from './lib/router'
import { client, runGcode, usePrinter } from './state/printer'
import { useJob, jobProgress } from './state/job'
import { useBoxPresent, useBoxBusy } from './state/box'
import { useWarnings } from './state/warnings'
import { Toasts, attempt } from './components/toast'
import { UploadsPanel } from './components/Upload'
import { BedScrewsDialog, ManualProbeDialog } from './components/tune/HelperDialogs'
import { AskHost, Spinner, confirm } from './components/ui'
import { LogoMark } from './components/Logo'
import { NotificationsButton } from './components/Notifications'
import { useUiSetting } from './components/settings/fluidd'
import { HomeScreen } from './screens/Home'
import { BoxScreen } from './screens/Box'
import { DONT_POWER_OFF, UpdatesScreen } from './screens/Updates'
import { useUpdateRunning, valveOil } from './state/valve-oil'
import { useStore } from './lib/store'
import { installStepText, installTitle } from './components/updates/Install'
import { fluiddUrl } from './lib/links'
import { useTheme } from './lib/theme'

// Screens other than Home, the Box, and Updates load when first opened, so the first page a tablet shows is small.
const FilesScreen = lazy(() => import('./screens/Files').then((m) => ({ default: m.FilesScreen })))
const ControlScreen = lazy(() => import('./screens/Control').then((m) => ({ default: m.ControlScreen })))
const ConsoleScreen = lazy(() => import('./screens/Console').then((m) => ({ default: m.ConsoleScreen })))
const TuneScreen = lazy(() => import('./screens/Tune').then((m) => ({ default: m.TuneScreen })))
const HistoryScreen = lazy(() => import('./screens/History').then((m) => ({ default: m.HistoryScreen })))
const SystemScreen = lazy(() => import('./screens/System').then((m) => ({ default: m.SystemScreen })))
const SettingsScreen = lazy(() => import('./screens/Settings').then((m) => ({ default: m.SettingsScreen })))

type NavItem = { route: Route; label: string; icon: ReactNode; mobile?: boolean }
const I = 22
const NAV: NavItem[] = [
  { route: 'home', label: 'Home', icon: <House size={I} />, mobile: true },
  { route: 'files', label: 'Files', icon: <Folder size={I} />, mobile: true },
  { route: 'box', label: 'Box', icon: <LayoutGrid size={I} />, mobile: true },
  { route: 'control', label: 'Control', icon: <Move size={I} />, mobile: true },
  { route: 'console', label: 'Console', icon: <SquareTerminal size={I} /> },
  { route: 'tune', label: 'Tune', icon: <Grid3x3 size={I} /> },
  { route: 'history', label: 'History', icon: <History size={I} /> },
  { route: 'system', label: 'System', icon: <Cpu size={I} /> },
]
const NAV_END: NavItem[] = [
  { route: 'settings', label: 'Settings', icon: <SlidersHorizontal size={I} /> },
  { route: 'updates', label: 'Updates', icon: <RefreshCw size={I} /> },
]

// Screens that only make sense with Klipper running. The rest need only Moonraker, so the configuration can be fixed
// and the logs read while Klipper is stopped, and Updates needs neither.
const NEEDS_KLIPPER: Route[] = ['home', 'box', 'control', 'tune']

const SCREENS: Record<Route, () => ReactNode> = {
  home: () => <HomeScreen />,
  files: () => <FilesScreen />,
  box: () => <BoxScreen />,
  control: () => <ControlScreen />,
  console: () => <ConsoleScreen />,
  tune: () => <TuneScreen />,
  history: () => <HistoryScreen />,
  system: () => <SystemScreen />,
  settings: () => <SettingsScreen />,
  updates: () => <UpdatesScreen />,
}

export function App() {
  const route = useRoute()
  const conn = usePrinter((s) => s.conn)
  const klippy = usePrinter((s) => s.klippy)
  const boxPresent = useBoxPresent()
  const nav = NAV.filter((n) => n.route !== 'box' || boxPresent)
  const shown: Route = route === 'box' && !boxPresent && klippy === 'ready' ? 'home' : route

  let body: ReactNode
  // Updates talks to Valve Oil's status service, not Moonraker, so it stays up while an update restarts Moonraker.
  if (shown === 'updates') body = <UpdatesScreen />
  else if (conn !== 'open') body = <NotReady title={conn === 'connecting' ? 'Connecting to the printer…' : 'Can’t reach the printer'} detail={`Trying ${client.wsUrl} again.`} />
  else if (klippy !== 'ready' && NEEDS_KLIPPER.includes(shown)) body = <KlippyNotReady />
  else body = <Suspense fallback={<div className="grid place-items-center py-16"><Spinner size={24} /></div>}>{SCREENS[shown]()}</Suspense>

  const cockpit = shown === 'home' && conn === 'open' && klippy === 'ready'
  return (
    <div className="flex h-full">
      <aside className="rail hidden md:flex">
        <a href={href('home')} className="mb-2 grid place-items-center pt-1 pb-1" title="Valve Oil">
          <LogoMark height={46} />
        </a>
        {nav.map((n) => (
          <RailItem key={n.route} item={n} on={shown === n.route} />
        ))}
        <div className="mt-auto" />
        {NAV_END.map((n) => (
          <RailItem key={n.route} item={n} on={shown === n.route} />
        ))}
        <a href={fluiddUrl()} className="rail-item" title="Open Fluidd">
          <ExternalLink size={18} />
          Fluidd
        </a>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <UpdateBanner />
        <Warnings />
        {klippy !== 'ready' && conn === 'open' && !NEEDS_KLIPPER.includes(shown) && shown !== 'updates' && <KlippyStrip />}
        <div className="min-h-0 flex-1 overflow-auto">
          <div className={cockpit ? 'h-full p-3.5 pb-24 md:p-[18px] md:pb-[18px]' : 'mx-auto grid max-w-[1500px] content-start gap-3.5 p-3.5 pb-24 md:p-[18px] md:pb-[18px]'}>{body}</div>
        </div>
        <MobileNav nav={nav} route={shown} />
      </main>
      <Toasts />
      {/* Uploads started on any screen keep showing their progress here. */}
      <UploadsPanel />
      {/* Klipper's manual probe and bed screws helpers wait for the operator wherever they started from, as in Fluidd. */}
      {conn === 'open' && (
        <>
          <ManualProbeDialog />
          <BedScrewsDialog />
        </>
      )}
      <AskHost />
    </div>
  )
}

function RailItem({ item, on }: { item: NavItem; on: boolean }) {
  return (
    <a href={href(item.route)} className={`rail-item ${on ? 'on' : ''}`} aria-current={on ? 'page' : undefined}>
      {item.icon}
      {item.label}
      {item.route === 'box' && <BoxBusyBadge />}
      {item.route === 'updates' && <UpdatingBadge />}
    </a>
  )
}

function MobileNav({ nav, route }: { nav: NavItem[]; route: Route }) {
  const [more, setMore] = useState(false)
  const main = nav.filter((n) => n.mobile)
  const rest = [...nav.filter((n) => !n.mobile), ...NAV_END]
  const inRest = rest.some((n) => n.route === route)
  const cls = (on: boolean) => `grid justify-items-center gap-1 px-2 text-[10.5px] font-semibold ${on ? 'text-accent-ink' : 'text-ink-3'}`
  return (
    <>
      {more && (
        <div className="fixed inset-0 z-40 md:hidden" onClick={() => setMore(false)}>
          <div className="absolute inset-x-2 bottom-[72px] grid grid-cols-4 gap-1 rounded-2xl bg-surface p-2" style={{ boxShadow: 'var(--shadow-overlay)' }}>
            {rest.map((n) => (
              <a key={n.route} href={href(n.route)} className={`rail-item w-auto ${route === n.route ? 'on' : ''}`}>
                {n.icon}
                {n.label}
              </a>
            ))}
            <a href={fluiddUrl()} className="rail-item w-auto">
              <ExternalLink size={20} />
              Fluidd
            </a>
          </div>
        </div>
      )}
      <nav className="flex justify-around border-t border-line px-1.5 pt-2 pb-[max(10px,env(safe-area-inset-bottom))] md:hidden" style={{ background: 'var(--rail)' }}>
        {main.map((n) => (
          <a key={n.route} href={href(n.route)} className={cls(route === n.route)} onClick={() => setMore(false)}>
            {n.icon}
            {n.label}
          </a>
        ))}
        <button className={`border-0 bg-transparent ${cls(inRest || more)}`} onClick={() => setMore((m) => !m)} aria-expanded={more}>
          <Ellipsis size={I} />
          More
        </button>
      </nav>
    </>
  )
}

function BoxBusyBadge() {
  return useBoxBusy() ? <span className="badge" title="Box busy" /> : null
}

function UpdatingBadge() {
  return useUpdateRunning() ? <span className="badge" title="Updating" /> : null
}

/** While Valve Oil updates the printer, on every screen, and through Moonraker and Klipper restarting for it. */
function UpdateBanner() {
  const running = useUpdateRunning()
  const route = useRoute()
  const install = useStore(valveOil, (s) => (s.status?.progress?.state === 'running' ? s.status.progress : null))
  // The Updates screen says the same in its own card.
  if (!running || route === 'updates') return null
  return (
    <div className="border-b border-line bg-page px-3.5 py-2 md:px-[18px]">
      <div className="tone-warn flex items-start gap-2.5 rounded-xl px-3 py-2">
        <LoaderCircle size={16} className="spin mt-0.5 flex-none" />
        <div className="min-w-0 flex-1 text-ink">
          <b>{install ? `${installTitle(install)}.` : 'Valve Oil is updating the printer.'}</b>{' '}
          {install && <span className="text-ink-2">{installStepText(install)}. </span>}
          <span className="text-ink-2">{DONT_POWER_OFF}</span>
        </div>
        <a href={href('updates')} className="flex-none font-semibold text-accent-ink">
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
  if (conn !== 'open') return <span className="pill tone-bad">Offline</span>
  if (klippy !== 'ready') return <span className="pill tone-bad">Klipper {klippy}</span>
  if (job.state === 'printing') return <span className="pill tone-accent">Printing · {Math.floor(jobProgress(job) * 100)}%</span>
  if (job.state === 'paused') return <span className="pill tone-warn">Paused</span>
  if (busy) return <span className="pill tone-warn">Box busy</span>
  if (idle === 'Printing') return <span className="pill tone-accent">Busy</span>
  return null
}

/** Nozzle, bed, and chamber at a glance, amber while heating, as across the top of the touchscreen. */
function MiniTemps() {
  const temps = usePrinter((s) =>
    (
      [
        ['Nozzle', s.status.extruder],
        ['Bed', s.status.heater_bed],
        ['Chamber', s.status['heater_generic chamber']],
      ] as const
    )
      .filter(([, o]) => o && typeof o.temperature === 'number')
      .map(([label, o]) => `${label}:${Math.round(o!.temperature)}:${o!.target > 0 ? 1 : 0}`)
      .join('|'),
  )
  if (!temps) return null
  return (
    <div className="hidden items-center gap-3.5 lg:flex">
      {temps.split('|').map((t) => {
        const [label, v, hot] = t.split(':')
        return (
          <span key={label} className={`num flex items-baseline gap-1 text-[13px] font-semibold ${hot === '1' ? 'text-accent-ink' : 'text-ink-2'}`} title={label}>
            <span className="text-[11px] font-semibold text-ink-3">{label}</span>
            {v}°
          </span>
        )
      })}
    </div>
  )
}

/**
 * Klipper holds changes (a calibration, a saved mesh) until SAVE_CONFIG writes them to printer.cfg and restarts it.
 * Fluidd shows a save button whenever that's pending; so does this.
 */
function SaveConfigButton() {
  const pending = usePrinter((s) => !!s.status.configfile?.save_config_pending && s.klippy === 'ready')
  const items = usePrinter((s) => s.status.configfile?.save_config_pending_items as Record<string, unknown> | undefined)
  const printing = usePrinter((s) => ['printing', 'paused'].includes(s.status.print_stats?.state))
  if (!pending) return null
  const sections = Object.keys(items ?? {})
  return (
    <button
      className="btn btn-sm btn-primary"
      disabled={printing}
      title={printing ? 'Save config restarts Klipper, so it waits until the print ends.' : undefined}
      onClick={async () => {
        const ok = await confirm({
          title: 'Save config and restart Klipper?',
          body: (
            <>
              SAVE_CONFIG writes Klipper's pending changes to printer.cfg and restarts Klipper.
              {sections.length > 0 && <div className="mt-2">Changed: {sections.join(', ')}</div>}
            </>
          ),
          confirm: 'Save and restart',
        })
        if (ok) attempt(() => runGcode('SAVE_CONFIG'))
      }}
    >
      Save config
    </button>
  )
}

function TopBar() {
  const [theme, toggleTheme] = useTheme()
  const [confirmEstop] = useUiSetting('general.confirmOnEstop')
  const conn = usePrinter((s) => s.conn)
  // The model the installer detected (QIDI Max 4, Plus 5, or Smart 5), from Valve Oil's status service.
  const name = useStore(valveOil, (s) => s.status?.printer.name ?? null)
  return (
    <div className="flex items-center gap-3 border-b border-line px-3.5 py-2.5 pt-[max(10px,env(safe-area-inset-top))] md:px-[18px]" style={{ background: 'var(--page)' }}>
      <a href={href('home')} className="flex items-center gap-2.5 md:hidden">
        <LogoMark height={30} />
      </a>
      <span className="wordmark hidden sm:inline">VALVE OIL</span>
      {name && (
        <>
          <span className="hidden text-ink-3 sm:inline">|</span>
          <span className="hidden min-w-0 truncate font-semibold text-ink-2 sm:inline">{name}</span>
        </>
      )}
      <StatusPill />
      <div className="flex-1" />
      <SaveConfigButton />
      <MiniTemps />
      <NotificationsButton />
      <button className="iconbtn" onClick={toggleTheme} aria-label="Switch theme" title="Switch theme">
        {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
      </button>
      <button
        className="estop"
        disabled={conn !== 'open'}
        onClick={async () => {
          // Fluidd's "Confirm emergency stop" setting, shared with it.
          if (confirmEstop && !(await confirm({ title: 'Emergency stop?', body: 'Klipper halts the printer at once and has to be restarted afterward.', confirm: 'Emergency stop', danger: true }))) return
          attempt(() => client.call('printer.emergency_stop'))
        }}
        title="Emergency stop: halts the printer immediately. Klipper must be restarted afterward."
      >
        <X size={14} /> <span className="hidden sm:inline">Emergency stop</span>
        <span className="sm:hidden">Stop</span>
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
        <div key={i} className={`flex items-start gap-2.5 rounded-xl px-3 py-2 ${w.level === 'error' ? 'tone-bad' : 'tone-warn'}`}>
          <TriangleAlert size={16} className="mt-0.5 flex-none" />
          <div className="min-w-0 text-ink [overflow-wrap:anywhere]">
            <b>{w.title}</b>
            {w.message && <span className="text-ink-2"> {w.message}</span>}
            {/* A board's issue link (checked by tltgPrinterWarnings), and the Updates screen for Valve Oil's update warnings. */}
            {w.url && (
              <>
                {' '}
                <a href={w.url} target="_blank" rel="noopener noreferrer" className="font-semibold whitespace-nowrap text-accent-ink underline">
                  Report this board
                </a>
              </>
            )}
            {w.id.startsWith('valve_oil_') && route !== 'updates' && (
              <>
                {' '}
                <a href={href('updates')} className="font-semibold whitespace-nowrap text-accent-ink underline">
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
    <div className="mx-auto mt-[8vh] grid w-full max-w-[560px] justify-items-center gap-3 p-6 text-center">
      <LogoMark height={84} />
      <div className="mt-2 text-[20px] font-bold">{title}</div>
      {detail && <div className="whitespace-pre-line text-ink-2">{detail}</div>}
      {children && <div className="mt-3 flex flex-wrap justify-center gap-2">{children}</div>}
    </div>
  )
}

function klippyTitle(klippy: string) {
  return klippy === 'startup' ? 'Klipper is starting…' : klippy === 'shutdown' ? 'Klipper stopped' : klippy === 'error' ? 'Klipper reported an error' : 'Klipper isn’t running'
}

/** Klipper's own reason, in plain sight, with the one button that usually gets it back. */
function KlippyNotReady() {
  const klippy = usePrinter((s) => s.klippy)
  const message = usePrinter((s) => s.klippyMessage)
  // Valve Oil stops and restarts Klipper itself while it updates; a restart from here could interrupt a board flash.
  const updating = useUpdateRunning()
  return (
    <div className="mx-auto mt-[4vh] grid w-full max-w-[760px] gap-4">
      <h1 className="m-0 text-[24px] font-bold">{klippyTitle(klippy)}</h1>
      {message && (
        <div className="rounded-2xl px-4 py-3.5" style={{ boxShadow: 'inset 0 0 0 1px var(--crimson)', background: 'var(--red-tint)' }}>
          <div className="text-[15px] font-semibold whitespace-pre-line text-bad [overflow-wrap:anywhere]">{message}</div>
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        {klippy !== 'startup' && !updating && (
          <>
            <button className="btn btn-primary btn-lg" onClick={() => attempt(() => client.call('printer.firmware_restart'))}>
              <RefreshCw size={16} /> Firmware restart
            </button>
            <button className="btn btn-lg" onClick={() => attempt(() => client.call('printer.restart'))}>
              Restart Klipper
            </button>
          </>
        )}
        <a className="btn btn-lg" href={href('files', 'config')}>
          Edit configuration
        </a>
        <a className="btn btn-lg" href={href('console')}>
          Console
        </a>
        <a className="btn btn-lg" href={href('system', 'logs')}>
          Logs
        </a>
      </div>
    </div>
  )
}

/** Above screens that work without Klipper: says it's stopped, with the restarts. */
function KlippyStrip() {
  const klippy = usePrinter((s) => s.klippy)
  const message = usePrinter((s) => s.klippyMessage)
  const updating = useUpdateRunning()
  return (
    <div className="border-b border-line bg-page px-3.5 py-2 md:px-[18px]">
      <div className="tone-bad flex flex-wrap items-center gap-2.5 rounded-xl px-3 py-2">
        <TriangleAlert size={16} className="flex-none" />
        <div className="min-w-0 flex-1 text-ink [overflow-wrap:anywhere]">
          <b>{klippyTitle(klippy)}</b> {message && <span className="text-ink-2">{message.split('\n')[0]}</span>}
        </div>
        {klippy !== 'startup' && !updating && (
          <>
            <button className="btn btn-sm" onClick={() => attempt(() => client.call('printer.restart'))}>
              Restart Klipper
            </button>
            <button className="btn btn-sm btn-primary" onClick={() => attempt(() => client.call('printer.firmware_restart'))}>
              Firmware restart
            </button>
          </>
        )}
      </div>
    </div>
  )
}

// Re-exported for screens that need to send G-code with console logging.
export { runGcode }

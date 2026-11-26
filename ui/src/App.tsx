import { lazy, Suspense, useRef, useState, type ReactNode } from 'react'
import {
  Cpu,
  Ellipsis,
  ExternalLink,
  Folder,
  Grid3x3,
  History,
  House,
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
import { client, usePrinter } from './state/printer'
import { useJob, useJobActive, jobProgress } from './state/job'
import { useBoxBusy, useBoxPresent } from './state/box'
import { useWarnings } from './state/warnings'
import { Toasts, attempt } from './components/toast'
import { UploadsPanel } from './components/Upload'
import { BedScrewsDialog, ManualProbeDialog } from './components/tune/HelperDialogs'
import { AskHost, Loading, confirm, useModalFocus } from './components/ui'
import { LogoMark } from './components/Logo'
import { NotificationsButton } from './components/Notifications'
import { HealthButton } from './components/Panels'
import { useUiSetting } from './components/settings/fluidd'
import { HomeScreen } from './screens/Home'
import { DONT_POWER_OFF, UpdatesScreen } from './screens/Updates'
import { useUpdateRunning, valveOil } from './state/valve-oil'
import { useUninstallShown } from './state/uninstall'
import { UninstallScreen } from './components/updates/Uninstall'
import { useStore } from './lib/store'
import { installStepText, installTitle } from './components/updates/Install'
import { fluiddUrl } from './lib/links'
import { useTheme } from './lib/theme'
import { t, type MessageKey } from './i18n'
import { confirmAndSaveConfig } from './components/save-config'

// Screens other than Home and Updates load when first opened, so the first page a tablet shows is small.
const FilesScreen = lazy(() => import('./screens/Files').then((m) => ({ default: m.FilesScreen })))
const ControlScreen = lazy(() => import('./screens/Control').then((m) => ({ default: m.ControlScreen })))
const ConsoleScreen = lazy(() => import('./screens/Console').then((m) => ({ default: m.ConsoleScreen })))
const TuneScreen = lazy(() => import('./screens/Tune').then((m) => ({ default: m.TuneScreen })))
const HistoryScreen = lazy(() => import('./screens/History').then((m) => ({ default: m.HistoryScreen })))
const SystemScreen = lazy(() => import('./screens/System').then((m) => ({ default: m.SystemScreen })))
const FilamentsScreen = lazy(() => import('./screens/Filaments').then((m) => ({ default: m.FilamentsScreen })))
const SettingsScreen = lazy(() => import('./screens/Settings').then((m) => ({ default: m.SettingsScreen })))

// Labels are message keys, looked up while rendering.
type NavItem = { route: Route; labelKey: MessageKey; icon: ReactNode; mobile?: boolean }
const I = 22
const NAV: NavItem[] = [
  { route: 'home', labelKey: 'app.nav.home', icon: <House size={I} />, mobile: true },
  { route: 'files', labelKey: 'app.nav.files', icon: <Folder size={I} />, mobile: true },
  { route: 'control', labelKey: 'app.nav.control', icon: <Move size={I} />, mobile: true },
  { route: 'console', labelKey: 'app.nav.console', icon: <SquareTerminal size={I} /> },
  { route: 'tune', labelKey: 'app.nav.tune', icon: <Grid3x3 size={I} /> },
  { route: 'history', labelKey: 'app.nav.history', icon: <History size={I} /> },
  { route: 'system', labelKey: 'app.nav.system', icon: <Cpu size={I} /> },
]
const NAV_END: NavItem[] = [
  { route: 'settings', labelKey: 'app.nav.settings', icon: <SlidersHorizontal size={I} /> },
  { route: 'updates', labelKey: 'app.nav.updates', icon: <RefreshCw size={I} /> },
]

const SCREENS: Record<Route, () => ReactNode> = {
  home: () => <HomeScreen />,
  files: () => <FilesScreen />,
  filaments: () => <FilamentsScreen />,
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
  // Klipper has answered on this page before, or on the last one in this browser (its saved state), with its objects
  // and the commands it knows, which decide some buttons (Cut, In and Out). Both stay through a restart.
  const hadKlipper = usePrinter((s) => s.objects.length > 0 && s.commands.length > 0)
  const connLost = usePrinter((s) => s.connLost)
  const shown: Route = route === 'filaments' && !boxPresent && klippy === 'ready' ? 'home' : route
  // Uninstalling removes Valve Oil UI itself, so nothing else on the page is used until it ends.
  const uninstalling = useUninstallShown()

  let body: ReactNode
  // Updates talks to Valve Oil's status service, not Moonraker, so it stays up while an update restarts Moonraker.
  if (shown === 'updates') body = <UpdatesScreen />
  // On a fresh page with the printer's last state saved, the screens draw from it while the connection opens.
  else if (conn !== 'open' && !(conn === 'connecting' && !connLost && hadKlipper)) body = <NotReady title={conn === 'connecting' ? t('app.conn.connecting') : t('app.conn.unreachable')} detail={t('app.conn.trying', { url: client.wsUrl })} />
  // On a fresh page, wait for Klipper's first answer, so the screen doesn't draw empty and then rearrange itself.
  else if (klippy === 'unknown' && !hadKlipper) body = <Loading className="py-16" size={24} />
  // Every other screen stays up while Klipper restarts or is stopped, as in Fluidd: it shows what Klipper last
  // reported, the camera keeps running, and the strip above says what Klipper is doing.
  else body = <Suspense fallback={<Loading className="py-16" size={24} />}>{SCREENS[shown]()}</Suspense>

  const cockpit = shown === 'home' && (conn === 'open' || (conn === 'connecting' && !connLost && hadKlipper)) && (klippy !== 'unknown' || hadKlipper)
  if (uninstalling) return <UninstallScreen />
  return (
    <div className="flex h-full">
      <aside className="rail hidden md:flex">
        <a href={href('home')} className="mb-2 grid place-items-center pt-1 pb-1" title="Valve Oil" aria-label={t('app.nav.home')}>
          <LogoMark height={40} />
        </a>
        {NAV.map((n) => (
          <RailItem key={n.route} item={n} on={shown === n.route} />
        ))}
        <div className="mt-auto" />
        {NAV_END.map((n) => (
          <RailItem key={n.route} item={n} on={shown === n.route} />
        ))}
        <a href={fluiddUrl()} className="rail-item" title={t('app.nav.open_fluidd')}>
          <ExternalLink size={18} />
          Fluidd
        </a>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <UpdateBanner />
        <Warnings />
        {klippy !== 'ready' && klippy !== 'unknown' && conn === 'open' && shown !== 'updates' && <KlippyStrip />}
        <div className="min-h-0 flex-1 overflow-auto">
          <div className={cockpit ? 'h-full p-3.5 pb-24 md:p-[18px] md:pb-[18px]' : `mx-auto grid grid-cols-1 content-start gap-3.5 p-3.5 pb-24 md:p-[18px] md:pb-[18px] ${shown === 'system' ? '' : 'max-w-[1500px]'}`}>{body}</div>
        </div>
        <MobileNav nav={NAV} route={shown} />
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
      {t(item.labelKey)}
      {item.route === 'updates' && <UpdatingBadge />}
    </a>
  )
}

function MobileNav({ nav, route }: { nav: NavItem[]; route: Route }) {
  const [more, setMore] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const main = nav.filter((n) => n.mobile)
  const rest = [...nav.filter((n) => !n.mobile), ...NAV_END]
  const inRest = rest.some((n) => n.route === route)
  const cls = (on: boolean) => `grid justify-items-center gap-1 px-2 text-[10.5px] font-semibold ${on ? 'text-accent-ink' : 'text-ink-3'}`
  // The More panel is modal for the keyboard, as a dialog is; focus goes back to More when it closes.
  useModalFocus(more, panelRef, () => setMore(false), triggerRef)
  return (
    <>
      {more && (
        <div className="fixed inset-0 z-40 md:hidden" onClick={() => setMore(false)}>
          <div
            ref={panelRef}
            role="dialog"
            aria-modal="true"
            aria-label={t('app.nav.more')}
            className="absolute inset-x-2 bottom-[72px] grid grid-cols-3 gap-1 rounded-2xl bg-surface p-2 sm:grid-cols-4"
            style={{ boxShadow: 'var(--shadow-overlay)' }}
            onClick={(e) => e.stopPropagation()}
          >
            {rest.map((n) => (
              <a key={n.route} href={href(n.route)} className={`rail-item ${route === n.route ? 'on' : ''}`} onClick={() => setMore(false)}>
                {n.icon}
                {t(n.labelKey)}
              </a>
            ))}
            <a href={fluiddUrl()} className="rail-item" onClick={() => setMore(false)}>
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
            {t(n.labelKey)}
          </a>
        ))}
        <button
          ref={triggerRef}
          className={`border-0 bg-transparent ${cls(inRest || more)}`}
          onClick={() => setMore(!more)}
          aria-expanded={more}
          aria-haspopup="dialog"
        >
          <Ellipsis size={I} />
          {t('app.nav.more')}
        </button>
      </nav>
    </>
  )
}

function UpdatingBadge() {
  return useUpdateRunning() ? <span className="badge" title={t('app.nav.updating')} /> : null
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
          <b>{install ? `${installTitle(install)}.` : t('app.update_banner.updating')}</b>{' '}
          {install && <span className="text-ink-2">{installStepText(install)}. </span>}
          <span className="text-ink-2">{DONT_POWER_OFF}</span>
        </div>
        <a href={href('updates')} className="flex-none font-semibold text-accent-ink">
          {t('app.details')}
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
  const connLost = usePrinter((s) => s.connLost)
  const idle = usePrinter((s) => s.status.idle_timeout?.state as string | undefined)
  // Reconnect attempts after a drop stay Offline until one succeeds; only the first connection is loading.
  if ((conn === 'connecting' && !connLost) || (conn === 'open' && klippy === 'unknown')) return <span className="pill">{t('common.loading')}</span>
  if (conn !== 'open') return <span className="pill tone-bad">{t('app.status.offline')}</span>
  if (klippy !== 'ready') return <span className="pill tone-bad">{klippyPill(klippy)}</span>
  if (job.state === 'printing') return <span className="pill tone-accent">{t('app.status.printing', { pct: Math.floor(jobProgress(job) * 100) })}</span>
  if (job.state === 'paused') return <span className="pill tone-warn">{t('app.status.paused')}</span>
  if (busy) return <span className="pill tone-warn">{t('app.status.box_busy')}</span>
  if (idle === 'Printing') return <span className="pill tone-accent">{t('app.status.busy')}</span>
  return null
}

/** Klipper's state in the top bar's pill. */
function klippyPill(klippy: string) {
  const known: Record<string, string> = { startup: t('app.status.klipper_startup'), error: t('app.status.klipper_error'), shutdown: t('app.status.klipper_shutdown'), disconnected: t('app.status.klipper_disconnected'), unknown: t('app.status.klipper_unknown') }
  return known[klippy] ?? t('app.status.klipper_state', { state: klippy })
}

/** Nozzle, bed, and chamber at a glance, amber while heating, as across the top of the touchscreen. */
function MiniTemps() {
  const temps = usePrinter((s) =>
    (
      [
        ['nozzle', s.status.extruder],
        ['bed', s.status.heater_bed],
        ['chamber', s.status['heater_generic chamber']],
      ] as const
    )
      .filter(([, o]) => o && typeof o.temperature === 'number')
      .map(([label, o]) => `${label}:${Math.round(o!.temperature)}:${o!.target > 0 ? 1 : 0}`)
      .join('|'),
  )
  if (!temps) return null
  return (
    <div className="hidden items-center gap-3.5 lg:flex">
      {temps.split('|').map((entry) => {
        const [id, v, hot] = entry.split(':')
        const label = t(`app.heaters.${id}` as MessageKey)
        return (
          <span key={id} className={`num flex items-baseline gap-1 text-[13px] font-semibold ${hot === '1' ? 'text-accent-ink' : 'text-ink-2'}`} title={label}>
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
  const printing = useJobActive()
  if (!pending) return null
  const sections = Object.keys(items ?? {})
  return (
    <button
      className="btn btn-sm btn-primary max-w-full whitespace-normal"
      disabled={printing}
      title={printing ? t('app.save_config.waits') : undefined}
      onClick={() =>
        confirmAndSaveConfig({
          title: t('app.save_config.confirm_title'),
          body: (
            <>
              {t('app.save_config.confirm_body')}
              {sections.length > 0 && <div className="mt-2">{t('app.save_config.changed', { sections: sections.join(', ') })}</div>}
            </>
          ),
          confirm: t('app.save_config.confirm'),
        })
      }
    >
      {t('app.save_config.button')}
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
    <div className="topbar relative flex flex-wrap items-center gap-x-2 border-b border-line px-3.5 py-2.5 pt-[max(10px,env(safe-area-inset-top))] lg:gap-x-3 md:px-[18px]" style={{ background: 'var(--page)' }}>
      <a href={href('home')} className="flex flex-none items-center gap-2.5 md:hidden" aria-label={t('app.nav.home')}>
        <LogoMark height={30} />
      </a>
      {/* i18n-ignore: the product's wordmark */}
      <span className="wordmark hidden md:inline">VALVE OIL</span>
      {name && (
        <>
          <span className="hidden text-ink-3 xl:inline">|</span>
          <span className="hidden min-w-0 truncate font-semibold text-ink-2 xl:inline">{name}</span>
        </>
      )}
      {/* Status text must not displace the controls: on a phone it wraps onto its own row below them. */}
      <div className="topbar-state order-last flex w-full min-w-0 flex-wrap items-center gap-2 empty:hidden max-md:[&:not(:empty)]:mt-2 md:order-none md:w-auto md:flex-initial [&_.pill]:max-w-full [&_.pill]:whitespace-normal [&_.pill]:[overflow-wrap:anywhere]">
        <StatusPill />
        <SaveConfigButton />
      </div>
      <div className="flex-1" />
      <MiniTemps />
      <HealthButton />
      <NotificationsButton />
      <button className="iconbtn" onClick={toggleTheme} aria-label={t('app.topbar.switch_theme')} title={t('app.topbar.switch_theme')}>
        {theme === 'dark' ? <Sun size={17} /> : <Moon size={17} />}
      </button>
      <button
        className="estop flex-none"
        disabled={conn !== 'open'}
        onClick={async () => {
          // Fluidd's "Confirm emergency stop" setting, shared with it.
          if (confirmEstop && !(await confirm({ title: t('app.topbar.estop_confirm_title'), body: t('app.topbar.estop_confirm_body'), confirm: t('app.topbar.estop'), danger: true }))) return
          attempt(() => client.call('printer.emergency_stop'))
        }}
        title={t('app.topbar.estop_title')}
      >
        <X size={14} /> <span className="hidden lg:inline">{t('app.topbar.estop')}</span>
        <span className="lg:hidden">{t('app.topbar.estop_short')}</span>
      </button>
    </div>
  )
}

function Warnings() {
  const route = useRoute()
  // Klipper's word that an update is running repeats the update banner, or on Updates its card.
  const running = useUpdateRunning()
  const warnings = useWarnings().filter((w) => !(running && w.id === 'valve_oil_update_running'))
  if (!warnings.length) return null
  return (
    <div className="grid gap-1.5 border-b border-line bg-page px-3.5 py-2 md:px-[18px]">
      {warnings.map((w, i) => (
        <div key={i} className={`flex items-start gap-2.5 rounded-xl px-3 py-2 ${w.level === 'error' ? 'tone-bad' : 'tone-warn'}`}>
          <TriangleAlert size={16} className="mt-0.5 flex-none" />
          <div className="min-w-0 text-ink [overflow-wrap:anywhere]">
            <b>{w.title}</b>
            {w.message && <span className="text-ink-2"> {w.message}</span>}
            {/* A board's issue link (checked by voPrinterWarnings). */}
            {w.url && more(w.url, t('app.warnings.support_issue'), true)}
            {/* Valve Oil's update warnings, and a board it can't support yet, whose Boards entry says what to report. */}
            {(w.id.startsWith('valve_oil_') || w.id.includes('_firmware_unsupported')) && route !== 'updates' && more(href('updates'), t('app.details'))}
            {/* QIDI's Klipper runs although Valve Oil's is chosen: the Klipper settings try again or go back. */}
            {w.id === 'vo_klipper_fallback' && route !== 'settings' && more(href('settings', 'klipper'), t('app.details'))}
          </div>
        </div>
      ))}
    </div>
  )
}

/** A link after a warning's text; an external one opens in a new tab. */
const more = (to: string, text: string, external?: boolean) => (
  <>
    {' '}
    <a href={to} target={external ? '_blank' : undefined} rel={external ? 'noopener noreferrer' : undefined} className="font-semibold whitespace-nowrap text-accent-ink underline">
      {text}
    </a>
  </>
)

function NotReady({ title, detail }: { title: string; detail: string }) {
  return (
    <div role="status" className="mx-auto mt-[8vh] grid w-full max-w-[560px] justify-items-center gap-3 p-6 text-center">
      <LogoMark height={84} />
      <div className="mt-2 text-[20px] font-bold">{title}</div>
      <div className="whitespace-pre-line text-ink-2">{detail}</div>
    </div>
  )
}

function klippyTitle(klippy: string) {
  return klippy === 'startup' ? t('app.klippy.starting') : klippy === 'shutdown' ? t('app.klippy.stopped') : klippy === 'error' ? t('app.klippy.error') : t('app.klippy.not_running')
}

/** Above every screen but Updates while Klipper isn't ready: what it's doing and why, with the restarts. */
function KlippyStrip() {
  const klippy = usePrinter((s) => s.klippy)
  const message = usePrinter((s) => s.klippyMessage)
  const updating = useUpdateRunning()
  const [all, setAll] = useState(false)
  // One line of Klipper's reason, so the strip keeps its height through a restart: Klipper's own start message is
  // three lines after a blank one. A longer reason, such as a configuration error, opens with Show all.
  const lines = message.split('\n').map((l) => l.trim()).filter(Boolean)
  return (
    <div className="border-b border-line bg-page px-3.5 py-2 md:px-[18px]">
      <div className="tone-bad flex flex-wrap items-center gap-2.5 rounded-xl px-3 py-2">
        <TriangleAlert size={16} className="flex-none" />
        {/* Two lines even without a reason, so the strip doesn't change height as a restart moves on. */}
        <div className="min-h-[calc(2lh+2px)] min-w-0 flex-1 text-ink [overflow-wrap:anywhere]">
          <b>{klippyTitle(klippy)}</b>
          {lines.length > 0 && (
            <div className={`mt-0.5 flex gap-2 text-ink-2 ${all ? 'items-end' : 'items-baseline'}`}>
              <div className={`min-w-0 ${all ? 'max-h-40 overflow-auto whitespace-pre-line' : 'truncate'}`}>{all ? lines.join('\n') : lines[0]}</div>
              {lines.length > 1 && (
                <button className="flex-none border-0 bg-transparent p-0 text-[12.5px] font-semibold whitespace-nowrap text-ink underline" onClick={() => setAll(!all)}>
                  {all ? t('app.klippy.message_less') : t('app.klippy.message_all')}
                </button>
              )}
            </div>
          )}
        </div>
        {klippy !== 'startup' && (
          <div className="flex flex-wrap gap-1.5">
            {/* Valve Oil stops and restarts Klipper itself while it updates; a restart from here could interrupt a board flash. */}
            {!updating && (
              <>
                <button className="btn btn-sm" onClick={() => attempt(() => client.call('printer.restart'))}>
                  {t('app.klippy.restart')}
                </button>
                <button className="btn btn-sm btn-primary" onClick={() => attempt(() => client.call('printer.firmware_restart'))}>
                  {t('app.klippy.firmware_restart')}
                </button>
              </>
            )}
            <a className="btn btn-sm" href={href('files', 'config')}>
              {t('app.klippy.edit_config')}
            </a>
            <a className="btn btn-sm" href={href('system', 'logs')}>
              {t('app.klippy.logs')}
            </a>
          </div>
        )}
      </div>
    </div>
  )
}

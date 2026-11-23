import { useEffect, type ReactNode } from 'react'
import { Camera, ChevronLeft, ChevronRight, Cpu, Film, Folder, Info, Layers, Move, Puzzle, RefreshCw, SlidersHorizontal, Thermometer } from 'lucide-react'
import { PageHeader, Spinner } from '../components/ui'
import { href, useSubPath } from '../lib/router'
import { useConnected, usePrinter } from '../state/printer'
import { useValveOil, useValveOilView, watchValveOil } from '../state/valve-oil'
import { t, type MessageKey } from '../i18n'
import { GeneralSettings } from '../components/settings/General'
import { ToolheadSettings } from '../components/settings/Toolhead'
import { CameraSettings } from '../components/settings/Cameras'
import { PresetSettings } from '../components/settings/Presets'
import { FilesSettings } from '../components/settings/FilesSettings'
import { GcodePreviewSettings } from '../components/settings/GcodePreviewSettings'
import { TimelapseSettingsSection } from '../components/settings/Timelapse'
import { AddonsSettings } from '../components/settings/Addons'
import { KlipperSettings } from '../components/settings/Klipper'
import { AboutSettings } from '../components/settings/About'

// Settings, one section per sub-route (#/settings/cameras). Wide screens show the list of sections beside the open one;
// phones show the list on #/settings and each section on its own page. The Valve Oil section of the Fluidd fork's
// settings is the Updates screen here, which the list links to.

// `key` names the section's label and description in the settings messages (settings.sections.<key>).
type Section = { id: string; key: string; icon: ReactNode; body: () => ReactNode; when?: (components: string[]) => boolean }

const I = 17
const SECTIONS: Section[] = [
  { id: 'general', key: 'general', icon: <SlidersHorizontal size={I} />, body: () => <GeneralSettings /> },
  { id: 'toolhead', key: 'toolhead', icon: <Move size={I} />, body: () => <ToolheadSettings /> },
  { id: 'cameras', key: 'cameras', icon: <Camera size={I} />, body: () => <CameraSettings /> },
  { id: 'presets', key: 'presets', icon: <Thermometer size={I} />, body: () => <PresetSettings /> },
  { id: 'files', key: 'files', icon: <Folder size={I} />, body: () => <FilesSettings /> },
  { id: 'gcode-preview', key: 'gcode_preview', icon: <Layers size={I} />, body: () => <GcodePreviewSettings /> },
  { id: 'timelapse', key: 'timelapse', icon: <Film size={I} />, body: () => <TimelapseSettingsSection />, when: (c) => c.includes('timelapse') },
  { id: 'addons', key: 'addons', icon: <Puzzle size={I} />, body: () => <AddonsSettings /> },
  { id: 'klipper', key: 'klipper', icon: <Cpu size={I} />, body: () => <KlipperSettings /> },
  { id: 'about', key: 'about', icon: <Info size={I} />, body: () => <AboutSettings /> },
]
const sectionLabel = (s: Section) => t(`settings.sections.${s.key}.label` as MessageKey)
const sectionSub = (s: Section) => t(`settings.sections.${s.key}.sub` as MessageKey)

export function SettingsScreen() {
  const [sub] = useSubPath()
  const components = usePrinter((s) => s.server?.components ?? [])
  const open = useConnected()
  const sections = SECTIONS.filter((s) => !s.when || s.when(components))
  const current = sections.find((s) => s.id === sub)

  return (
    <div className="grid content-start gap-3.5">
      <PageHeader
        title={
          current ? (
            <span className="flex items-center gap-1.5">
              <a href={href('settings')} className="iconbtn -ml-2 lg:hidden" aria-label={t('settings.screen.back')}>
                <ChevronLeft size={20} />
              </a>
              <span className="lg:hidden">{sectionLabel(current)}</span>
              <span className="hidden lg:inline">{t('settings.screen.title')}</span>
            </span>
          ) : (
            t('settings.screen.title')
          )
        }
      />
      <div className="grid items-start gap-3.5 lg:grid-cols-[280px_minmax(0,1fr)]">
        <nav className={`${current ? 'hidden lg:grid' : 'grid'} gap-1.5 lg:sticky lg:top-0`} aria-label={t('settings.screen.sections')}>
          {sections.map((s) => (
            <NavRow key={s.id} href={href('settings', s.id)} icon={s.icon} title={sectionLabel(s)} sub={sectionSub(s)} on={(current ?? (sub ? undefined : sections[0]))?.id === s.id} />
          ))}
          <UpdatesLink />
        </nav>
        <div className={`${current ? 'grid' : 'hidden lg:grid'} min-w-0 content-start gap-3.5`}>
          {/* The database is read on mount, so the sections wait for Moonraker to answer. */}
          {open ? (
            (current ?? sections[0]).body()
          ) : (
            <div className="card flex items-center gap-2.5 text-ink-2">
              <Spinner size={14} /> {t('settings.screen.waiting')}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/** A link in the list of sections; `on` is given for a section, which is outlined when open and has an arrow only on phones. */
function NavRow({ href, icon, title, sub, subClass = 'text-ink-3', on }: { href: string; icon: ReactNode; title: string; sub: string; subClass?: string; on?: boolean }) {
  return (
    <a href={href} className={`link-row py-2.5! ${on ? 'lg:shadow-[0_0_0_1px_var(--accent)]!' : ''}`} aria-current={on ? 'page' : undefined}>
      <span className={`ic h-[34px]! w-[34px]! ${on ? 'lg:bg-accent-tint!' : ''}`}>{icon}</span>
      <span className="min-w-0 flex-1">
        <span className={`block font-semibold ${on ? 'lg:text-accent-ink' : ''}`}>{title}</span>
        <span className={`block truncate text-[12px] ${subClass}`}>{sub}</span>
      </span>
      <ChevronRight size={16} className={`flex-none text-ink-3 ${on == null ? '' : 'lg:hidden'}`} />
    </a>
  )
}

/** Valve Oil's own settings live on the Updates screen, as the fork's Valve Oil section does in Fluidd. */
function UpdatesLink() {
  useEffect(watchValveOil, [])
  const s = useValveOil()
  const view = useValveOilView()
  const auto = s.status?.auto_update.enabled
  const release = view?.release
  const waiting = !!view && view.waiting.length > 0
  const summary = !view
    ? t('settings.screen.valve_oil_sub')
    : release
      ? auto == null
        ? t('settings.screen.summary_release', { release })
        : t(auto ? 'settings.screen.summary_release_auto_on' : 'settings.screen.summary_release_auto_off', { release })
      : auto == null
        ? ''
        : t(auto ? 'settings.screen.summary_auto_on' : 'settings.screen.summary_auto_off')
  return <NavRow href={href('updates')} icon={<RefreshCw size={I} />} title="Valve Oil" sub={waiting ? t('settings.screen.update_waiting') : summary} subClass={waiting ? 'font-semibold text-accent-ink' : undefined} />
}

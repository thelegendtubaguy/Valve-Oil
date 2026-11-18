import { useEffect, type ReactNode } from 'react'
import { Camera, ChevronLeft, ChevronRight, Film, Folder, Info, Layers, Move, Puzzle, RefreshCw, SlidersHorizontal, Thermometer } from 'lucide-react'
import { PageHeader, Spinner } from '../components/ui'
import { href, useSubPath } from '../lib/router'
import { usePrinter } from '../state/printer'
import { useValveOil, useValveOilView, watchValveOil } from '../state/valve-oil'
import { GeneralSettings } from '../components/settings/General'
import { ToolheadSettings } from '../components/settings/Toolhead'
import { CameraSettings } from '../components/settings/Cameras'
import { PresetSettings } from '../components/settings/Presets'
import { FilesSettings } from '../components/settings/FilesSettings'
import { GcodePreviewSettings } from '../components/settings/GcodePreviewSettings'
import { TimelapseSettingsSection } from '../components/settings/Timelapse'
import { AddonsSettings } from '../components/settings/Addons'
import { AboutSettings } from '../components/settings/About'

// Settings, one section per sub-route (#/settings/cameras). Wide screens show the list of sections beside the open one;
// phones show the list on #/settings and each section on its own page. The Valve Oil section of the Fluidd fork's
// settings is the Updates screen here, which the list links to.

type Section = { id: string; label: string; sub: string; icon: ReactNode; body: () => ReactNode; when?: (components: string[]) => boolean }

const I = 17
const SECTIONS: Section[] = [
  { id: 'general', label: 'General', sub: 'Printer name, theme, warnings', icon: <SlidersHorizontal size={I} />, body: () => <GeneralSettings /> },
  { id: 'toolhead', label: 'Toolhead', sub: 'Jog distances, speeds, axes', icon: <Move size={I} />, body: () => <ToolheadSettings /> },
  { id: 'cameras', label: 'Cameras', sub: 'Add, edit, and remove cameras', icon: <Camera size={I} />, body: () => <CameraSettings /> },
  { id: 'presets', label: 'Temperature presets', sub: 'Targets for each material', icon: <Thermometer size={I} />, body: () => <PresetSettings /> },
  { id: 'files', label: 'Files and editor', sub: 'Sorting, drag-and-drop, editor', icon: <Folder size={I} />, body: () => <FilesSettings /> },
  { id: 'gcode-preview', label: 'G-code preview', sub: 'Line widths, flip, auto load', icon: <Layers size={I} />, body: () => <GcodePreviewSettings /> },
  { id: 'timelapse', label: 'Timelapse', sub: 'Camera, parking, render', icon: <Film size={I} />, body: () => <TimelapseSettingsSection />, when: (c) => c.includes('timelapse') },
  { id: 'addons', label: 'Add-ons', sub: 'Remote access and networking', icon: <Puzzle size={I} />, body: () => <AddonsSettings /> },
  { id: 'about', label: 'About', sub: 'Versions', icon: <Info size={I} />, body: () => <AboutSettings /> },
]

export function SettingsScreen() {
  const [sub] = useSubPath()
  const components = usePrinter((s) => s.server?.components ?? [])
  const open = usePrinter((s) => s.conn === 'open')
  const sections = SECTIONS.filter((s) => !s.when || s.when(components))
  const current = sections.find((s) => s.id === sub)

  return (
    <div className="grid content-start gap-3.5">
      <PageHeader
        title={
          current ? (
            <span className="flex items-center gap-1.5">
              <a href={href('settings')} className="iconbtn -ml-2 lg:hidden" aria-label="Back to Settings">
                <ChevronLeft size={20} />
              </a>
              <span className="lg:hidden">{current.label}</span>
              <span className="hidden lg:inline">Settings</span>
            </span>
          ) : (
            'Settings'
          )
        }
      />
      <div className="grid items-start gap-3.5 lg:grid-cols-[280px_minmax(0,1fr)]">
        <nav className={`${current ? 'hidden lg:grid' : 'grid'} gap-1.5 lg:sticky lg:top-0`} aria-label="Settings sections">
          {sections.map((s) => (
            <SectionLink key={s.id} section={s} on={(current ?? (sub ? undefined : sections[0]))?.id === s.id} />
          ))}
          <UpdatesLink />
        </nav>
        <div className={`${current ? 'grid' : 'hidden lg:grid'} min-w-0 content-start gap-3.5`}>
          {/* The database is read on mount, so the sections wait for Moonraker to answer. */}
          {open ? (
            (current ?? sections[0]).body()
          ) : (
            <div className="card flex items-center gap-2.5 text-ink-2">
              <Spinner size={14} /> Waiting for Moonraker…
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function SectionLink({ section, on }: { section: Section; on: boolean }) {
  return (
    <a href={href('settings', section.id)} className={`link-row py-2.5! ${on ? 'lg:shadow-[0_0_0_1px_var(--accent)]!' : ''}`} aria-current={on ? 'page' : undefined}>
      <span className={`ic h-[34px]! w-[34px]! ${on ? 'lg:bg-accent-tint!' : ''}`}>{section.icon}</span>
      <span className="min-w-0 flex-1">
        <span className={`block font-semibold ${on ? 'lg:text-accent-ink' : ''}`}>{section.label}</span>
        <span className="block truncate text-[12px] text-ink-3">{section.sub}</span>
      </span>
      <ChevronRight size={16} className="flex-none text-ink-3 lg:hidden" />
    </a>
  )
}

/** Valve Oil's own settings live on the Updates screen, as the fork's Valve Oil section does in Fluidd. */
function UpdatesLink() {
  useEffect(watchValveOil, [])
  const s = useValveOil()
  const view = useValveOilView()
  const summary = view
    ? [view.release ? `Valve Oil ${view.release}` : null, s.status?.auto_update.enabled == null ? null : `automatic updates ${s.status.auto_update.enabled ? 'on' : 'off'}`].filter(Boolean).join(', ')
    : 'Updates, automatic updates, LAN only mode'
  return (
    <a href={href('updates')} className="link-row py-2.5!">
      <span className="ic h-[34px]! w-[34px]!">
        <RefreshCw size={I} />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block font-semibold">Valve Oil</span>
        {view && view.waiting.length > 0 ? <span className="block truncate text-[12px] font-semibold text-accent-ink">Update waiting</span> : <span className="block truncate text-[12px] text-ink-3">{summary}</span>}
      </span>
      <ChevronRight size={16} className="flex-none text-ink-3" />
    </a>
  )
}

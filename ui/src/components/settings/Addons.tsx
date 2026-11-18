import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ChevronDown, ExternalLink, Puzzle } from 'lucide-react'
import { Card, confirm, Spinner } from '../ui'
import { tltgAddonActions, type TltgAddon, type TltgAddonAction, type TltgAddonJob } from '../../qidi/tltg-addons'
import { actionsDisabled, dismissJob, hasStatusPanel, installBlocked, jobFor, runAddonAction, useAddons, watchAddons } from '../../state/addons'
import { Notice } from './common'
import { ObicoPanel, OctoEverywherePanel, TailscalePanel, ZerotierPanel } from './AddonPanels'

// The Fluidd fork's Add-ons section (src/components/settings/addons/) and its wording (app.tltg_addons in
// src/locales/en.yaml there); keep the two in step.

const STATE: Record<TltgAddon['state'], { label: string; tone: string }> = {
  installed: { label: 'Installed', tone: 'tone-good' },
  update_available: { label: 'Update available', tone: 'tone-accent' },
  foreign: { label: 'Installed outside Valve Oil', tone: 'tone-warn' },
  not_installed: { label: 'Not installed', tone: 'tone-muted' },
}
const BUTTON: Record<TltgAddonAction, string> = { install: 'Install', update: 'Update', remove: 'Remove', relink: 'Restart linking' }
const JOB: Record<TltgAddonAction, Record<TltgAddonJob['state'], (name: string) => string>> = {
  install: { running: (n) => `Installing ${n}…`, succeeded: (n) => `${n} installed`, failed: (n) => `Installing ${n} failed` },
  update: { running: (n) => `Updating ${n}…`, succeeded: (n) => `${n} updated`, failed: (n) => `Updating ${n} failed` },
  remove: { running: (n) => `Removing ${n}…`, succeeded: (n) => `${n} removed`, failed: (n) => `Removing ${n} failed` },
  relink: { running: (n) => `Restarting ${n} for linking…`, succeeded: (n) => `${n} is ready to link`, failed: (n) => `Restarting ${n} for linking failed` },
}

function versionText(addon: TltgAddon) {
  const { installed_version: installed, available_version: available } = addon
  if (addon.state === 'update_available' && installed && available) return `Version ${installed}, ${available} available`
  if (addon.state !== 'not_installed' && installed) return `Version ${installed}`
  return available ? `Version ${available}` : ''
}

export function AddonsSettings() {
  useEffect(watchAddons, [])
  const s = useAddons()
  const addons = s.list?.addons ?? []

  if (!s.list || addons.length === 0)
    return (
      <Card title="Add-ons" icon={<Puzzle size={14} />}>
        <div className="flex items-center gap-2.5 text-ink-2">
          {!s.list && !s.unavailable && <Spinner size={14} />}
          {!s.list ? (s.unavailable ? "Add-ons aren't available on this printer." : 'Loading add-ons…') : 'No add-ons are offered for this printer.'}
        </div>
      </Card>
    )

  return (
    <>
      {addons.map((addon) => (
        <AddonCard key={addon.id} addon={addon} />
      ))}
    </>
  )
}

function AddonCard({ addon }: { addon: TltgAddon }) {
  const s = useAddons()
  const disabled = actionsDisabled(s)
  const actions = tltgAddonActions(addon)
  const blocked = installBlocked(addon)
  const job = jobFor(s, addon)
  const error = s.actionErrors[addon.id]
  const version = versionText(addon)
  // A running job explains itself; the idle hint is only for a printer that's printing.
  const printerBusyHint = s.list != null && s.list.printer_busy && !s.list.busy
  const pending = s.pendingAction?.addon === addon.id

  const run = async (action: TltgAddonAction | null, body?: Record<string, unknown>) => {
    if (!action || actionsDisabled(s) || (action === 'install' && blocked)) return
    if (action === 'remove' && !(await confirm({ title: `Remove ${addon.name}`, body: `Remove ${addon.name} from the printer?`, confirm: 'Remove', danger: true }))) return
    runAddonAction(addon, action, body)
  }

  return (
    <section className="card">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
        <div className="min-w-[min(260px,100%)] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="m-0 text-[15px] font-bold">{addon.name}</h3>
            {addon.homepage && (
              <a href={addon.homepage} target="_blank" rel="noopener noreferrer" className="text-ink-3 hover:text-accent-ink" aria-label={`${addon.name} website`} title={addon.homepage}>
                <ExternalLink size={14} />
              </a>
            )}
            <span className={`pill ${STATE[addon.state].tone} py-0.5!`}>{STATE[addon.state].label}</span>
          </div>
          {addon.description && <div className="mt-1 text-ink-2">{addon.description}</div>}
          {version && <div className="mt-0.5 text-[12.5px] text-ink-3">{version}</div>}
        </div>
        <div className="ml-auto grid max-w-full justify-items-end gap-1">
          <div className="flex flex-wrap justify-end gap-2">
            {actions.remove && (
              <button className="btn btn-sm btn-danger" disabled={disabled} onClick={() => run('remove')}>
                Remove
              </button>
            )}
            {actions.primary && (
              <button className={`btn btn-sm ${actions.primary === 'remove' ? 'btn-danger' : 'btn-primary'}`} disabled={disabled || blocked} onClick={() => run(actions.primary)}>
                {pending && <Spinner size={12} />}
                {BUTTON[actions.primary]}
              </button>
            )}
          </div>
          {blocked ? (
            <div className="max-w-[300px] text-right text-[12px] text-ink-3">Remove {addon.blocked_by.join(', ')} first. Only one remote access add-on can be installed.</div>
          ) : (
            actions.primary && printerBusyHint && <div className="text-right text-[12px] text-ink-3">Available when the printer isn't printing or calibrating</div>
          )}
        </div>
      </div>

      {(addon.state === 'foreign' || error || job || hasStatusPanel(addon)) && (
        <div className="mt-3 grid gap-2">
          {addon.state === 'foreign' && <Notice tone="info">{addon.name} was installed outside Valve Oil, so it can't be updated or removed here.</Notice>}
          {error && <Notice tone="bad">{error}</Notice>}
          {job && <AddonJob job={job} name={addon.name} />}
          {hasStatusPanel(addon) && addon.id === 'octoeverywhere' && <OctoEverywherePanel addon={addon} />}
          {hasStatusPanel(addon) && addon.id === 'obico' && <ObicoPanel addon={addon} disabled={disabled} onAction={(a) => run(a.action, a.body)} />}
          {hasStatusPanel(addon) && addon.id === 'tailscale' && <TailscalePanel addon={addon} />}
          {hasStatusPanel(addon) && addon.id === 'zerotier' && <ZerotierPanel addon={addon} />}
        </div>
      )}
    </section>
  )
}

function AddonJob({ job, name }: { job: TltgAddonJob; name: string }) {
  // null follows the job: the log opens by itself when the job fails.
  const [showLog, setShowLog] = useState<boolean | null>(null)
  useEffect(() => setShowLog(null), [job.id])
  const logVisible = showLog ?? job.state === 'failed'
  return (
    <div className="grid gap-1.5">
      {job.state === 'running' ? (
        <div className="grid gap-1.5 rounded-lg bg-inset px-3 py-2.5 shadow-[inset_0_0_0_1px_var(--line)]">
          <div className="font-medium">{JOB[job.action].running(name)}</div>
          <div className="bar live">
            <i style={{ width: '100%' }} />
          </div>
          {job.message && <div className="text-[12.5px] text-ink-2 [overflow-wrap:anywhere]">{job.message}</div>}
        </div>
      ) : (
        <Notice tone={job.state === 'failed' ? 'bad' : 'good'} onClose={() => dismissJob(job.id)}>
          <div className="font-medium">{JOB[job.action][job.state](name)}</div>
          {job.state === 'failed' && job.message && <div className="text-ink-2">{job.message}</div>}
        </Notice>
      )}
      {job.log.length > 0 && (
        <>
          <button className="btn btn-sm btn-ghost justify-self-start px-1.5!" onClick={() => setShowLog(!logVisible)}>
            <ChevronDown size={14} className={logVisible ? 'rotate-180' : ''} />
            {logVisible ? 'Hide log' : 'Show log'}
          </button>
          {logVisible && <Log lines={job.log} />}
        </>
      )}
    </div>
  )
}

export function Log({ lines }: { lines: string[] }) {
  const ref = useRef<HTMLPreElement>(null)
  const following = useRef(true)
  // Follow the newest line unless the reader has scrolled back.
  useLayoutEffect(() => {
    const el = ref.current
    if (el && following.current) el.scrollTop = el.scrollHeight
  }, [lines])
  return (
    <pre
      ref={ref}
      onScroll={(e) => {
        const el = e.currentTarget
        following.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 8
      }}
      className="m-0 max-h-[240px] overflow-auto rounded-lg bg-inset p-2.5 font-mono text-[12px] whitespace-pre-wrap text-ink-2 shadow-[inset_0_0_0_1px_var(--line)] [overflow-wrap:anywhere]"
    >
      {lines.join('\n')}
    </pre>
  )
}

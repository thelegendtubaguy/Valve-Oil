import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { ChevronDown, ExternalLink, Puzzle } from 'lucide-react'
import { confirm, Spinner, Notice } from '../ui'
import { voAddonActions, type VoAddon, type VoAddonAction, type VoAddonJob } from '../../qidi/vo-addons'
import { actionsDisabled, dismissJob, hasStatusPanel, installBlocked, jobFor, runAddonAction, useAddons, watchAddons } from '../../state/addons'
import { ObicoPanel, OctoEverywherePanel, TailscalePanel, ZerotierPanel } from './AddonPanels'
import { WaitingCard } from './common'
import { t, type MessageKey } from '../../i18n'

// The Fluidd fork's Add-ons section (src/components/settings/addons/) and its wording (app.vo_addons in
// src/locales/en.yaml there); keep the two in step.

const TONE: Record<VoAddon['state'], string> = { installed: 'tone-good', update_available: 'tone-accent', foreign: 'tone-warn', not_installed: 'tone-muted' }
const stateLabel = (state: VoAddon['state']) => t(`settings.addons.state.${state}` as MessageKey)
const buttonLabel = (action: VoAddonAction) => t(`settings.addons.button.${action}` as MessageKey)
const jobText = (action: VoAddonAction, state: VoAddonJob['state'], name: string) => t(`settings.addons.job.${action}.${state}` as MessageKey, { name })

function versionText(addon: VoAddon) {
  const { installed_version: installed, available_version: available } = addon
  if (addon.state === 'update_available' && installed && available) return t('settings.addons.version_update', { installed, available })
  if (addon.state !== 'not_installed' && installed) return t('settings.addons.version', { version: installed })
  return available ? t('settings.addons.version', { version: available }) : ''
}

export function AddonsSettings() {
  useEffect(watchAddons, [])
  const s = useAddons()
  const addons = s.list?.addons ?? []

  if (!s.list || addons.length === 0)
    return (
      <WaitingCard title={t('settings.addons.title')} icon={<Puzzle size={14} />} waiting={!s.list && !s.unavailable}>
        {!s.list ? (s.unavailable ? t('settings.addons.unavailable') : t('settings.addons.loading')) : t('settings.addons.none')}
      </WaitingCard>
    )

  return (
    <>
      {addons.map((addon) => (
        <AddonCard key={addon.id} addon={addon} />
      ))}
    </>
  )
}

function AddonCard({ addon }: { addon: VoAddon }) {
  const s = useAddons()
  const disabled = actionsDisabled(s)
  const actions = voAddonActions(addon)
  const blocked = installBlocked(addon)
  const job = jobFor(s, addon)
  const error = s.actionErrors[addon.id]
  const version = versionText(addon)
  // A running job explains itself; the idle hint is only for a printer that's printing.
  const printerBusyHint = s.list != null && s.list.printer_busy && !s.list.busy
  const pending = s.pendingAction?.addon === addon.id

  const run = async (action: VoAddonAction | null, body?: Record<string, unknown>) => {
    if (!action || actionsDisabled(s) || (action === 'install' && blocked)) return
    if (action === 'remove' && !(await confirm({ title: t('settings.common.remove_named', { name: addon.name }), body: t('settings.addons.remove_confirm', { name: addon.name }), confirm: t('common.remove'), danger: true }))) return
    runAddonAction(addon, action, body)
  }

  return (
    <section className="card">
      <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
        <div className="min-w-[min(260px,100%)] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="m-0 text-[15px] font-bold">{addon.name}</h3>
            {addon.homepage && (
              <a href={addon.homepage} target="_blank" rel="noopener noreferrer" className="text-ink-3 hover:text-accent-ink" aria-label={t('settings.addons.website', { name: addon.name })} title={addon.homepage}>
                <ExternalLink size={14} />
              </a>
            )}
            <span className={`pill ${TONE[addon.state]} py-0.5!`}>{stateLabel(addon.state)}</span>
          </div>
          {addon.description && <div className="mt-1 text-ink-2">{addon.description}</div>}
          {version && <div className="mt-0.5 text-[12.5px] text-ink-3">{version}</div>}
        </div>
        <div className="ml-auto grid max-w-full justify-items-end gap-1">
          <div className="flex flex-wrap justify-end gap-2">
            {actions.remove && (
              <button className="btn btn-sm btn-danger" disabled={disabled} onClick={() => run('remove')}>
                {t('common.remove')}
              </button>
            )}
            {actions.primary && (
              <button className={`btn btn-sm ${actions.primary === 'remove' ? 'btn-danger' : 'btn-primary'}`} disabled={disabled || blocked} onClick={() => run(actions.primary)}>
                {pending && <Spinner size={12} />}
                {buttonLabel(actions.primary)}
              </button>
            )}
          </div>
          {blocked ? (
            <div className="max-w-[300px] text-right text-[12px] text-ink-3">{t('settings.addons.blocked', { names: addon.blocked_by.join(', ') })}</div>
          ) : (
            actions.primary && printerBusyHint && <div className="text-right text-[12px] text-ink-3">{t('settings.addons.printer_busy')}</div>
          )}
        </div>
      </div>

      {(addon.state === 'foreign' || error || job || hasStatusPanel(addon)) && (
        <div className="mt-3 grid gap-2">
          {addon.state === 'foreign' && <Notice tone="info">{t('settings.addons.foreign_notice', { name: addon.name })}</Notice>}
          {error && <Notice tone="bad">{error}</Notice>}
          {job && (
            <JobView id={job.id} failed={job.state === 'failed'} log={job.log} show={t('settings.addons.show_log')} hide={t('settings.addons.hide_log')}>
              {job.state === 'running' ? (
                <RunningBox title={jobText(job.action, 'running', addon.name)}>{job.message && <div className="text-[12.5px] text-ink-2 [overflow-wrap:anywhere]">{job.message}</div>}</RunningBox>
              ) : (
                <Notice tone={job.state === 'failed' ? 'bad' : 'good'} onClose={() => dismissJob(job.id)}>
                  <div className="font-medium">{jobText(job.action, job.state, addon.name)}</div>
                  {job.state === 'failed' && job.message && <div className="text-ink-2">{job.message}</div>}
                </Notice>
              )}
            </JobView>
          )}
          {hasStatusPanel(addon) && addon.id === 'octoeverywhere' && <OctoEverywherePanel addon={addon} />}
          {hasStatusPanel(addon) && addon.id === 'obico' && <ObicoPanel addon={addon} disabled={disabled} onAction={(a) => run(a.action, a.body)} />}
          {hasStatusPanel(addon) && addon.id === 'tailscale' && <TailscalePanel addon={addon} />}
          {hasStatusPanel(addon) && addon.id === 'zerotier' && <ZerotierPanel addon={addon} />}
        </div>
      )}
    </section>
  )
}

/** A job's progress or result, then its log behind a button; the log opens by itself when the job fails. */
export function JobView({ id, failed, log, show, hide, children }: { id: string; failed: boolean; log: string[]; show: string; hide: string; children: ReactNode }) {
  // null follows the job: the log opens by itself when the job fails.
  const [showLog, setShowLog] = useState<boolean | null>(null)
  useEffect(() => setShowLog(null), [id])
  const logVisible = showLog ?? failed
  return (
    <div className="grid gap-1.5">
      {children}
      {log.length > 0 && (
        <>
          <button className="btn btn-sm btn-ghost justify-self-start px-1.5!" onClick={() => setShowLog(!logVisible)}>
            <ChevronDown size={14} className={logVisible ? 'rotate-180' : ''} />
            {logVisible ? hide : show}
          </button>
          {logVisible && <Log lines={log} />}
        </>
      )}
    </div>
  )
}

/** A running job: what it's doing over a moving bar, then any more it says. */
export function RunningBox({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <div className="grid gap-1.5 rounded-lg bg-inset px-3 py-2.5 shadow-[inset_0_0_0_1px_var(--line)]">
      <div className="font-medium">{title}</div>
      <div className="bar live">
        <i style={{ width: '100%' }} />
      </div>
      {children}
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

import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ExternalLink } from 'lucide-react'
import { CancelButton, confirm, Dialog, Spinner, Notice } from '../ui'
import {
  parseVoAddonLinkCode,
  parseVoTailscaleStatus,
  parseVoZerotierStatus,
  voServerUrlValid,
  voZerotierNetworkIdValid,
  VO_OBICO_CLOUD,
  type VoAddon,
  type VoAddonLinkCode,
  type VoAddonPanelAction,
  type VoTailscaleStatus,
  type VoZerotierNetworkState,
  type VoZerotierStatus,
} from '../../qidi/vo-addons'
import { addonsRequest } from '../../state/addons'
import { errorText } from '../../lib/browser'
import { fmtDateTime, fmtTime, KV } from './common'
import { t, type MessageKey } from '../../i18n'
import { tr } from '../../i18n/rich'

// The status panels of the Fluidd fork's add-ons (VoOctoEverywhereStatus, VoObicoStatus, VoTailscaleStatus,
// VoZerotierStatus), with their wording.

// systemd's states for the add-on's service, with their labels in settings.addons.panel.service_state.
const SERVICE: Record<string, string> = { active: 'text-good', activating: 'text-info', failed: 'text-bad', inactive: 'text-warn', unknown: 'text-warn' }

// Obico's name for its hosted service.
const obicoCloud = 'Obico Cloud' // i18n-ignore

const isIpv4 = (address: string) => /^\d{1,3}(?:\.\d{1,3}){3}$/.test(address)
const unavailableText = (addon: VoAddon) => (typeof addon.status?.unavailable === 'string' ? addon.status.unavailable : '')
const path = (addon: VoAddon, action: string) => `/addons/${encodeURIComponent(addon.id)}/${action}`

/** The add-on's service state, then the panel's rows, or why the add-on can't say more. */
function Panel({ addon, unavailable, children }: { addon: VoAddon; unavailable?: string; children?: ReactNode }) {
  const state = addon.service?.active ?? 'unknown'
  const service = Object.hasOwn(SERVICE, state) ? { label: t(`settings.addons.panel.service_state.${state}` as MessageKey), cls: SERVICE[state] } : { label: state, cls: 'text-warn' }
  return (
    <div className="grid min-w-0 grid-cols-1 gap-0.5 rounded-[10px] bg-inset px-3 py-2 shadow-[inset_0_0_0_1px_var(--line)]">
      <KV k={t('settings.addons.panel.service')}>
        <span className={`font-semibold ${service.cls}`}>{service.label}</span>
      </KV>
      {unavailable ? <div className="mt-1 text-ink-2">{unavailable}</div> : children}
    </div>
  )
}

function Actions({ children }: { children: ReactNode }) {
  return <div className="mt-1 flex flex-wrap justify-end gap-2">{children}</div>
}

function Ext({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex min-w-0 max-w-full flex-wrap items-center justify-center gap-1 break-all font-semibold text-accent-ink hover:underline">
      {children} <ExternalLink size={12} />
    </a>
  )
}

function Addresses({ list }: { list: string[] }) {
  return (
    <span className="grid justify-items-end">
      {list.map((a) =>
        isIpv4(a) ? (
          <a key={a} href={`http://${a}/`} target="_blank" rel="noopener noreferrer" className="font-semibold text-accent-ink hover:underline">
            {a}
          </a>
        ) : (
          <span key={a}>{a}</span>
        ),
      )}
    </span>
  )
}

/** Whether the add-on is linked to its account, and with a link there when it is. */
function Account({ linked, children }: { linked: boolean; children: ReactNode }) {
  return (
    <KV k={t('settings.addons.panel.account')}>
      {linked ? (
        <span className="inline-flex items-center gap-2">
          <span className="pill tone-good py-0.5!">{t('settings.addons.panel.linked')}</span>
          {children}
        </span>
      ) : (
        <span className="font-semibold text-warn">{t('settings.addons.panel.not_linked')}</span>
      )}
    </KV>
  )
}

const Code = ({ children }: { children: ReactNode }) => <div className="code my-1 text-center text-[28px] font-semibold tracking-[0.2em]">{children}</div>

// ---- OctoEverywhere ----
export function OctoEverywherePanel({ addon }: { addon: VoAddon }) {
  const status = addon.status
  const unavailable = unavailableText(addon)
  const connected = status?.connected === true
  const linked = status?.linked === true
  const linkUrl = typeof status?.link_url === 'string' && /^https:\/\//.test(status.link_url) ? status.link_url : ''
  const [code, setCode] = useState<{ link: VoAddonLinkCode; expiresAt: number } | null>(null)
  const [loading, setLoading] = useState(false)
  const [codeError, setCodeError] = useState('')

  // A code clears when it runs out, and once the printer is linked.
  useEffect(() => {
    if (linked) setCode(null)
  }, [linked])
  useEffect(() => {
    if (!code?.expiresAt) return
    const t = setTimeout(() => setCode(null), code.expiresAt - Date.now())
    return () => clearTimeout(t)
  }, [code])

  const showLinkCode = async () => {
    setLoading(true)
    setCodeError('')
    try {
      const link = parseVoAddonLinkCode(await addonsRequest(path(addon, 'link-code'), 'POST'))
      setCode(null)
      if (!link) return setCodeError(t('settings.addons.panel.octo.no_code'))
      setCode({ link, expiresAt: link.expires_in > 0 ? Date.now() + link.expires_in * 1000 : 0 })
    } catch (e) {
      setCode(null)
      setCodeError(errorText(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Panel addon={addon} unavailable={unavailable}>
      {status && (
        <>
          <KV k="OctoEverywhere">
            <span className={`font-semibold ${connected ? 'text-good' : 'text-warn'}`}>{connected ? t('settings.addons.panel.octo.connected') : t('settings.addons.panel.octo.not_connected')}</span>
          </KV>
          <Account linked={linked}>
            <Ext href="https://octoeverywhere.com/dashboard">{t('settings.addons.panel.octo.dashboard')}</Ext>
          </Account>
          {!linked && (
            <>
              <Actions>
                {linkUrl && (
                  <a className="btn btn-sm btn-primary" href={linkUrl} target="_blank" rel="noopener noreferrer">
                    {t('settings.addons.panel.octo.link_account')} <ExternalLink size={13} />
                  </a>
                )}
                <button className="btn btn-sm" disabled={loading} onClick={showLinkCode}>
                  {loading && <Spinner size={12} />}
                  {t('settings.addons.panel.octo.show_code')}
                </button>
              </Actions>
              {codeError && <Notice tone="bad" className="mt-2">{codeError}</Notice>}
              {code && (
                <div className="mt-2 text-center">
                  <Code>{code.link.code}</Code>
                  {code.link.url && (
                    <div>
                      {tr('settings.addons.panel.octo.enter_code', { url: <Ext href={code.link.url}>{code.link.url}</Ext> })}
                    </div>
                  )}
                  {code.expiresAt > 0 && <div className="text-[12px] text-ink-3">{t('settings.addons.panel.octo.expires', { time: fmtTime(code.expiresAt) })}</div>}
                </div>
              )}
            </>
          )}
        </>
      )}
    </Panel>
  )
}

// ---- Obico ----
export function ObicoPanel({ addon, disabled, onAction }: { addon: VoAddon; disabled: boolean; onAction: (a: VoAddonPanelAction) => void }) {
  const status = addon.status
  const server = typeof status?.server === 'string' ? status.server : ''
  const serverUrl = voServerUrlValid(server) ? server : ''
  const cloud = status?.cloud === true
  const linked = status?.linked === true
  const discovering = status?.discovering === true
  const linkCode = typeof status?.link_code === 'string' ? status.link_code : ''
  const [dialog, setDialog] = useState(false)

  const unlink = async () => {
    if (await confirm({ title: t('settings.addons.panel.obico.unlink_title'), body: t('settings.addons.panel.obico.unlink_body'), confirm: t('settings.addons.panel.obico.unlink'), danger: true })) onAction({ action: 'relink' })
  }

  return (
    <Panel addon={addon}>
      {status && (
        <>
          <KV k={t('settings.addons.panel.obico.server')}>
            <span className="inline-flex flex-wrap items-center justify-end gap-x-2">
              <span className="[overflow-wrap:anywhere]">{cloud ? obicoCloud : server}</span>
              <button className="btn btn-sm btn-ghost px-1.5! text-accent-ink!" disabled={disabled} onClick={() => setDialog(true)}>
                {t('settings.addons.panel.obico.change')}
              </button>
            </span>
          </KV>
          <Account linked={linked}>{serverUrl && <Ext href={serverUrl}>{t('settings.addons.panel.obico.open')}</Ext>}</Account>
          {linked ? (
            <Actions>
              <button className="btn btn-sm btn-danger" disabled={disabled} onClick={unlink}>
                {t('settings.addons.panel.obico.unlink')}
              </button>
            </Actions>
          ) : discovering ? (
            linkCode ? (
              <div className="mt-2">
                <Code>{linkCode}</Code>
                <div className="mt-1">{cloud ? t('settings.addons.panel.obico.link_cloud') : t('settings.addons.panel.obico.link_self')}</div>
                <ol className="my-1 grid list-decimal gap-0.5 pl-5">
                  <li>{t('settings.addons.panel.obico.step_device')}</li>
                  <li>{t('settings.addons.panel.obico.step_manual')}</li>
                  <li>{t('settings.addons.panel.obico.step_code')}</li>
                </ol>
              </div>
            ) : (
              <div className="mt-1 flex items-center gap-2 text-ink-2">
                <Spinner size={13} /> {t('settings.addons.panel.obico.waiting_code')}
              </div>
            )
          ) : (
            <>
              <div className="mt-1 text-ink-2">{t('settings.addons.panel.obico.stopped')}</div>
              <Actions>
                <button className="btn btn-sm btn-primary" disabled={disabled} onClick={() => onAction({ action: 'relink' })}>
                  {t('settings.addons.button.relink')}
                </button>
              </Actions>
            </>
          )}
        </>
      )}
      {dialog && (
        <ObicoServerDialog
          cloud={cloud}
          server={server}
          onClose={() => setDialog(false)}
          onSave={(next) => {
            setDialog(false)
            onAction({ action: 'relink', body: { server: next } })
          }}
        />
      )}
    </Panel>
  )
}

function ObicoServerDialog({ cloud, server, onClose, onSave }: { cloud: boolean; server: string; onClose: () => void; onSave: (server: string) => void }) {
  const [choice, setChoice] = useState<'cloud' | 'self_hosted'>(cloud ? 'cloud' : 'self_hosted')
  const [input, setInput] = useState(cloud ? '' : server)
  const valid = choice === 'cloud' || voServerUrlValid(input)
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('settings.addons.panel.obico.server_title')}
      footer={
        <>
          <CancelButton onClick={onClose} />
          <button className="btn btn-primary" disabled={!valid} onClick={() => onSave(choice === 'cloud' ? VO_OBICO_CLOUD : input.trim())}>
            {t('settings.addons.panel.obico.use_server')}
          </button>
        </>
      }
    >
      <p className="m-0 mb-3 text-ink-2">{t('settings.addons.panel.obico.change_note')}</p>
      <div className="grid gap-2">
        {(['cloud', 'self_hosted'] as const).map((c) => (
          <label key={c} className="flex items-center gap-2.5">
            <input type="radio" name="obico-server" className="h-4 w-4 accent-[var(--accent)]" checked={choice === c} onChange={() => setChoice(c)} />
            {c === 'cloud' ? obicoCloud : t('settings.addons.panel.obico.own_server')}
          </label>
        ))}
      </div>
      {choice === 'self_hosted' && (
        <div className="mt-3 grid gap-1">
          {/* i18n-ignore: an example address */}
          <input className="field code" autoFocus spellCheck={false} placeholder="http://192.168.1.10:3334" value={input} onChange={(e) => setInput(e.target.value)} aria-label={t('settings.addons.panel.obico.server_title')} />
          {input.trim() !== '' && !voServerUrlValid(input) && <div className="text-[12px] text-bad">{t('settings.addons.panel.obico.server_invalid')}</div>}
        </div>
      )}
    </Dialog>
  )
}

/**
 * What the last request answered, shown until the add-ons list is read again; panels for add-ons whose actions answer
 * with the new status (Tailscale's log in and out, ZeroTier's join and leave).
 */
function useAnswered<T>(addon: VoAddon, parse: (v: unknown) => T | null) {
  const [answered, setAnswered] = useState<T | null>(null)
  const [working, setWorking] = useState(false)
  const [error, setError] = useState('')
  const workingRef = useRef(false)
  useEffect(() => {
    if (!workingRef.current) setAnswered(null)
  }, [addon.status])
  const run = async (action: string, body?: Record<string, unknown>) => {
    workingRef.current = true
    setWorking(true)
    setError('')
    try {
      setAnswered(parse(await addonsRequest(path(addon, action), 'POST', undefined, body)))
      return true
    } catch (e) {
      setError(errorText(e))
      return false
    } finally {
      workingRef.current = false
      setWorking(false)
    }
  }
  const status = answered ?? parse(addon.status)
  const unavailable = answered == null ? unavailableText(addon) : ''
  return { status, unavailable, working, error, run }
}

// ---- Tailscale ----
// Labels in settings.addons.panel.tailscale.state.
const TAILSCALE_STATE: Record<VoTailscaleStatus['state'], string> = { running: 'text-good', starting: 'text-info', needs_login: 'text-warn', needs_approval: 'text-warn', stopped: 'text-warn' }

export function TailscalePanel({ addon }: { addon: VoAddon }) {
  const { status, unavailable, working, error, run } = useAnswered(addon, parseVoTailscaleStatus)
  const logOut = async () => {
    if (await confirm({ title: t('settings.addons.panel.tailscale.logout_title'), body: t('settings.addons.panel.tailscale.logout_body'), confirm: t('settings.addons.panel.tailscale.logout'), danger: true })) run('logout')
  }
  return (
    <Panel addon={addon} unavailable={unavailable}>
      {status && (
        <>
          <KV k="Tailscale">
            <span className={`font-semibold ${TAILSCALE_STATE[status.state]}`}>{t(`settings.addons.panel.tailscale.state.${status.state}` as MessageKey)}</span>
          </KV>
          {status.state === 'running' && (
            <>
              {status.tailnet && <KV k={t('settings.addons.panel.tailscale.tailnet')}>{status.tailnet}</KV>}
              {status.name && (
                <KV k={t('settings.addons.panel.tailscale.name')}>
                  <a href={`http://${status.name}/`} target="_blank" rel="noopener noreferrer" className="font-semibold text-accent-ink hover:underline">
                    {status.name}
                  </a>
                </KV>
              )}
              {status.addresses.length > 0 && (
                <KV k={t('settings.addons.panel.address')}>
                  <Addresses list={status.addresses} />
                </KV>
              )}
              {status.keyExpiry && <KV k={t('settings.addons.panel.tailscale.key_expiry')}>{fmtDateTime(status.keyExpiry)}</KV>}
              <div className="mt-1 text-ink-2">{t('settings.addons.panel.tailscale.reach')}</div>
              {status.problems.map((p) => (
                <Notice key={p} tone="warn" className="mt-2">
                  {p}
                </Notice>
              ))}
            </>
          )}
          {status.state === 'needs_approval' && <div className="mt-1">{t('settings.addons.panel.tailscale.needs_approval')}</div>}
          {status.state === 'needs_login' && (
            <>
              <div className="mt-1">
                {status.loginUrl ? t('settings.addons.panel.tailscale.login_url_hint') : t('settings.addons.panel.tailscale.login_hint')}
              </div>
              {status.loginUrl && (
                <div className="my-2 text-center">
                  <Ext href={status.loginUrl}>{status.loginUrl}</Ext>
                </div>
              )}
            </>
          )}
          {error && <Notice tone="bad" className="mt-2">{error}</Notice>}
          <Actions>
            {status.state === 'needs_login' && (
              <>
                {status.loginUrl && (
                  <a className="btn btn-sm btn-primary" href={status.loginUrl} target="_blank" rel="noopener noreferrer">
                    {t('settings.addons.panel.tailscale.sign_in')} <ExternalLink size={13} />
                  </a>
                )}
                <button className={`btn btn-sm ${status.loginUrl ? '' : 'btn-primary'}`} disabled={working} onClick={() => run('login')}>
                  {working && <Spinner size={12} />}
                  {status.loginUrl ? t('settings.addons.panel.tailscale.new_link') : t('settings.addons.panel.tailscale.log_in')}
                </button>
              </>
            )}
            {status.state === 'stopped' && (
              <button className="btn btn-sm btn-primary" disabled={working} onClick={() => run('login')}>
                {working && <Spinner size={12} />}
                {t('settings.addons.panel.tailscale.connect')}
              </button>
            )}
            {(status.state === 'running' || status.state === 'stopped' || status.state === 'needs_approval') && (
              <button className="btn btn-sm btn-danger" disabled={working} onClick={logOut}>
                {t('settings.addons.panel.tailscale.logout')}
              </button>
            )}
          </Actions>
        </>
      )}
    </Panel>
  )
}

// ---- ZeroTier ----
// Labels in settings.addons.panel.zerotier.state.
const NETWORK_STATE: Record<VoZerotierNetworkState, string> = {
  ok: 'text-good',
  requesting_configuration: 'text-info',
  access_denied: 'text-warn',
  not_found: 'text-bad',
  port_error: 'text-bad',
  client_too_old: 'text-bad',
  authentication_required: 'text-bad',
}

function networkMessage(state: VoZerotierNetworkState, node: string) {
  switch (state) {
    case 'ok':
      return t('settings.addons.panel.zerotier.message_ok')
    case 'access_denied':
      return t('settings.addons.panel.zerotier.message_access_denied', { node })
    case 'not_found':
      return t('settings.addons.panel.zerotier.message_not_found')
    case 'authentication_required':
      return t('settings.addons.panel.zerotier.message_authentication_required')
    default:
      return ''
  }
}

export function ZerotierPanel({ addon }: { addon: VoAddon }) {
  const { status, unavailable, working, error, run } = useAnswered<VoZerotierStatus>(addon, parseVoZerotierStatus)
  const [networkId, setNetworkId] = useState('')
  const idValid = voZerotierNetworkIdValid(networkId)

  const join = async () => {
    if (!idValid || working) return
    if (await run('join', { network: networkId.trim().toLowerCase() })) setNetworkId('')
  }
  const leave = async (network: { id: string; name: string }) => {
    const ok = await confirm({
      title: t('settings.addons.panel.zerotier.leave_title'),
      body: t('settings.addons.panel.zerotier.leave_body', { name: network.name || network.id }),
      confirm: t('settings.addons.panel.zerotier.leave'),
      danger: true,
    })
    if (ok) run('leave', { network: network.id })
  }

  return (
    <Panel addon={addon} unavailable={unavailable}>
      {status && (
        <>
          <KV k="ZeroTier">
            <span className={`font-semibold ${status.online ? 'text-good' : 'text-warn'}`}>{status.online ? t('settings.addons.panel.zerotier.online') : t('settings.addons.panel.zerotier.offline')}</span>
          </KV>
          {status.nodeId && (
            <KV k={t('settings.addons.panel.zerotier.node_id')}>
              <span className="code">{status.nodeId}</span>
            </KV>
          )}
          {status.networks.length === 0 && <div className="mt-1">{t('settings.addons.panel.zerotier.no_networks')}</div>}
          {status.networks.map((n) => {
            const message = networkMessage(n.state, status.nodeId)
            return (
              <div key={n.id} className="mt-2 grid gap-0.5 rounded-[9px] bg-surface px-3 py-2 shadow-[inset_0_0_0_1px_var(--line)]">
                <KV k={t('settings.addons.panel.zerotier.network')}>
                  <span>
                    {n.name ? `${n.name} ` : ''}
                    <span className="code text-[12px]">{n.name ? `(${n.id})` : n.id}</span>
                  </span>
                </KV>
                <KV k={t('settings.addons.panel.zerotier.status')}>
                  <span className={`font-semibold ${NETWORK_STATE[n.state]}`}>{t(`settings.addons.panel.zerotier.state.${n.state}` as MessageKey)}</span>
                </KV>
                {n.addresses.length > 0 && (
                  <KV k={t('settings.addons.panel.address')}>
                    <Addresses list={n.addresses} />
                  </KV>
                )}
                {message && <div className={`mt-1 ${n.state === 'ok' ? 'text-ink-2' : ''}`}>{message}</div>}
                <Actions>
                  <button className="btn btn-sm btn-danger" disabled={working} onClick={() => leave(n)}>
                    {t('settings.addons.panel.zerotier.leave')}
                  </button>
                </Actions>
              </div>
            )
          })}
          {error && <Notice tone="bad" className="mt-2">{error}</Notice>}
          <div className="mt-2 grid gap-1">
            <span className="lbl">{t('settings.addons.panel.zerotier.network_id')}</span>
            <div className="flex gap-2">
              <input
                className="field code"
                placeholder="8056c2e21c000001"
                maxLength={16}
                autoComplete="off"
                spellCheck={false}
                aria-label={t('settings.addons.panel.zerotier.network_id')}
                value={networkId}
                onChange={(e) => setNetworkId(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && join()}
              />
              <button className="btn btn-primary" disabled={!idValid || working} onClick={join}>
                {working && <Spinner size={12} />}
                {t('settings.addons.panel.zerotier.join')}
              </button>
            </div>
            <div className={`text-[12px] ${networkId && !idValid ? 'text-bad' : 'text-ink-3'}`}>
              {networkId && !idValid ? t('settings.addons.panel.zerotier.id_invalid') : t('settings.addons.panel.zerotier.id_hint')}
            </div>
          </div>
        </>
      )}
    </Panel>
  )
}

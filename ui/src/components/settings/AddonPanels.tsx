import { useEffect, useRef, useState, type ReactNode } from 'react'
import { ExternalLink } from 'lucide-react'
import { confirm, Dialog, Spinner } from '../ui'
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
import { fmtDateTime, fmtTime, KV, Notice } from './common'

// The status panels of the Fluidd fork's add-ons (VoOctoEverywhereStatus, VoObicoStatus, VoTailscaleStatus,
// VoZerotierStatus), with their wording.

const SERVICE: Record<string, { label: string; cls: string }> = {
  active: { label: 'Running', cls: 'text-good' },
  activating: { label: 'Starting', cls: 'text-info' },
  failed: { label: 'Failed', cls: 'text-bad' },
  inactive: { label: 'Stopped', cls: 'text-warn' },
  unknown: { label: 'Unknown', cls: 'text-warn' },
}

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))
const isIpv4 = (address: string) => /^\d{1,3}(?:\.\d{1,3}){3}$/.test(address)
const unavailableText = (addon: VoAddon) => (typeof addon.status?.unavailable === 'string' ? addon.status.unavailable : '')
const path = (addon: VoAddon, action: string) => `/addons/${encodeURIComponent(addon.id)}/${action}`

function Panel({ addon, children }: { addon: VoAddon; children?: ReactNode }) {
  const state = addon.service?.active ?? 'unknown'
  const service = SERVICE[state] ?? { label: state, cls: 'text-warn' }
  return (
    <div className="grid gap-0.5 rounded-[10px] bg-inset px-3 py-2 shadow-[inset_0_0_0_1px_var(--line)]">
      <KV k="Service">
        <span className={`font-semibold ${service.cls}`}>{service.label}</span>
      </KV>
      {children}
    </div>
  )
}

function Actions({ children }: { children: ReactNode }) {
  return <div className="mt-1 flex flex-wrap justify-end gap-2">{children}</div>
}

function Ext({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-semibold text-accent-ink hover:underline">
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

function LinkedChip() {
  return <span className="pill tone-good py-0.5!">Linked</span>
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
      if (!link) return setCodeError("OctoEverywhere didn't return a link code.")
      setCode({ link, expiresAt: link.expires_in > 0 ? Date.now() + link.expires_in * 1000 : 0 })
    } catch (e) {
      setCode(null)
      setCodeError(errorText(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <Panel addon={addon}>
      {unavailable ? (
        <div className="mt-1 text-ink-2">{unavailable}</div>
      ) : (
        status && (
          <>
            <KV k="OctoEverywhere">
              <span className={`font-semibold ${connected ? 'text-good' : 'text-warn'}`}>{connected ? 'Connected' : 'Not connected'}</span>
            </KV>
            <KV k="Account">
              {linked ? (
                <span className="inline-flex items-center gap-2">
                  <LinkedChip />
                  <Ext href="https://octoeverywhere.com/dashboard">Dashboard</Ext>
                </span>
              ) : (
                <span className="font-semibold text-warn">Not linked</span>
              )}
            </KV>
            {!linked && (
              <>
                <Actions>
                  {linkUrl && (
                    <a className="btn btn-sm btn-primary" href={linkUrl} target="_blank" rel="noopener noreferrer">
                      Link account <ExternalLink size={13} />
                    </a>
                  )}
                  <button className="btn btn-sm" disabled={loading} onClick={showLinkCode}>
                    {loading && <Spinner size={12} />}
                    Show link code
                  </button>
                </Actions>
                {codeError && <Notice tone="bad" className="mt-2">{codeError}</Notice>}
                {code && (
                  <div className="mt-2 text-center">
                    <Code>{code.link.code}</Code>
                    {code.link.url && (
                      <div>
                        Enter this code at <Ext href={code.link.url}>{code.link.url}</Ext>
                      </div>
                    )}
                    {code.expiresAt > 0 && <div className="text-[12px] text-ink-3">Expires at {fmtTime(code.expiresAt)}</div>}
                  </div>
                )}
              </>
            )}
          </>
        )
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
    if (await confirm({ title: 'Unlink Obico', body: 'Unlink this printer from your Obico account? Obico restarts and waits to be linked again.', confirm: 'Unlink', danger: true })) onAction({ action: 'relink' })
  }

  return (
    <Panel addon={addon}>
      {status && (
        <>
          <KV k="Server">
            <span className="inline-flex flex-wrap items-center justify-end gap-x-2">
              <span className="[overflow-wrap:anywhere]">{cloud ? 'Obico Cloud' : server}</span>
              <button className="btn btn-sm btn-ghost px-1.5! text-accent-ink!" disabled={disabled} onClick={() => setDialog(true)}>
                Change
              </button>
            </span>
          </KV>
          <KV k="Account">
            {linked ? (
              <span className="inline-flex items-center gap-2">
                <LinkedChip />
                {serverUrl && <Ext href={serverUrl}>Open Obico</Ext>}
              </span>
            ) : (
              <span className="font-semibold text-warn">Not linked</span>
            )}
          </KV>
          {linked ? (
            <Actions>
              <button className="btn btn-sm btn-danger" disabled={disabled} onClick={unlink}>
                Unlink
              </button>
            </Actions>
          ) : discovering ? (
            linkCode ? (
              <div className="mt-2">
                <Code>{linkCode}</Code>
                <div className="mt-1">{cloud ? 'To link this printer, add a printer in the Obico app or at app.obico.io.' : 'To link this printer, add a printer on your Obico server.'}</div>
                <ol className="my-1 grid list-decimal gap-0.5 pl-5">
                  <li>For the device, select "Generic Klipper - Self Installed".</li>
                  <li>Select "Switch to Manual Linking".</li>
                  <li>Enter the code above.</li>
                </ol>
              </div>
            ) : (
              <div className="mt-1 flex items-center gap-2 text-ink-2">
                <Spinner size={13} /> Waiting for Obico's link code…
              </div>
            )
          ) : (
            <>
              <div className="mt-1 text-ink-2">Obico stopped waiting to be linked. Restart linking to link it.</div>
              <Actions>
                <button className="btn btn-sm btn-primary" disabled={disabled} onClick={() => onAction({ action: 'relink' })}>
                  Restart linking
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
      title="Obico server"
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={!valid} onClick={() => onSave(choice === 'cloud' ? VO_OBICO_CLOUD : input.trim())}>
            Use this server
          </button>
        </>
      }
    >
      <p className="m-0 mb-3 text-ink-2">Changing the server unlinks the printer. Link it again once Obico has restarted.</p>
      <div className="grid gap-2">
        {(['cloud', 'self_hosted'] as const).map((c) => (
          <label key={c} className="flex items-center gap-2.5">
            <input type="radio" name="obico-server" className="h-4 w-4 accent-[var(--accent)]" checked={choice === c} onChange={() => setChoice(c)} />
            {c === 'cloud' ? 'Obico Cloud' : 'My own Obico server'}
          </label>
        ))}
      </div>
      {choice === 'self_hosted' && (
        <div className="mt-3 grid gap-1">
          <input className="field code" autoFocus spellCheck={false} placeholder="http://192.168.1.10:3334" value={input} onChange={(e) => setInput(e.target.value)} aria-label="Obico server" />
          {input.trim() !== '' && !voServerUrlValid(input) && <div className="text-[12px] text-bad">Enter an http or https address, such as http://192.168.1.10:3334.</div>}
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
const TAILSCALE_STATE: Record<VoTailscaleStatus['state'], { label: string; cls: string }> = {
  running: { label: 'Connected', cls: 'text-good' },
  starting: { label: 'Starting', cls: 'text-info' },
  needs_login: { label: 'Not logged in', cls: 'text-warn' },
  needs_approval: { label: 'Waiting for approval', cls: 'text-warn' },
  stopped: { label: 'Disconnected', cls: 'text-warn' },
}

export function TailscalePanel({ addon }: { addon: VoAddon }) {
  const { status, unavailable, working, error, run } = useAnswered(addon, parseVoTailscaleStatus)
  const logOut = async () => {
    if (await confirm({ title: 'Log out of Tailscale', body: 'Log this printer out of Tailscale? It leaves your tailnet until you log it in again.', confirm: 'Log out', danger: true })) run('logout')
  }
  return (
    <Panel addon={addon}>
      {unavailable ? (
        <div className="mt-1 text-ink-2">{unavailable}</div>
      ) : (
        status && (
          <>
            <KV k="Tailscale">
              <span className={`font-semibold ${TAILSCALE_STATE[status.state].cls}`}>{TAILSCALE_STATE[status.state].label}</span>
            </KV>
            {status.state === 'running' && (
              <>
                {status.tailnet && <KV k="Tailnet">{status.tailnet}</KV>}
                {status.name && (
                  <KV k="Name">
                    <a href={`http://${status.name}/`} target="_blank" rel="noopener noreferrer" className="font-semibold text-accent-ink hover:underline">
                      {status.name}
                    </a>
                  </KV>
                )}
                {status.addresses.length > 0 && (
                  <KV k="Address">
                    <Addresses list={status.addresses} />
                  </KV>
                )}
                {status.keyExpiry && <KV k="Sign-in expires">{fmtDateTime(status.keyExpiry)}</KV>}
                <div className="mt-1 text-ink-2">Devices on your tailnet can open Fluidd at the name or address above, and reach the printer's other services, such as SSH.</div>
                {status.problems.map((p) => (
                  <Notice key={p} tone="warn" className="mt-2">
                    {p}
                  </Notice>
                ))}
              </>
            )}
            {status.state === 'needs_approval' && <div className="mt-1">This printer is waiting to be approved in your tailnet's admin console at login.tailscale.com.</div>}
            {status.state === 'needs_login' && (
              <>
                <div className="mt-1">
                  {status.loginUrl ? 'Open this address on any device and sign in to Tailscale to add this printer to your tailnet.' : 'Log in to add this printer to your tailnet.'}
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
                      Sign in to Tailscale <ExternalLink size={13} />
                    </a>
                  )}
                  <button className={`btn btn-sm ${status.loginUrl ? '' : 'btn-primary'}`} disabled={working} onClick={() => run('login')}>
                    {working && <Spinner size={12} />}
                    {status.loginUrl ? 'New link' : 'Log in'}
                  </button>
                </>
              )}
              {status.state === 'stopped' && (
                <button className="btn btn-sm btn-primary" disabled={working} onClick={() => run('login')}>
                  {working && <Spinner size={12} />}
                  Connect
                </button>
              )}
              {(status.state === 'running' || status.state === 'stopped' || status.state === 'needs_approval') && (
                <button className="btn btn-sm btn-danger" disabled={working} onClick={logOut}>
                  Log out
                </button>
              )}
            </Actions>
          </>
        )
      )}
    </Panel>
  )
}

// ---- ZeroTier ----
const NETWORK_STATE: Record<VoZerotierNetworkState, { label: string; cls: string }> = {
  ok: { label: 'Connected', cls: 'text-good' },
  requesting_configuration: { label: 'Joining', cls: 'text-info' },
  access_denied: { label: 'Waiting for approval', cls: 'text-warn' },
  not_found: { label: 'Network not found', cls: 'text-bad' },
  port_error: { label: "Couldn't create its network interface", cls: 'text-bad' },
  client_too_old: { label: 'ZeroTier is too old for this network', cls: 'text-bad' },
  authentication_required: { label: 'Needs a browser sign-in', cls: 'text-bad' },
}

function networkMessage(state: VoZerotierNetworkState, node: string) {
  switch (state) {
    case 'ok':
      return "Devices on this network can open Fluidd at the address above, and reach the printer's other services, such as SSH."
    case 'access_denied':
      return `Waiting for approval. In the network's members at my.zerotier.com, authorize node ${node}.`
    case 'not_found':
      return 'ZeroTier has no network with this ID. Check the ID, then leave this network and join the right one.'
    case 'authentication_required':
      return "This network needs a sign-in in a browser, which the printer can't do. Turn off SSO for this network to use the printer on it."
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
      title: 'Leave ZeroTier network',
      body: `Leave ${network.name || network.id}? Devices on that network can no longer reach the printer until it joins again.`,
      confirm: 'Leave',
      danger: true,
    })
    if (ok) run('leave', { network: network.id })
  }

  return (
    <Panel addon={addon}>
      {unavailable ? (
        <div className="mt-1 text-ink-2">{unavailable}</div>
      ) : (
        status && (
          <>
            <KV k="ZeroTier">
              <span className={`font-semibold ${status.online ? 'text-good' : 'text-warn'}`}>{status.online ? 'Online' : 'Offline'}</span>
            </KV>
            {status.nodeId && (
              <KV k="Node ID">
                <span className="code">{status.nodeId}</span>
              </KV>
            )}
            {status.networks.length === 0 && <div className="mt-1">This printer isn't on a ZeroTier network. Enter the ID of the network to join.</div>}
            {status.networks.map((n) => {
              const message = networkMessage(n.state, status.nodeId)
              return (
                <div key={n.id} className="mt-2 grid gap-0.5 rounded-[9px] bg-surface px-3 py-2 shadow-[inset_0_0_0_1px_var(--line)]">
                  <KV k="Network">
                    <span>
                      {n.name ? `${n.name} ` : ''}
                      <span className="code text-[12px]">{n.name ? `(${n.id})` : n.id}</span>
                    </span>
                  </KV>
                  <KV k="Status">
                    <span className={`font-semibold ${NETWORK_STATE[n.state].cls}`}>{NETWORK_STATE[n.state].label}</span>
                  </KV>
                  {n.addresses.length > 0 && (
                    <KV k="Address">
                      <Addresses list={n.addresses} />
                    </KV>
                  )}
                  {message && <div className={`mt-1 ${n.state === 'ok' ? 'text-ink-2' : ''}`}>{message}</div>}
                  <Actions>
                    <button className="btn btn-sm btn-danger" disabled={working} onClick={() => leave(n)}>
                      Leave
                    </button>
                  </Actions>
                </div>
              )
            })}
            {error && <Notice tone="bad" className="mt-2">{error}</Notice>}
            <div className="mt-2 grid gap-1">
              <span className="lbl">Network ID</span>
              <div className="flex gap-2">
                <input
                  className="field code"
                  placeholder="8056c2e21c000001"
                  maxLength={16}
                  autoComplete="off"
                  spellCheck={false}
                  aria-label="Network ID"
                  value={networkId}
                  onChange={(e) => setNetworkId(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && join()}
                />
                <button className="btn btn-primary" disabled={!idValid || working} onClick={join}>
                  {working && <Spinner size={12} />}
                  Join
                </button>
              </div>
              <div className={`text-[12px] ${networkId && !idValid ? 'text-bad' : 'text-ink-3'}`}>
                {networkId && !idValid ? 'A network ID is 16 characters, 0-9 and a-f.' : 'Enter the 16-character ID of the network to join, from my.zerotier.com.'}
              </div>
            </div>
          </>
        )
      )}
    </Panel>
  )
}

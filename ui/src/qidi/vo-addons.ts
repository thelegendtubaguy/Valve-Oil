// Ported from the Valve Oil Fluidd fork (GPL-3.0), src/util/vo-addons.ts at
// github.com/thelegendtubaguy/fluidd qidi@6405679. Unchanged from the fork but for the one message it writes itself,
// translated here; keep it in step. Valve Oil UI passes its Moonraker's origin as apiUrl, and no token, as it has no
// Moonraker login.
//
// Client for Valve Oil's add-ons service (tltg-addons), which the nginx serving Fluidd on the printer
// exposes at /tltg-addons/api. nginx authorizes each request through Moonraker, so requests carry
// Fluidd's Moonraker access token when there is one.

import { t } from '../i18n'

export type VoAddonState = 'not_installed' | 'installed' | 'update_available' | 'foreign'

export type VoAddonAction = 'install' | 'update' | 'remove' | 'relink'

export type VoAddonJobState = 'running' | 'succeeded' | 'failed'

export interface VoAddonService {
  name: string;
  active: string;
}

export interface VoAddon {
  id: string;
  name: string;
  description: string;
  homepage: string;
  available_version: string | null;
  installed_version: string | null;
  state: VoAddonState;
  service: VoAddonService | null;
  status: Record<string, unknown> | null;
  /** Installed add-ons that must be removed before this one can be installed. */
  blocked_by: string[];
}

export interface VoAddonJob {
  id: string;
  addon: string;
  action: VoAddonAction;
  state: VoAddonJobState;
  message: string;
  log: string[];
}

export interface VoAddonsList {
  busy: boolean;
  printer_busy: boolean;
  addons: VoAddon[];
  job: VoAddonJob | null;
}

/** An add-on change an add-on's panel asks for, such as a relink with a new server. */
export interface VoAddonPanelAction {
  action: VoAddonAction;
  body?: Record<string, unknown>;
}

export interface VoAddonLinkCode {
  code: string;
  url: string;
  expires_in: number;
}

export type VoTailscaleState = 'needs_login' | 'needs_approval' | 'stopped' | 'starting' | 'running'

export interface VoTailscaleStatus {
  state: VoTailscaleState;
  /** Where to sign in to add the printer to a tailnet, once a login has asked Tailscale for it. */
  loginUrl: string;
  /** The printer's name on the tailnet, such as qidi-plus5.example.ts.net. */
  name: string;
  addresses: string[];
  tailnet: string;
  /** When the printer's sign-in runs out, in milliseconds, or null when it doesn't. */
  keyExpiry: number | null;
  problems: string[];
}

export type VoZerotierNetworkState =
  | 'ok'
  | 'requesting_configuration'
  | 'access_denied'
  | 'not_found'
  | 'port_error'
  | 'client_too_old'
  | 'authentication_required'

export interface VoZerotierNetwork {
  /** The 16-character network ID. */
  id: string;
  name: string;
  state: VoZerotierNetworkState;
  /** The printer's addresses on the network, without their prefix length. */
  addresses: string[];
}

export interface VoZerotierStatus {
  /** The printer's 10-character ZeroTier address, which the network's member list shows. */
  nodeId: string;
  /** Whether ZeroTier reaches its root servers. */
  online: boolean;
  networks: VoZerotierNetwork[];
}

export const VO_OBICO_CLOUD = 'https://app.obico.io'

export const VO_ADDONS_POLL_BUSY_MS = 2000
export const VO_ADDONS_POLL_IDLE_MS = 10000

const ADDON_STATES: readonly string[] = ['not_installed', 'installed', 'update_available', 'foreign']
const ADDON_ACTIONS: readonly string[] = ['install', 'update', 'remove', 'relink']
const JOB_STATES: readonly string[] = ['running', 'succeeded', 'failed']

const isRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === 'object' &&
  value !== null &&
  !Array.isArray(value)
)

const text = (value: unknown): string => typeof value === 'string' ? value : ''

const optionalText = (value: unknown): string | null => typeof value === 'string' && value !== '' ? value : null

/**
 * The add-ons API base, on the nginx that served this Fluidd. Only used while Fluidd talks to that same
 * printer's Moonraker, so the access token and the add-ons shown both belong to the printer in use.
 */
export const voAddonsApiBase = (apiUrl: string, location: Pick<Location, 'origin' | 'hostname'>): string | null => {
  if (!apiUrl) {
    return null
  }

  try {
    if (new URL(apiUrl).hostname !== location.hostname) {
      return null
    }
  } catch {
    return null
  }

  return `${location.origin}/tltg-addons/api`
}

const parseService = (value: unknown): VoAddonService | null => {
  if (!isRecord(value)) {
    return null
  }

  return {
    name: text(value.name),
    active: text(value.active) || 'unknown'
  }
}

const parseAddon = (value: unknown): VoAddon | null => {
  if (
    !isRecord(value) ||
    typeof value.id !== 'string' ||
    value.id === '' ||
    typeof value.state !== 'string' ||
    !ADDON_STATES.includes(value.state)
  ) {
    return null
  }

  return {
    id: value.id,
    name: text(value.name) || value.id,
    description: text(value.description),
    homepage: /^https?:\/\//.test(text(value.homepage)) ? text(value.homepage) : '',
    available_version: optionalText(value.available_version),
    installed_version: optionalText(value.installed_version),
    state: value.state as VoAddonState,
    service: parseService(value.service),
    status: isRecord(value.status) ? value.status : null,
    blocked_by: Array.isArray(value.blocked_by)
      ? value.blocked_by.filter((name): name is string => typeof name === 'string' && name !== '')
      : []
  }
}

const parseJob = (value: unknown): VoAddonJob | null => {
  if (
    !isRecord(value) ||
    typeof value.id !== 'string' ||
    typeof value.addon !== 'string' ||
    typeof value.action !== 'string' ||
    !ADDON_ACTIONS.includes(value.action) ||
    typeof value.state !== 'string' ||
    !JOB_STATES.includes(value.state)
  ) {
    return null
  }

  return {
    id: value.id,
    addon: value.addon,
    action: value.action as VoAddonAction,
    state: value.state as VoAddonJobState,
    message: text(value.message),
    log: Array.isArray(value.log)
      ? value.log.filter((line): line is string => typeof line === 'string')
      : []
  }
}

/**
 * Reads the add-ons list, or null when the response isn't one, as from a printer without the service.
 * Malformed add-ons are skipped and the rest kept.
 */
export const parseVoAddonsList = (value: unknown): VoAddonsList | null => {
  if (!isRecord(value) || !Array.isArray(value.addons)) {
    return null
  }

  const seen = new Set<string>()
  const addons = value.addons
    .map(parseAddon)
    .filter((addon): addon is VoAddon => {
      if (addon == null || seen.has(addon.id)) {
        return false
      }

      seen.add(addon.id)

      return true
    })
  const job = parseJob(value.job)

  return {
    busy: value.busy === true || job?.state === 'running',
    printer_busy: value.printer_busy === true,
    addons,
    job
  }
}

/** Reads the job an install, update, or remove request started. */
export const parseVoAddonsJob = (value: unknown): VoAddonJob | null => (
  isRecord(value)
    ? parseJob(value.job)
    : null
)

export const parseVoAddonLinkCode = (value: unknown): VoAddonLinkCode | null => {
  if (
    !isRecord(value) ||
    typeof value.code !== 'string' ||
    value.code === ''
  ) {
    return null
  }

  return {
    code: value.code,
    url: /^https?:\/\//.test(text(value.url)) ? text(value.url) : '',
    expires_in: typeof value.expires_in === 'number' && value.expires_in > 0 ? value.expires_in : 0
  }
}

const TAILSCALE_STATES: Record<string, VoTailscaleState> = {
  NeedsLogin: 'needs_login',
  NeedsMachineAuth: 'needs_approval',
  Stopped: 'stopped',
  Running: 'running'
}

/**
 * Reads the Tailscale add-on's status, or null when tailscaled didn't answer. States this doesn't know, as while
 * tailscaled starts, read as starting.
 */
export const parseVoTailscaleStatus = (value: unknown): VoTailscaleStatus | null => {
  if (!isRecord(value) || typeof value.state !== 'string') {
    return null
  }

  const state = TAILSCALE_STATES[value.state] ?? 'starting'
  const strings = (list: unknown): string[] => Array.isArray(list)
    ? list.filter((item): item is string => typeof item === 'string' && item !== '')
    : []
  const expiry = Date.parse(text(value.key_expiry))

  return {
    state,
    loginUrl: state === 'needs_login' && /^https?:\/\/\S+$/.test(text(value.login_url)) ? text(value.login_url) : '',
    name: /^[a-z0-9-]+(?:\.[a-z0-9-]+)*$/i.test(text(value.name)) ? text(value.name) : '',
    addresses: strings(value.addresses).filter(address => /^[0-9a-f:.]+$/i.test(address)),
    tailnet: text(value.tailnet),
    keyExpiry: Number.isFinite(expiry) && expiry > 0 ? expiry : null,
    problems: strings(value.problems)
  }
}

const ZEROTIER_NETWORK_STATES: readonly string[] = [
  'ok',
  'requesting_configuration',
  'access_denied',
  'not_found',
  'port_error',
  'client_too_old',
  'authentication_required'
]

const ZEROTIER_NETWORK_ID = /^[0-9a-f]{16}$/

/** Whether a value is a ZeroTier network ID: 16 characters, 0-9 and a-f, in either case. */
export const voZerotierNetworkIdValid = (value: unknown): boolean => (
  typeof value === 'string' &&
  ZEROTIER_NETWORK_ID.test(value.trim().toLowerCase())
)

/**
 * Reads the ZeroTier add-on's status, or null when zerotier-one didn't answer. Networks without a valid ID are
 * skipped, and states this doesn't know read as still joining.
 */
export const parseVoZerotierStatus = (value: unknown): VoZerotierStatus | null => {
  if (!isRecord(value) || typeof value.node_id !== 'string' || !Array.isArray(value.networks)) {
    return null
  }

  const networks = value.networks
    .filter(isRecord)
    .filter(network => typeof network.id === 'string' && ZEROTIER_NETWORK_ID.test(network.id))
    .map((network): VoZerotierNetwork => ({
      id: network.id as string,
      name: text(network.name),
      state: ZEROTIER_NETWORK_STATES.includes(text(network.status))
        ? network.status as VoZerotierNetworkState
        : 'requesting_configuration',
      addresses: Array.isArray(network.addresses)
        ? network.addresses
          .filter((address): address is string => typeof address === 'string')
          .map(address => address.split('/')[0])
          .filter(address => /^[0-9a-f:.]+$/i.test(address))
        : []
    }))

  return {
    nodeId: /^[0-9a-f]{10}$/.test(value.node_id) ? value.node_id : '',
    online: value.online === true,
    networks
  }
}

/**
 * What an add-on offers in its state: the main action, and whether it can be removed alongside it.
 * Add-ons installed outside Valve Oil offer nothing.
 */
export const voAddonActions = (addon: Pick<VoAddon, 'state'>): { primary: VoAddonAction | null, remove: boolean } => {
  switch (addon.state) {
    case 'not_installed':
      return { primary: 'install', remove: false }
    case 'update_available':
      return { primary: 'update', remove: true }
    case 'installed':
      return { primary: 'remove', remove: false }
    default:
      return { primary: null, remove: false }
  }
}

/**
 * Whether a server address looks like one Valve Oil accepts for an add-on such as Obico: http or https, a host,
 * an optional port and path, and no query. The printer checks it again.
 */
export const voServerUrlValid = (value: unknown): boolean => (
  typeof value === 'string' &&
  value.trim().length <= 200 &&
  /^https?:\/\/(?:[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?(?:\/[\w.~%/-]*)?$/.test(value.trim())
)

export const voAddonsPollInterval = (list: VoAddonsList | null): number => (
  list?.busy
    ? VO_ADDONS_POLL_BUSY_MS
    : VO_ADDONS_POLL_IDLE_MS
)

export class VoAddonsRequestError extends Error {
  readonly status: number

  constructor (status: number, message: string) {
    super(message)
    this.name = 'VoAddonsRequestError'
    this.status = status
  }
}

export interface VoAddonsRequestOptions {
  method?: 'GET' | 'POST';
  token?: string | null;
  signal?: AbortSignal;
  /** A POST's JSON body, such as the server for a relink. */
  body?: Record<string, unknown>;
}

/**
 * Calls the add-ons API and returns its JSON body. Throws VoAddonsRequestError with the server's
 * error text on an error status, and with status 0 when the printer can't be reached or doesn't answer
 * with JSON. POSTs carry the header and body the service requires of them.
 */
export const voAddonsRequest = async (base: string, path: string, options: VoAddonsRequestOptions = {}): Promise<unknown> => {
  const method = options.method ?? 'GET'
  const headers: Record<string, string> = {
    Accept: 'application/json'
  }

  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`
  }

  if (method === 'POST') {
    headers['X-Valve-Oil'] = 'addons'
    headers['Content-Type'] = 'application/json'
  }

  let response: Response

  try {
    response = await fetch(`${base}${path}`, {
      method,
      headers,
      body: method === 'POST' ? JSON.stringify(options.body ?? {}) : undefined,
      cache: 'no-store',
      credentials: 'same-origin',
      signal: options.signal
    })
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      throw e
    }

    throw new VoAddonsRequestError(0, e instanceof Error ? e.message : String(e))
  }

  let body: unknown = null

  try {
    body = await response.json()
  } catch {
    body = null
  }

  if (!response.ok) {
    const error = isRecord(body) ? text(body.error) : ''

    throw new VoAddonsRequestError(response.status, error || `${response.status} ${response.statusText}`.trim())
  }

  if (body === null) {
    throw new VoAddonsRequestError(0, t('settings.addons.invalid_response'))
  }

  return body
}

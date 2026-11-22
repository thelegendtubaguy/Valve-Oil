import { useSyncExternalStore } from 'react'

// Hash routes: #/<route>[/<sub>/<path>...]. Each segment is URI-encoded, so file paths with spaces survive.
export type Route = 'home' | 'files' | 'filaments' | 'control' | 'console' | 'tune' | 'history' | 'system' | 'settings' | 'updates' | 'mock'
const ROUTES: Route[] = ['home', 'files', 'filaments', 'control', 'console', 'tune', 'history', 'system', 'settings', 'updates', 'mock']

function segments(): string[] {
  return location.hash
    .replace(/^#\/?/, '')
    .split('/')
    .filter(Boolean)
    .map((s) => {
      try {
        return decodeURIComponent(s)
      } catch {
        return s
      }
    })
}

function current(): Route {
  const r = segments()[0] as Route
  // The Box page became Home's Box card; its Filaments tab is the filament library.
  if ((r as string) === 'box') return segments()[1] === 'filaments' ? 'filaments' : 'home'
  return ROUTES.includes(r) ? r : 'home'
}

// useSyncExternalStore needs a stable snapshot: the joined hash is compared, and the array is rebuilt only when it
// changes.
let lastHash = ''
let lastSub: string[] = []
function currentSub(): string[] {
  if (location.hash !== lastHash) {
    lastHash = location.hash
    lastSub = segments().slice(1)
  }
  return lastSub
}

function subscribe(fn: () => void) {
  window.addEventListener('hashchange', fn)
  return () => window.removeEventListener('hashchange', fn)
}

export function useRoute(): Route {
  return useSyncExternalStore(subscribe, current, current)
}

/** The segments after the route: ['config', 'printer.cfg'] for #/files/config/printer.cfg. */
export function useSubPath(): string[] {
  return useSyncExternalStore(subscribe, currentSub, currentSub)
}

export const href = (r: Route, ...sub: string[]) => `#/${[r, ...sub.filter((s) => s !== '').map(encodeURIComponent)].join('/')}`

export function navigate(r: Route, ...sub: string[]) {
  location.hash = href(r, ...sub)
}

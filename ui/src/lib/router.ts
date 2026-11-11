import { useSyncExternalStore } from 'react'

export type Route = 'home' | 'files' | 'box'
const ROUTES: Route[] = ['home', 'files', 'box']

function current(): Route {
  const r = location.hash.replace(/^#\/?/, '').split('/')[0] as Route
  return ROUTES.includes(r) ? r : 'home'
}

export function useRoute(): Route {
  return useSyncExternalStore(
    (fn) => {
      window.addEventListener('hashchange', fn)
      return () => window.removeEventListener('hashchange', fn)
    },
    current,
    current,
  )
}

export const href = (r: Route) => `#/${r}`

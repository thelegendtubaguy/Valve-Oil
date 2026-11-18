import { createStore, useStore } from './store'

// Dark or light, kept per browser in localStorage as `vo-theme` (index.html applies it before the first paint). One
// store, so the top bar's toggle and the Settings screen always show the same choice, and other tabs follow a change.
export type Theme = 'light' | 'dark'

const KEY = 'vo-theme'
const theme = createStore<Theme>(typeof document !== 'undefined' && !document.documentElement.classList.contains('dark') ? 'light' : 'dark')

function apply(t: Theme) {
  document.documentElement.classList.toggle('dark', t === 'dark')
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', t === 'dark' ? '#0b0f15' : '#ffffff')
}

export function setTheme(t: Theme) {
  theme.set(t)
  apply(t)
  try {
    localStorage.setItem(KEY, t)
  } catch {
    // Private browsing can refuse storage; the choice still holds for this page.
  }
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', (e) => {
    if (e.key !== KEY) return
    const t: Theme = e.newValue === 'light' ? 'light' : 'dark'
    theme.set(t)
    apply(t)
  })
}

export function useTheme(): [Theme, () => void] {
  const t = useStore(theme, (s) => s)
  return [t, () => setTheme(theme.get() === 'dark' ? 'light' : 'dark')]
}

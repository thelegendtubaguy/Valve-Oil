// Reloads open tabs once after a new build is installed: the build writes version.json, which the
// web server serves uncached; check it every 5 minutes and whenever the tab is shown again.
const INTERVAL = 5 * 60 * 1000

async function check() {
  try {
    const r = await fetch('./version.json', { cache: 'no-store' })
    if (!r.ok) return
    const { version } = (await r.json()) as { version?: string }
    if (version && version !== __APP_VERSION__) {
      const key = `vo-reloaded-${version}`
      if (sessionStorage.getItem(key)) return
      sessionStorage.setItem(key, '1')
      location.reload()
    }
  } catch {}
}

export function startUpdateCheck() {
  if (import.meta.env.DEV) return
  setInterval(check, INTERVAL)
  document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && check())
}

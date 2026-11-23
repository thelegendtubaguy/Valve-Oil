// Small browser helpers shared across screens: downloads, ids, errors, saved values, media queries, and popups.
import { useEffect, useRef, useState } from 'react'

/** An error's message, or the thrown value as text. */
export const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e))

/** A random v4 UUID. crypto.randomUUID needs a secure context, and the printer is usually reached over plain http. */
export function uuid() {
  const b = crypto.getRandomValues(new Uint8Array(16))
  b[6] = (b[6] & 0x0f) | 0x40
  b[8] = (b[8] & 0x3f) | 0x80
  const h = [...b].map((x) => x.toString(16).padStart(2, '0')).join('')
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`
}

/** Saves what's at a URL (a file on the printer, a data URL) under a name. */
export function saveUrl(url: string, name: string) {
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.rel = 'noopener'
  document.body.append(a)
  a.click()
  a.remove()
}

/** Saves a blob as a file. The URL is revoked later, as revoking right after the click can cancel the download. */
export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  saveUrl(url, name)
  setTimeout(() => URL.revokeObjectURL(url), 30000)
}

/** A value kept in this browser, or null when there's none or the browser refuses storage. */
export function readLocal(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

/** Keeps a value in this browser; private browsing can refuse it, which only loses the choice. */
export function saveLocal(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    // Kept for this page only.
  }
}

/** State kept in this browser under `key`, read again when the key changes. */
export function useStored<T extends string>(key: string, initial: T) {
  const read = () => (readLocal(key) as T | null) ?? initial
  const [value, setValue] = useState(read)
  const [shownKey, setShownKey] = useState(key)
  if (shownKey !== key) {
    setShownKey(key)
    setValue(read())
  }
  const set = (v: T) => {
    setValue(v)
    saveLocal(key, v)
  }
  return [value, set] as const
}

/** Whether a CSS media query matches, following changes. */
export function useMedia(query: string) {
  const [match, setMatch] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const m = window.matchMedia(query)
    const on = () => setMatch(m.matches)
    m.addEventListener('change', on)
    return () => m.removeEventListener('change', on)
  }, [query])
  return match
}

/** A popup's open state, closed by a press outside `ref` or by Escape. */
export function usePopup<T extends HTMLElement = HTMLDivElement>() {
  const [open, setOpen] = useState(false)
  const ref = useRef<T>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: Event) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', esc)
    return () => (window.removeEventListener('pointerdown', close), window.removeEventListener('keydown', esc))
  }, [open])
  return { open, setOpen, ref }
}

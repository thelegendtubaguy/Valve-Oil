import { useCallback, useRef, useSyncExternalStore } from 'react'

export type Store<T> = {
  get: () => T
  set: (next: T | ((prev: T) => T)) => void
  subscribe: (fn: () => void) => () => void
}

export function createStore<T>(initial: T): Store<T> {
  let state = initial
  const subs = new Set<() => void>()
  return {
    get: () => state,
    set: (next) => {
      const value = typeof next === 'function' ? (next as (p: T) => T)(state) : next
      if (value === state) return
      state = value
      for (const fn of subs) fn()
    },
    subscribe: (fn) => {
      subs.add(fn)
      return () => subs.delete(fn)
    },
  }
}

export function shallowEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true
  if (typeof a !== 'object' || typeof b !== 'object' || !a || !b) return false
  if (Array.isArray(a) !== Array.isArray(b)) return false
  const ka = Object.keys(a)
  const kb = Object.keys(b)
  if (ka.length !== kb.length) return false
  for (const k of ka) if (!Object.is((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k])) return false
  return true
}

/** Subscribes to a slice of a store; re-renders only when the slice changes (shallowly). */
export function useStore<T, S>(store: Store<T>, selector: (s: T) => S, equal: (a: S, b: S) => boolean = shallowEqual): S {
  const last = useRef<{ state: T; sel: (s: T) => S; value: S } | null>(null)
  const sel = useRef(selector)
  sel.current = selector
  const get = useCallback(() => {
    const state = store.get()
    const prev = last.current
    if (prev && prev.state === state && prev.sel === sel.current) return prev.value
    const value = sel.current(state)
    const keep = prev && equal(prev.value, value)
    last.current = { state, sel: sel.current, value: keep ? prev.value : value }
    return last.current.value
  }, [store, equal])
  return useSyncExternalStore(store.subscribe, get, get)
}

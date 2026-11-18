import { useEffect } from 'react'
import { client } from './printer'
import { RpcError } from '../moonraker/client'
import { createStore, useStore } from '../lib/store'

// Moonraker's database. Valve Oil UI reads and writes Fluidd's own `fluidd` namespace for anything Fluidd also shows
// (macro categories, temperature presets, console history, camera choices), using Fluidd's key names and value
// shapes, so the two never disagree. Settings only Valve Oil UI has go in the `valve_oil_ui` namespace.

export const FLUIDD = 'fluidd'
export const VALVE_OIL_UI = 'valve_oil_ui'

export async function getItem<T>(namespace: string, key?: string): Promise<T | undefined> {
  try {
    const r = await client.call<{ value: T }>('server.database.get_item', key ? { namespace, key } : { namespace })
    return r.value
  } catch {
    return undefined
  }
}

export async function postItem<T>(namespace: string, key: string, value: T): Promise<void> {
  await client.call('server.database.post_item', { namespace, key, value })
  cache.set((c) => ({ ...c, [`${namespace}:${key}`]: value }))
}

export async function deleteItem(namespace: string, key: string): Promise<void> {
  await client.call('server.database.delete_item', { namespace, key })
  cache.set((c) => {
    const next = { ...c }
    delete next[`${namespace}:${key}`]
    return next
  })
}

const cache = createStore<Record<string, unknown>>({})
const loading = new Set<string>()
const loaded = createStore<Record<string, true>>({})

// Loads a key once Moonraker is connected. A key that doesn't exist (404) counts as loaded with the fallback; any other
// failure, such as the socket dropping first, leaves it to load again on the next connect.
function load(namespace: string, key: string) {
  const id = `${namespace}:${key}`
  if (loading.has(id) || loaded.get()[id]) return
  loading.add(id)
  const run = () =>
    client
      .call<{ value: unknown }>('server.database.get_item', { namespace, key })
      .then((r) => cache.set((c) => ({ ...c, [id]: r.value })))
      .then(
        () => true,
        (e) => e instanceof RpcError && e.code === 404,
      )
      .then((done) => {
        loading.delete(id)
        if (done) loaded.set((l) => ({ ...l, [id]: true }))
      })
  if (client.state === 'open') return void run()
  const off = client.onState((st) => {
    if (st !== 'open') return
    off()
    run()
  })
}
client.onState((st) => {
  if (st !== 'open') return
  // Keys that failed while the socket was down load again now.
  for (const id of watched) {
    const [namespace, ...rest] = id.split(':')
    load(namespace, rest.join(':'))
  }
})
const watched = new Set<string>()

/**
 * One database value, loaded once per page and shared by every component that asks for it. The setter saves it to
 * Moonraker. `fallback` is used until it loads and when the key doesn't exist.
 */
export function useDbItem<T>(namespace: string, key: string, fallback: T): [T, (v: T) => Promise<void>, boolean] {
  const id = `${namespace}:${key}`
  const value = useStore(cache, (c) => c[id]) as T | undefined
  const isLoaded = useStore(loaded, (l) => !!l[id]) || value !== undefined
  useEffect(() => {
    watched.add(id)
    load(namespace, key)
  }, [id, namespace, key])
  return [value ?? fallback, (v: T) => postItem(namespace, key, v), isLoaded]
}

import { useSyncExternalStore } from 'react'
import { client, printer as printerStore, type Status } from '../../state/printer'
import { getItem, postItem, FLUIDD } from '../../state/database'
import { createStore, useStore } from '../../lib/store'
import { createBuffer, pushSample, type SeriesBuffer } from './chart-data'
import { referencedObjects } from './collectors'
import { t } from '../../i18n'

// Fluidd's Diagnostics charts. The cards live where Fluidd keeps them, the `fluidd` namespace's
// layout.layouts.diagnostics (container1…container4 of DiagnosticsCardConfig), so both UIs show the same charts.

export type MetricStyle = { lineColor: string; lineStyle: 'solid' | 'dashed' | 'dotted'; fillColor: string | null; fillOpacity: number; displayLegend: boolean }
export type Metric = { collector: string; name: string; style: MetricStyle }
export type ChartAxis = { enabled: boolean; unit: string; min?: number | string; max?: number | string; showLegend: boolean; metrics: Metric[] }
export type DiagnosticsCardConfig = { id: string; enabled: boolean; collapsed: boolean; icon: string; title: string; height: number; axes: ChartAxis[] }
export type DiagnosticsContainers = Record<string, DiagnosticsCardConfig[]>

/** Seconds of history, as Fluidd's CHART_HISTORY_RETENTION. */
export const DIAGNOSTICS_RETENTION = 1200

const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}`)

/** Fluidd's default card (createDiagnosticsCardState). */
export function defaultCard(): DiagnosticsCardConfig {
  return {
    id: uuid(),
    enabled: true,
    // i18n-ignore: Fluidd's own default card, names and all, saved to its database for both UIs
    title: 'Speeds',
    collapsed: false,
    height: 300,
    icon: 'motion',
    axes: [
      {
        enabled: true,
        unit: 'mm/s',
        showLegend: true,
        metrics: [
          { collector: 'printer.motion_report.live_velocity', name: 'Velocity', style: { lineStyle: 'solid', lineColor: '#2196f3', fillColor: null, fillOpacity: 0, displayLegend: true } },
          { collector: 'printer.toolhead.max_velocity', name: 'Max Velocity', style: { lineStyle: 'dotted', lineColor: '#0075d2', fillColor: null, fillOpacity: 0, displayLegend: false } },
        ],
      },
      {
        enabled: true,
        unit: 'mm³/s',
        showLegend: true,
        max: 20,
        metrics: [
          {
            collector: 'printer.motion_report.live_extruder_velocity * Math.PI * (printer.configfile.settings.extruder.filament_diameter / 2) ** 2',
            name: 'Flow',
            style: { lineStyle: 'solid', lineColor: '#b12f36', fillColor: null, fillOpacity: 5, displayLegend: true },
          },
          { collector: '12', name: 'Max Flow', style: { lineStyle: 'dashed', lineColor: '#820007', fillColor: null, fillOpacity: 0, displayLegend: false } },
        ],
      },
    ],
  }
}

export function defaultMetric(): Metric {
  return structuredClone(defaultCard().axes[0].metrics[0])
}

type LayoutState = { loaded: boolean; containers: DiagnosticsContainers }
const layout = createStore<LayoutState>({ loaded: false, containers: {} })
let loading = false

export async function loadLayout(force = false) {
  if (loading || (layout.get().loaded && !force)) return
  loading = true
  const value = await getItem<{ layouts?: Record<string, unknown> }>(FLUIDD, 'layout')
  loading = false
  const saved = value?.layouts?.diagnostics as DiagnosticsContainers | undefined
  const containers: DiagnosticsContainers = {}
  if (saved && typeof saved === 'object') {
    for (const [k, cards] of Object.entries(saved)) containers[k] = Array.isArray(cards) ? cards.filter((c) => c != null && Array.isArray(c.axes)) : []
    // As Fluidd's layout migration: fillColor was added later.
    for (const cards of Object.values(containers)) for (const c of cards) for (const a of c.axes) for (const m of a.metrics) m.style.fillColor = m.style.fillColor ?? null
  } else containers.container1 = [defaultCard()]
  layout.set({ loaded: true, containers })
}

export function useLayout() {
  return useStore(layout, (s) => s)
}

async function saveLayout(containers: DiagnosticsContainers) {
  layout.set({ loaded: true, containers })
  await postItem(FLUIDD, 'layout.layouts.diagnostics', containers)
}

export function saveCard(card: DiagnosticsCardConfig) {
  const containers = structuredClone(layout.get().containers)
  if (!card.id) {
    card.id = uuid()
    ;(containers.container1 ??= []).push(card)
  } else {
    for (const cards of Object.values(containers)) {
      const i = cards.findIndex((c) => c.id === card.id)
      if (i >= 0) {
        cards[i] = card
        break
      }
    }
  }
  return saveLayout(containers)
}

export function deleteCard(id: string) {
  const containers = structuredClone(layout.get().containers)
  for (const k of Object.keys(containers)) containers[k] = containers[k].filter((c) => c.id !== id)
  return saveLayout(containers)
}

export function allCards(containers: DiagnosticsContainers) {
  return Object.keys(containers)
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }))
    .flatMap((k) => containers[k])
}

// ---- collecting ----

let worker: Worker | null = null
let nextId = 0
function getWorker() {
  return (worker ??= new Worker(new URL('./collector.worker.ts', import.meta.url), { type: 'module' }))
}

/** Runs collectors in the sandbox worker against `printer`; a collector that hangs ends the worker. */
export function evalCollectors(printer: unknown, collectors: string[], timeoutMs = 800): Promise<Record<string, number | string>> {
  const w = getWorker()
  const id = ++nextId
  return new Promise((resolve, reject) => {
    const done = (fn: () => void) => (clearTimeout(timer), w.removeEventListener('message', onMsg), fn())
    const onMsg = (e: MessageEvent<{ id: number; results: Record<string, number | string> }>) => e.data.id === id && done(() => resolve(e.data.results))
    const timer = setTimeout(
      () =>
        done(() => {
          w.terminate()
          if (worker === w) worker = null
          reject(new Error(t('system.diagnostics.too_long')))
        }),
      timeoutMs,
    )
    w.addEventListener('message', onMsg)
    w.postMessage({ id, printer, collectors })
  })
}

/**
 * Klipper's state as Fluidd's collectors see it: every subscribed object, configfile with its settings, and any object
 * a collector names that this page doesn't subscribe to, queried now.
 */
export async function collectorPrinter(collectors: string[]): Promise<Status> {
  const s = printerStore.get()
  const out: Status = { ...s.status, configfile: { ...s.status.configfile, settings: s.settings } }
  const missing = [...new Set(collectors.flatMap(referencedObjects))].filter((o) => !(o in out) && s.objects.includes(o))
  if (missing.length) {
    try {
      const r = await client.call<{ status: Status }>('printer.objects.query', { objects: Object.fromEntries(missing.map((o) => [o, null])) })
      Object.assign(out, r.status)
    } catch {}
  }
  return out
}

const buffer: SeriesBuffer = createBuffer()
let rev = 0
const subs = new Set<() => void>()
let running = false

/** Starts sampling the charts once a second; it carries on after the tab closes so the charts keep their history. */
export function startCollecting() {
  if (running) return
  running = true
  let busy = false
  setInterval(async () => {
    const s = printerStore.get()
    if (busy || s.conn !== 'open' || s.klippy !== 'ready') return
    const collectors = [
      ...new Set(
        allCards(layout.get().containers)
          .filter((c) => c.enabled)
          .flatMap((c) => c.axes.filter((a) => a.enabled).flatMap((a) => a.metrics.map((m) => m.collector))),
      ),
    ]
    if (!collectors.length) return
    busy = true
    try {
      const results = await evalCollectors(await collectorPrinter(collectors), collectors)
      const values: Record<string, number> = {}
      for (const c of collectors) values[c] = typeof results[c] === 'number' ? (results[c] as number) : NaN
      pushSample(buffer, Date.now(), values, DIAGNOSTICS_RETENTION)
      rev++
      for (const fn of subs) fn()
    } catch {}
    busy = false
  }, 1000)
}

export function useDiagnosticsBuffer(): SeriesBuffer {
  useSyncExternalStore(
    (fn) => (subs.add(fn), () => subs.delete(fn)),
    () => rev,
  )
  return buffer
}

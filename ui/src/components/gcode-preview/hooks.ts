import { useMemo } from 'react'
import { usePrinter } from '../../state/printer'
import { useTheme } from '../../lib/theme'
import { isActive, type JobState } from '../../state/job'
import type { FileMeta } from '../../state/files'
import { getBounds, getExtrusionColors, getLayers, getToolColors } from './model'
import type { BBox, Layer, ParsedGcode } from './types'
import type { PreviewSettings } from './settings'
import type { ExcludeObject } from './PreviewCanvas'

/** The bed from the X and Y steppers' position_min and position_max, or the print's bounds when they're missing. */
export function useBed(bounds: BBox): BBox {
  const lim = usePrinter((s) => {
    const x = s.settings.stepper_x
    const y = s.settings.stepper_y
    const th = s.status.toolhead
    return [x?.position_min ?? th?.axis_minimum?.[0], x?.position_max ?? th?.axis_maximum?.[0], y?.position_min ?? th?.axis_minimum?.[1], y?.position_max ?? th?.axis_maximum?.[1]] as (number | undefined)[]
  })
  const [x0, x1, y0, y1] = lim
  return useMemo(
    () => ({
      x: { min: typeof x0 === 'number' ? x0 : bounds.x.min, max: typeof x1 === 'number' ? x1 : bounds.x.max },
      y: { min: typeof y0 === 'number' ? y0 : bounds.y.min, max: typeof y1 === 'number' ? y1 : bounds.y.max },
    }),
    [x0, x1, y0, y1, bounds],
  )
}

const NO_LAYERS: readonly Layer[] = []
const NO_BOUNDS: BBox = { x: { min: 0, max: 0 }, y: { min: 0, max: 0 } }

/** Layers, bounds, and colours for a parsed file. */
export function usePreviewModel(meta: FileMeta | null | undefined, data: ParsedGcode | null, settings: PreviewSettings) {
  const isDark = useTheme()[0] === 'dark'
  const layers = useMemo(() => (data ? getLayers(data, settings.minLayerHeight) : NO_LAYERS), [data, settings.minLayerHeight])
  const bounds = useMemo(() => (data ? getBounds(data, layers) : NO_BOUNDS), [data, layers])
  const fileColors = (Array.isArray(meta?.extruder_colors) ? meta.extruder_colors : Array.isArray(meta?.filament_colors) ? meta.filament_colors : null) ?? []
  const fileColorsKey = fileColors.join(',')
  const toolColors = useMemo(
    () => getToolColors(data?.tools ?? [], fileColorsKey ? fileColorsKey.split(',') : [], isDark),
    [data, fileColorsKey, isDark],
  )
  const colors = useMemo(() => getExtrusionColors(settings.colorMode, toolColors, isDark), [settings.colorMode, toolColors, isDark])
  const bed = useBed(bounds)
  return { isDark, layers, bounds, toolColors, colors, bed }
}

/** The printer's job as the preview needs it: which file, where in it, and its exclude_object objects. */
export function usePrintFollow(path: string | null) {
  const job = usePrinter((s) => {
    const ps = s.status.print_stats ?? {}
    return {
      filename: (ps.filename ?? '') as string,
      state: (ps.state ?? 'standby') as JobState,
      currentLayer: (ps.info?.current_layer ?? null) as number | null,
      filePosition: (s.status.virtual_sdcard?.file_position ?? 0) as number,
      x: s.status.toolhead?.position?.[0] as number | undefined,
      y: s.status.toolhead?.position?.[1] as number | undefined,
    }
  })
  const exclude = usePrinter((s) => s.status.exclude_object)
  const klippyReady = usePrinter((s) => s.klippy === 'ready')
  const active = isActive(job.state)
  const isPrinterFile = !!path && active && job.filename === path
  const objects = useMemo<ExcludeObject[] | null>(() => {
    // As in Fluidd: Klipper's objects replace the file's parts only while this file is the one printing.
    if (!isPrinterFile || !klippyReady || !exclude?.objects?.length) return null
    const excluded: string[] = exclude.excluded_objects ?? []
    return (exclude.objects as { name: string; center?: number[]; polygon?: number[][] }[]).map((o) => ({
      ...o,
      excluded: excluded.includes(o.name),
      current: exclude.current_object === o.name,
    }))
  }, [isPrinterFile, klippyReady, exclude])
  return { ...job, active, isPrinterFile, objects }
}

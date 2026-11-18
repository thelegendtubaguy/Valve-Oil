import type { ColorMode } from './types'

// Fluidd keeps its preview options in the `fluidd` namespace under uiSettings.gcodePreview, saving only what was changed
// from its defaults and merging them back in on load. Valve Oil UI reads and writes the same object, so an option set
// in either UI shows in both.
export type PreviewSettings = {
  extrusionLineWidth: number
  moveLineWidth: number
  retractionIconSize: number
  drawOrigin: boolean
  drawBackground: boolean
  showAnimations: boolean
  minLayerHeight: number
  autoLoadOnPrintStart: boolean
  autoLoadMobileOnPrintStart: boolean
  autoFollowOnFileLoad: boolean
  hideSinglePartBoundingBox: boolean
  autoZoom: boolean
  flip: { horizontal: boolean; vertical: boolean }
  showCurrentLayer: boolean
  showNextLayer: boolean
  showPreviousLayer: boolean
  showMoves: boolean
  showExtrusions: boolean
  showRetractions: boolean
  showParts: boolean
  followProgress: boolean
  colorMode: ColorMode
}

// Fluidd's defaults (src/store/config/state.ts).
export const PREVIEW_DEFAULTS: PreviewSettings = {
  extrusionLineWidth: 0.3,
  moveLineWidth: 0.1,
  retractionIconSize: 0.6,
  drawOrigin: true,
  drawBackground: true,
  showAnimations: true,
  minLayerHeight: 0.1,
  autoLoadOnPrintStart: false,
  autoLoadMobileOnPrintStart: false,
  autoFollowOnFileLoad: true,
  hideSinglePartBoundingBox: false,
  autoZoom: false,
  flip: { horizontal: false, vertical: true },
  showCurrentLayer: true,
  showNextLayer: false,
  showPreviousLayer: false,
  showMoves: true,
  showExtrusions: true,
  showRetractions: true,
  showParts: true,
  followProgress: false,
  colorMode: 'tool',
}

const COLOR_MODES: ColorMode[] = ['tool', 'role', 'speed', 'fan']

/** Stored values over the defaults, ignoring any of the wrong type. */
export function mergePreviewSettings(stored: unknown): PreviewSettings {
  const s = (stored && typeof stored === 'object' ? stored : {}) as Record<string, unknown>
  const out = { ...PREVIEW_DEFAULTS, flip: { ...PREVIEW_DEFAULTS.flip } } as Record<string, unknown>
  for (const [k, def] of Object.entries(PREVIEW_DEFAULTS)) {
    const v = s[k]
    if (k === 'flip') {
      const f = (v && typeof v === 'object' ? v : {}) as Record<string, unknown>
      out.flip = {
        horizontal: typeof f.horizontal === 'boolean' ? f.horizontal : PREVIEW_DEFAULTS.flip.horizontal,
        vertical: typeof f.vertical === 'boolean' ? f.vertical : PREVIEW_DEFAULTS.flip.vertical,
      }
    } else if (k === 'colorMode') {
      if (COLOR_MODES.includes(v as ColorMode)) out[k] = v
    } else if (typeof v === typeof def) out[k] = v
  }
  return out as PreviewSettings
}

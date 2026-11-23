import { useCallback, useMemo } from 'react'
import { FLUIDD, useDbItem } from '../../state/database'
import { mergePreviewSettings, type PreviewSettings } from './preview-settings'

export type { PreviewSettings } from './preview-settings'

const NONE: Record<string, unknown> = {}
let unsaved: Record<string, unknown> | null = null

export function usePreviewSettings(): [PreviewSettings, <K extends keyof PreviewSettings>(key: K, value: PreviewSettings[K]) => void] {
  const [stored, save] = useDbItem<Record<string, unknown>>(FLUIDD, 'uiSettings.gcodePreview', NONE)
  const settings = useMemo(() => mergePreviewSettings(stored), [stored])
  const set = useCallback(
    <K extends keyof PreviewSettings>(key: K, value: PreviewSettings[K]) => {
      // Changes made before the last save comes back build on it, so quick changes in a row all keep.
      const next = { ...(unsaved ?? stored), [key]: value }
      unsaved = next
      save(next)
        .catch(() => {})
        .finally(() => unsaved === next && (unsaved = null))
    },
    [stored, save],
  )
  return [settings, set]
}

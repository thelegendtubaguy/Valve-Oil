import { attempt } from '../toast'
import { FLUIDD, useDbItem } from '../../state/database'

// Fluidd's own settings, in its `fluidd` database namespace under uiSettings, one key each (Fluidd saves them by path
// the same way), so a setting changed in either UI shows in both. The defaults are Fluidd's (src/store/config/state.ts).
export const UI_DEFAULTS = {
  'general.instanceName': '',
  'general.confirmOnEstop': false,
  'general.defaultExtrudeLength': 10,
  'general.defaultExtrudeSpeed': 5,
  'general.defaultToolheadMoveLength': 1,
  'general.defaultToolheadXYSpeed': 130,
  'general.defaultToolheadZSpeed': 10,
  'general.toolheadMoveDistances': [0.1, 1, 10, 25, 50, 100],
  'general.zAdjustDistances': [0.005, 0.01, 0.025, 0.05],
  'general.useGcodeCoords': false,
  'general.axis.x.inverted': false,
  'general.axis.y.inverted': false,
  'general.axis.z.inverted': false,
  'general.forceMoveToggleWarning': true,
  'general.showManualProbeDialogAutomatically': true,
  'general.showBedScrewsAdjustDialogAutomatically': true,
  'general.showScrewsTiltAdjustDialogAutomatically': true,
  'general.textSortOrder': 'default',
  'general.filesAndFoldersDragAndDrop': true,
  'general.showRateOfChange': false,
  'warnings.warnOnCpuThrottled': true,
  'warnings.warnOnStepperDriverOverheating': true,
  'editor.confirmDirtyEditorClose': true,
  'editor.autoEditExtensions': ['.cfg', '.conf', '.ini', '.log', '.sh', '.txt'],
}

export type UiSettingPath = keyof typeof UI_DEFAULTS
type Widen<T> = T extends boolean ? boolean : T extends number ? number : T extends string ? string : T extends number[] ? number[] : T extends string[] ? string[] : T

/** One of Fluidd's settings: its value (Fluidd's default until set, or when the stored value is the wrong type) and a setter that saves it. */
export function useUiSetting<P extends UiSettingPath>(path: P): [Widen<(typeof UI_DEFAULTS)[P]>, (v: Widen<(typeof UI_DEFAULTS)[P]>) => void, boolean] {
  type T = Widen<(typeof UI_DEFAULTS)[P]>
  const fallback = UI_DEFAULTS[path] as T
  const [stored, save, loaded] = useDbItem<T>(FLUIDD, `uiSettings.${path}`, fallback)
  const ok = Array.isArray(fallback) ? Array.isArray(stored) : typeof stored === typeof fallback
  return [ok ? stored : fallback, (v: T) => void attempt(() => save(v)), loaded]
}

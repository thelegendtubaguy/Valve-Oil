// The fork's bed-soak-format.ts, with its wording (src/locales/en.yaml, app.bed_soak) in the tune area's messages.
import { locale, t, type MessageKey } from '../../i18n'

/** m:ss, or h:mm:ss from an hour */
export const bedSoakClock = (seconds: number): string => {
  const total = Math.max(0, Math.round(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = String(total % 60).padStart(2, '0')
  return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}:${secs}` : `${minutes}:${secs}`
}

/** 9 min 30 s */
export const bedSoakDuration = (seconds: number): string => {
  const total = Math.max(0, Math.round(seconds))
  return t('tune.soak.duration', { m: Math.floor(total / 60), s: String(total % 60).padStart(2, '0') })
}

export const bedSoakMeshName = (index: number): string => (index === 0 ? t('tune.soak.cold_mesh') : t('tune.soak.mesh_n', { n: index }))

export const bedSoakPointName = (point: { x: number; y: number }): string => `X${Math.round(point.x)} Y${Math.round(point.y)}`

export const bedSoakMeshList = (indices: number[]): string => indices.join(', ')

export const bedSoakTargets = (bed: number, chamber: number | null | undefined) => (chamber ? t('tune.soak.targets', { bed, chamber }) : t('tune.soak.targets_no_chamber', { bed }))

export const bedSoakTargetsShort = (bed: number, chamber: number | null | undefined) => (chamber ? `${bed} °C · ${chamber} °C` : t('tune.soak.targets_short_no_chamber', { bed }))

const STATE_LABELS: Record<string, MessageKey> = {
  failed: 'tune.soak.state.failed',
  finished: 'tune.soak.state.finished',
  incomplete: 'tune.soak.state.incomplete',
  running: 'tune.soak.state.running',
  stopped: 'tune.soak.state.stopped',
}

export const bedSoakStateLabel = (state: string): string | undefined => (STATE_LABELS[state] ? t(STATE_LABELS[state]) : undefined)

export const fmtDateTime = (epoch: number) => new Date(epoch * 1000).toLocaleString(locale(), { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
export const fmtDate = (epoch: number) => new Date(epoch * 1000).toLocaleDateString(locale(), { year: 'numeric', month: 'short', day: 'numeric' })

/** +1.2 µm, −0.4 µm */
export const formatChange = (um: number) => `${um >= 0 ? '+' : '−'}${Math.abs(um).toFixed(1)} µm`

// The fork's bed-soak-format.ts, with its wording (src/locales/en.yaml, app.bed_soak) inlined.

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
  return `${Math.floor(total / 60)} min ${String(total % 60).padStart(2, '0')} s`
}

export const bedSoakMeshName = (index: number): string => (index === 0 ? 'Cold mesh' : `Mesh ${index}`)

export const bedSoakPointName = (point: { x: number; y: number }): string => `X${Math.round(point.x)} Y${Math.round(point.y)}`

export const bedSoakMeshList = (indices: number[]): string => indices.join(', ')

export const bedSoakTargets = (bed: number, chamber: number | null | undefined) => (chamber ? `${bed} °C bed, ${chamber} °C chamber` : `${bed} °C bed, chamber off`)

export const bedSoakTargetsShort = (bed: number, chamber: number | null | undefined) => (chamber ? `${bed} °C · ${chamber} °C` : `${bed} °C · chamber off`)

export const bedSoakStateLabel: Record<string, string> = { failed: 'Failed', finished: 'Finished', incomplete: 'Incomplete', running: 'Running', stopped: 'Stopped' }

export const fmtDateTime = (epoch: number) => new Date(epoch * 1000).toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
export const fmtDate = (epoch: number) => new Date(epoch * 1000).toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' })

/** +1.2 µm, −0.4 µm */
export const formatChange = (um: number) => `${um >= 0 ? '+' : '−'}${Math.abs(um).toFixed(1)} µm`

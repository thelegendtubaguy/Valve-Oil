import { useEffect, useState } from 'react'
import { Camera, Pencil, Plus, Trash2, VideoOff } from 'lucide-react'
import { Choices, confirm, Dialog, Empty, Spinner, Toggle } from '../ui'
import { attempt } from '../toast'
import { client, usePrinter } from '../../state/printer'
import { Row, SettingsCard, TextField } from './common'

// Moonraker's webcams (server.webcams.*), the same list Fluidd and the slicers use, with Fluidd's camera form.
export type Webcam = {
  uid?: string
  name: string
  enabled: boolean
  service: string
  location?: string
  icon?: string
  target_fps: number
  target_fps_idle: number
  stream_url: string
  snapshot_url: string
  flip_horizontal: boolean
  flip_vertical: boolean
  rotation: number
  aspect_ratio?: string
  source?: string
  extra_data?: Record<string, unknown>
}

// Fluidd's stream types (app.setting.camera_type_options).
const SERVICES: { value: string; label: string }[] = [
  { value: 'mjpegstreamer-adaptive', label: 'MJPEG Adaptive' },
  { value: 'mjpegstreamer', label: 'MJPEG Stream' },
  { value: 'uv4l-mjpeg', label: 'UV4L-MJPEG Stream' },
  { value: 'hlsstream', label: 'HLS Stream' },
  { value: 'webrtc-camerastreamer', label: 'WebRTC (camera-streamer)' },
  { value: 'webrtc-go2rtc', label: 'WebRTC (go2rtc)' },
  { value: 'webrtc-mediamtx', label: 'WebRTC (MediaMTX)' },
  { value: 'ipstream', label: 'IP Camera' },
  { value: 'iframe', label: 'HTTP page' },
]
const serviceLabel = (s: string) => SERVICES.find((x) => x.value === s)?.label ?? s

// Fluidd's defaults for a new camera (Globals.DEFAULTS), the stream QIDI's printers serve.
const NEW_CAMERA: Webcam = {
  name: '',
  enabled: true,
  service: 'mjpegstreamer-adaptive',
  target_fps: 15,
  target_fps_idle: 5,
  stream_url: '/webcam/?action=stream',
  snapshot_url: '/webcam/?action=snapshot',
  flip_horizontal: false,
  flip_vertical: false,
  rotation: 0,
  aspect_ratio: '4:3',
}

export function useWebcams(): Webcam[] | null {
  const [cams, setCams] = useState<Webcam[] | null>(null)
  const open = usePrinter((s) => s.conn === 'open')
  useEffect(() => {
    if (!open) return
    client
      .call<{ webcams: Webcam[] }>('server.webcams.list')
      .then((r) => setCams(r.webcams))
      .catch(() => setCams([]))
    const off = client.on('notify_webcams_changed', (p) => {
      const list = (p[0] as { webcams?: Webcam[] } | undefined)?.webcams
      if (Array.isArray(list)) setCams(list)
    })
    return () => {
      off()
    }
  }, [open])
  return cams
}

/** A webcam's address as this page reaches it: relative ones are on the printer's web server. */
export const camUrl = (url: string) => {
  try {
    return new URL(url, client.httpBase + '/').toString()
  } catch {
    return url
  }
}

export function CameraSettings() {
  const cams = useWebcams()
  const [editing, setEditing] = useState<Webcam | null>(null)

  const remove = async (cam: Webcam) => {
    if (!cam.uid) return
    const ok = await confirm({ title: `Remove ${cam.name}`, body: `Are you sure you want to remove the camera ${cam.name}?`, confirm: 'Remove', danger: true })
    if (ok) await attempt(() => client.call('server.webcams.delete_item', { uid: cam.uid }))
  }

  return (
    <SettingsCard
      title="Cameras"
      icon={<Camera size={14} />}
      aside={
        <button className="btn btn-sm" onClick={() => setEditing({ ...NEW_CAMERA })}>
          <Plus size={13} /> Add camera
        </button>
      }
      footer="Cameras are kept in Moonraker, so Fluidd and the slicers show the same ones."
    >
      {cams == null ? (
        <div className="flex justify-center py-4">
          <Spinner />
        </div>
      ) : cams.length === 0 ? (
        <Empty>No cameras yet.</Empty>
      ) : (
        cams.map((cam) => (
          <div key={cam.uid ?? cam.name} className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0">
            <div className="h-[54px] w-[96px] flex-none overflow-hidden rounded-lg bg-[#14161b]">
              <Snapshot cam={cam} />
            </div>
            <div className="min-w-[160px] flex-1">
              <div className="flex flex-wrap items-center gap-2 font-medium">
                {cam.name}
                {!cam.enabled && <span className="pill tone-muted py-0.5!">Off</span>}
              </div>
              <div className="text-[12.5px] text-ink-3">
                {serviceLabel(cam.service)} · <span className="code text-[12px]">{cam.stream_url}</span>
              </div>
              {cam.source === 'config' && <div className="text-[12px] text-ink-3">Managed by your Moonraker configuration</div>}
            </div>
            {cam.source !== 'config' && (
              <div className="ml-auto flex gap-1">
                <button className="iconbtn" onClick={() => setEditing({ ...NEW_CAMERA, ...cam })} aria-label={`Edit ${cam.name}`} title="Edit camera">
                  <Pencil size={16} />
                </button>
                <button className="iconbtn" onClick={() => remove(cam)} aria-label={`Remove ${cam.name}`} title="Remove camera">
                  <Trash2 size={16} />
                </button>
              </div>
            )}
          </div>
        ))
      )}
      {editing && <CameraDialog camera={editing} names={(cams ?? []).filter((c) => c.uid !== editing.uid).map((c) => c.name)} onClose={() => setEditing(null)} />}
    </SettingsCard>
  )
}

/** A small still from the camera's snapshot address, refreshed now and then. */
function Snapshot({ cam }: { cam: Webcam }) {
  const [tick, setTick] = useState(0)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 10000)
    return () => clearInterval(t)
  }, [])
  if (!cam.enabled || failed || !cam.snapshot_url || cam.service === 'iframe')
    return (
      <div className="grid h-full place-items-center text-[#9aa1ad]">
        <VideoOff size={16} />
      </div>
    )
  return <img src={withCacheBust(camUrl(cam.snapshot_url), tick)} alt="" className="h-full w-full object-cover" style={{ transform: camTransform(cam) }} onError={() => setFailed(true)} />
}

const withCacheBust = (url: string, n: number) => `${url}${url.includes('?') ? '&' : '?'}t=${n}`
const camTransform = (c: Pick<Webcam, 'flip_horizontal' | 'flip_vertical' | 'rotation'>) =>
  [c.flip_horizontal ? 'scaleX(-1)' : '', c.flip_vertical ? 'scaleY(-1)' : '', c.rotation ? `rotate(${c.rotation}deg)` : ''].join(' ') || undefined

const ASPECT = /^\d+(\.\d+)?:\d+(\.\d+)?$/

function CameraDialog({ camera, names, onClose }: { camera: Webcam; names: string[]; onClose: () => void }) {
  const [c, setC] = useState<Webcam>(camera)
  const [saving, setSaving] = useState(false)
  const set = <K extends keyof Webcam>(k: K, v: Webcam[K]) => setC((x) => ({ ...x, [k]: v }))
  const name = c.name.trim()
  const errors = [
    !name && 'Enter a name.',
    names.includes(name) && 'Another camera has this name.',
    !c.stream_url.trim() && 'Enter the stream address.',
    !c.snapshot_url.trim() && 'Enter the snapshot address.',
    c.service === 'mjpegstreamer-adaptive' && !(c.target_fps > 0) && 'Enter an FPS target.',
    c.service === 'iframe' && !ASPECT.test(c.aspect_ratio ?? '') && 'Enter the aspect ratio as width:height, such as 16:9.',
  ].filter(Boolean) as string[]

  const save = async () => {
    setSaving(true)
    const { source: _source, ...entry } = { ...c, name, stream_url: c.stream_url.trim(), snapshot_url: c.snapshot_url.trim() }
    const ok = await attempt(() => client.call('server.webcams.post_item', entry as unknown as Record<string, unknown>))
    setSaving(false)
    if (ok !== undefined) onClose()
  }

  return (
    <Dialog
      open
      size="wide"
      onClose={onClose}
      title={camera.uid ? 'Edit camera' : 'Add camera'}
      footer={
        <>
          {errors.length > 0 && <span className="mr-auto text-[12.5px] text-ink-3">{errors[0]}</span>}
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={errors.length > 0 || saving} onClick={save}>
            {saving && <Spinner size={13} />}
            {camera.uid ? 'Save' : 'Add'}
          </button>
        </>
      }
    >
      <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="grid content-start divide-y divide-line">
          <Row title="Enable">
            <Toggle checked={c.enabled} onChange={(v) => set('enabled', v)} label="Enable" />
          </Row>
          <Row title="Name" wide>
            <input className="field" value={c.name} onChange={(e) => set('name', e.target.value)} aria-label="Name" autoFocus={!camera.uid} />
          </Row>
          <Row title="Stream type" wide>
            <select className="field" value={c.service} onChange={(e) => set('service', e.target.value)} aria-label="Stream type">
              {SERVICES.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
              {!SERVICES.some((s) => s.value === c.service) && <option value={c.service}>{c.service}</option>}
            </select>
          </Row>
          <Row title="Camera URL stream" wide>
            <input className="field code" type="url" spellCheck={false} value={c.stream_url} onChange={(e) => set('stream_url', e.target.value)} aria-label="Camera URL stream" />
          </Row>
          <Row title="Camera URL snapshot" wide>
            <input className="field code" type="url" spellCheck={false} value={c.snapshot_url} onChange={(e) => set('snapshot_url', e.target.value)} aria-label="Camera URL snapshot" />
          </Row>
          {c.service === 'mjpegstreamer-adaptive' && (
            <>
              <Row title="FPS target" wide>
                <NumberInput value={c.target_fps} onChange={(v) => set('target_fps', v)} label="FPS target" />
              </Row>
              <Row title="FPS target when not in focus" wide>
                <NumberInput value={c.target_fps_idle} onChange={(v) => set('target_fps_idle', v)} label="FPS target when not in focus" />
              </Row>
            </>
          )}
          {c.service === 'iframe' && (
            <Row title="Aspect ratio" sub="[width : height]" wide>
              <TextField value={c.aspect_ratio ?? ''} onSave={(v) => set('aspect_ratio', v)} placeholder="16:9" label="Aspect ratio" />
            </Row>
          )}
          <Row title="Rotate by">
            <Choices value={c.rotation} onChange={(v) => set('rotation', v)} options={[0, 90, 180, 270].map((r) => ({ value: r, label: r ? `${r}°` : 'None' }))} />
          </Row>
          <Row title="Flip horizontally">
            <Toggle checked={c.flip_horizontal} onChange={(v) => set('flip_horizontal', v)} label="Flip horizontally" />
          </Row>
          <Row title="Flip vertically">
            <Toggle checked={c.flip_vertical} onChange={(v) => set('flip_vertical', v)} label="Flip vertically" />
          </Row>
        </div>
        <div className="grid content-start gap-2">
          <div className="section-label">Preview</div>
          <CameraPreview cam={c} />
        </div>
      </div>
    </Dialog>
  )
}

function NumberInput({ value, onChange, label }: { value: number; onChange: (v: number) => void; label: string }) {
  const [draft, setDraft] = useState(String(value))
  return (
    <input
      className="field num"
      inputMode="numeric"
      aria-label={label}
      value={draft}
      onChange={(e) => {
        setDraft(e.target.value)
        const n = Number(e.target.value)
        onChange(e.target.value.trim() !== '' && Number.isFinite(n) ? n : 0)
      }}
    />
  )
}

/** The camera as the form has it now: a stream, snapshots at the FPS target, a video, or the page. */
export function CameraPreview({ cam }: { cam: Webcam }) {
  const [tick, setTick] = useState(0)
  const [failed, setFailed] = useState(false)
  const url = camUrl(cam.service === 'mjpegstreamer-adaptive' ? cam.snapshot_url : cam.stream_url)
  useEffect(() => setFailed(false), [url, cam.service])
  useEffect(() => {
    if (cam.service !== 'mjpegstreamer-adaptive' || !(cam.target_fps > 0)) return
    const t = setInterval(() => setTick((n) => n + 1), Math.max(1000 / Math.min(cam.target_fps, 30), 33))
    return () => clearInterval(t)
  }, [cam.service, cam.target_fps])

  const ratio = ASPECT.test(cam.aspect_ratio ?? '') ? cam.aspect_ratio!.replace(':', ' / ') : '16 / 9'
  const style = { transform: camTransform(cam) }
  let body
  if (failed) body = <Unavailable text="The camera didn't answer at this address." />
  else if (['mjpegstreamer', 'uv4l-mjpeg', 'mjpegstreamer-adaptive'].includes(cam.service))
    body = <img src={cam.service === 'mjpegstreamer-adaptive' ? withCacheBust(url, tick) : url} alt="" className="absolute inset-0 h-full w-full object-contain" style={style} onError={() => setFailed(true)} />
  else if (cam.service === 'ipstream') body = <video src={url} autoPlay muted playsInline className="absolute inset-0 h-full w-full object-contain" style={style} onError={() => setFailed(true)} />
  else if (cam.service === 'iframe') body = <iframe src={url} title="Camera preview" className="absolute inset-0 h-full w-full border-0" style={style} />
  else body = <Unavailable text="Valve Oil UI can't preview this stream type. Fluidd shows it." />
  return (
    <div className="relative w-full overflow-hidden rounded-[10px] bg-[#14161b]" style={{ aspectRatio: ratio }}>
      {body}
    </div>
  )
}

function Unavailable({ text }: { text: string }) {
  return (
    <div className="absolute inset-0 grid place-items-center p-4 text-center text-[12.5px] text-[#9aa1ad]">
      <span className="flex items-center gap-2">
        <VideoOff size={16} className="flex-none" /> {text}
      </span>
    </div>
  )
}

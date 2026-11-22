import { useEffect, useState } from 'react'
import { Camera, Pencil, Plus, Trash2, VideoOff } from 'lucide-react'
import { Choices, confirm, Dialog, Empty, Spinner, Toggle } from '../ui'
import { attempt } from '../toast'
import { client, usePrinter } from '../../state/printer'
import { Row, SettingsCard, TextField } from './common'
import { t } from '../../i18n'

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
const services = (): { value: string; label: string }[] => [
  { value: 'mjpegstreamer-adaptive', label: t('settings.cameras.service.mjpeg_adaptive') },
  { value: 'mjpegstreamer', label: t('settings.cameras.service.mjpeg') },
  { value: 'uv4l-mjpeg', label: t('settings.cameras.service.uv4l') },
  { value: 'hlsstream', label: t('settings.cameras.service.hls') },
  // Names of the streaming programs.
  { value: 'webrtc-camerastreamer', label: 'WebRTC (camera-streamer)' }, // i18n-ignore
  { value: 'webrtc-go2rtc', label: 'WebRTC (go2rtc)' }, // i18n-ignore
  { value: 'webrtc-mediamtx', label: 'WebRTC (MediaMTX)' }, // i18n-ignore
  { value: 'ipstream', label: t('settings.cameras.service.ip') },
  { value: 'iframe', label: t('settings.cameras.service.iframe') },
]
const serviceLabel = (s: string) => services().find((x) => x.value === s)?.label ?? s

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
    const ok = await confirm({ title: t('settings.common.remove_named', { name: cam.name }), body: t('settings.cameras.remove_confirm', { name: cam.name }), confirm: t('common.remove'), danger: true })
    if (ok) await attempt(() => client.call('server.webcams.delete_item', { uid: cam.uid }))
  }

  return (
    <SettingsCard
      title={t('settings.cameras.title')}
      icon={<Camera size={14} />}
      aside={
        <button className="btn btn-sm" onClick={() => setEditing({ ...NEW_CAMERA })}>
          <Plus size={13} /> {t('settings.cameras.add')}
        </button>
      }
      footer={t('settings.cameras.footer')}
    >
      {cams == null ? (
        <div className="flex justify-center py-4">
          <Spinner />
        </div>
      ) : cams.length === 0 ? (
        <Empty>{t('settings.cameras.empty')}</Empty>
      ) : (
        cams.map((cam) => (
          <div key={cam.uid ?? cam.name} className="flex flex-wrap items-center gap-3 py-3 first:pt-0 last:pb-0">
            <div className="h-[54px] w-[96px] flex-none overflow-hidden rounded-lg bg-[#14161b]">
              <Snapshot cam={cam} />
            </div>
            <div className="min-w-[160px] flex-1">
              <div className="flex flex-wrap items-center gap-2 font-medium">
                {cam.name}
                {!cam.enabled && <span className="pill tone-muted py-0.5!">{t('common.off')}</span>}
              </div>
              <div className="text-[12.5px] text-ink-3">
                {serviceLabel(cam.service)} · <span className="code text-[12px]">{cam.stream_url}</span>
              </div>
              {cam.source === 'config' && <div className="text-[12px] text-ink-3">{t('settings.common.managed_by_config')}</div>}
            </div>
            {cam.source !== 'config' && (
              <div className="ml-auto flex gap-1">
                <button className="iconbtn" onClick={() => setEditing({ ...NEW_CAMERA, ...cam })} aria-label={t('settings.common.edit_named', { name: cam.name })} title={t('settings.cameras.edit')}>
                  <Pencil size={16} />
                </button>
                <button className="iconbtn" onClick={() => remove(cam)} aria-label={t('settings.common.remove_named', { name: cam.name })} title={t('settings.cameras.remove')}>
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
  const serviceList = services()
  const name = c.name.trim()
  const errors = [
    !name && t('settings.cameras.error.name_required'),
    names.includes(name) && t('settings.cameras.error.name_taken'),
    !c.stream_url.trim() && t('settings.cameras.error.stream_required'),
    !c.snapshot_url.trim() && t('settings.cameras.error.snapshot_required'),
    c.service === 'mjpegstreamer-adaptive' && !(c.target_fps > 0) && t('settings.cameras.error.fps_required'),
    c.service === 'iframe' && !ASPECT.test(c.aspect_ratio ?? '') && t('settings.cameras.error.aspect_invalid'),
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
      title={camera.uid ? t('settings.cameras.edit') : t('settings.cameras.add')}
      footer={
        <>
          {errors.length > 0 && <span className="mr-auto text-[12.5px] text-ink-3">{errors[0]}</span>}
          <button className="btn btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button className="btn btn-primary" disabled={errors.length > 0 || saving} onClick={save}>
            {saving && <Spinner size={13} />}
            {camera.uid ? t('common.save') : t('common.add')}
          </button>
        </>
      }
    >
      <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="grid content-start divide-y divide-line">
          <Row title={t('settings.cameras.enable')}>
            <Toggle checked={c.enabled} onChange={(v) => set('enabled', v)} label={t('settings.cameras.enable')} />
          </Row>
          <Row title={t('settings.cameras.name')} wide>
            <input className="field" value={c.name} onChange={(e) => set('name', e.target.value)} aria-label={t('settings.cameras.name')} autoFocus={!camera.uid} />
          </Row>
          <Row title={t('settings.cameras.stream_type')} wide>
            <select className="field" value={c.service} onChange={(e) => set('service', e.target.value)} aria-label={t('settings.cameras.stream_type')}>
              {serviceList.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
              {!serviceList.some((s) => s.value === c.service) && <option value={c.service}>{c.service}</option>}
            </select>
          </Row>
          <Row title={t('settings.cameras.stream_url')} wide>
            <input className="field code" type="url" spellCheck={false} value={c.stream_url} onChange={(e) => set('stream_url', e.target.value)} aria-label={t('settings.cameras.stream_url')} />
          </Row>
          <Row title={t('settings.cameras.snapshot_url')} wide>
            <input className="field code" type="url" spellCheck={false} value={c.snapshot_url} onChange={(e) => set('snapshot_url', e.target.value)} aria-label={t('settings.cameras.snapshot_url')} />
          </Row>
          {c.service === 'mjpegstreamer-adaptive' && (
            <>
              <Row title={t('settings.cameras.fps')} wide>
                <NumberInput value={c.target_fps} onChange={(v) => set('target_fps', v)} label={t('settings.cameras.fps')} />
              </Row>
              <Row title={t('settings.cameras.fps_idle')} wide>
                <NumberInput value={c.target_fps_idle} onChange={(v) => set('target_fps_idle', v)} label={t('settings.cameras.fps_idle')} />
              </Row>
            </>
          )}
          {c.service === 'iframe' && (
            <Row title={t('settings.cameras.aspect')} sub={t('settings.cameras.aspect_sub')} wide>
              <TextField value={c.aspect_ratio ?? ''} onSave={(v) => set('aspect_ratio', v)} placeholder="16:9" label={t('settings.cameras.aspect')} />
            </Row>
          )}
          <Row title={t('settings.cameras.rotate')}>
            <Choices value={c.rotation} onChange={(v) => set('rotation', v)} options={[0, 90, 180, 270].map((r) => ({ value: r, label: r ? `${r}°` : t('settings.cameras.rotate_none') }))} />
          </Row>
          <Row title={t('settings.cameras.flip_h')}>
            <Toggle checked={c.flip_horizontal} onChange={(v) => set('flip_horizontal', v)} label={t('settings.cameras.flip_h')} />
          </Row>
          <Row title={t('settings.cameras.flip_v')}>
            <Toggle checked={c.flip_vertical} onChange={(v) => set('flip_vertical', v)} label={t('settings.cameras.flip_v')} />
          </Row>
        </div>
        <div className="grid content-start gap-2">
          <div className="section-label">{t('settings.cameras.preview')}</div>
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
  if (failed) body = <Unavailable text={t('settings.cameras.no_answer')} />
  else if (['mjpegstreamer', 'uv4l-mjpeg', 'mjpegstreamer-adaptive'].includes(cam.service))
    body = <img src={cam.service === 'mjpegstreamer-adaptive' ? withCacheBust(url, tick) : url} alt="" className="absolute inset-0 h-full w-full object-contain" style={style} onError={() => setFailed(true)} />
  else if (cam.service === 'ipstream') body = <video src={url} autoPlay muted playsInline className="absolute inset-0 h-full w-full object-contain" style={style} onError={() => setFailed(true)} />
  else if (cam.service === 'iframe') body = <iframe src={url} title={t('settings.cameras.preview_title')} className="absolute inset-0 h-full w-full border-0" style={style} />
  else body = <Unavailable text={t('settings.cameras.cant_preview')} />
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

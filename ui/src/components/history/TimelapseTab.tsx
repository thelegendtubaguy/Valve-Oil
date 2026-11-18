import { useEffect, useState, type ReactNode } from 'react'
import { Archive, Download, Ellipsis, Film, Image as ImageIcon, Pencil, Play, Settings2, Trash2, Video } from 'lucide-react'
import { Card, Dialog, Empty, MenuButton, Spinner, Toggle, confirm } from '../ui'
import { href } from '../../lib/router'
import { attempt, toast } from '../toast'
import { client } from '../../state/printer'
import { postTimelapseSettings, renderTimelapse, saveTimelapseFrames, useTimelapse, useTimelapseFiles } from '../../state/history'
import { fmtDateTime, fmtSize, frameFile, isBlocked, lastFrame, renderLength, TIMELAPSE_DEFAULTS, timelapseItems, type TimelapseItem, type TimelapseSettings } from './logic'

const MANAGED = 'Managed by your Moonraker configuration'

/** Fluidd's Timelapse page: the rendered videos, the current print's frames and render, and the settings. */
export function TimelapseTab() {
  const [render, setRender] = useState<null | { renderable: boolean }>(null)
  return (
    <div className="grid items-start gap-3.5 lg:grid-cols-[minmax(0,1fr)_minmax(320px,400px)]">
      <div className="grid gap-3.5 lg:order-2">
        <StatusCard onRender={() => setRender({ renderable: true })} />
        <SettingsCard onRenderSettings={() => setRender({ renderable: false })} />
      </div>
      <FilesCard />
      {render && <RenderSettingsDialog renderable={render.renderable} onClose={() => setRender(null)} />}
    </div>
  )
}

const settingsOrDefaults = (s: TimelapseSettings | null) => s ?? (TIMELAPSE_DEFAULTS as TimelapseSettings)

// ---------------------------------------------------------------------------------------------------------------

/** Fluidd's Timelapse Status card: the frames taken so far, a frame slider, Save frames, and Render. */
function StatusCard({ onRender }: { onRender: () => void }) {
  const info = useTimelapse((s) => s.lastFrame)
  const render = useTimelapse((s) => s.render)
  const frame = lastFrame(info)
  const count = frame?.uniqueCount ?? 0
  const [picked, setPicked] = useState(0)
  const [saving, setSaving] = useState(false)
  const selected = picked && picked <= count ? picked : count
  const rendering = !!render && render.status !== 'success'
  const progress = render?.status === 'running' ? render.progress : 0
  const url = frame?.file && selected ? client.fileUrl('timelapse_frames', frameFile(frame.file, selected)) : null

  const save = async () => {
    setSaving(true)
    const r = await attempt(saveTimelapseFrames)
    if (r?.status === 'finished' && r.zipfile) toast(`Saved the frames to ${r.zipfile.split('/').pop()}`)
    else if (r?.status === 'skipped') toast('No frames to save.', 'error')
    setSaving(false)
  }

  return (
    <Card title="Timelapse status" icon={<Film size={14} />} aside={count ? `${count} ${count === 1 ? 'frame' : 'frames'}` : undefined}>
      <div className="relative grid aspect-video place-items-center overflow-hidden rounded-xl bg-inset" style={{ boxShadow: 'inset 0 0 0 1px var(--line)' }}>
        {url ? (
          <img src={url} alt={`Frame ${selected}`} className="h-full w-full object-contain" style={{ filter: rendering ? `saturate(${progress}%)` : undefined }} />
        ) : (
          <span className="px-6 text-center text-ink-3">No frames yet. Frames are taken during a print while timelapse is enabled.</span>
        )}
        {rendering && (
          <span className="absolute inset-0 grid place-items-center bg-black/35">
            <Ring value={progress} />
          </span>
        )}
      </div>
      <div className="mt-3 flex items-center gap-3">
        <span className="lbl w-[52px]">Frame</span>
        <input
          type="range"
          className="min-w-0 flex-1 accent-[var(--accent)]"
          min={1}
          max={Math.max(1, count)}
          value={selected || 1}
          disabled={!count || rendering}
          onChange={(e) => setPicked(Number(e.target.value) === count ? 0 : Number(e.target.value))}
          aria-label="Frame"
        />
        <span className="num w-[78px] text-right text-[12.5px] font-semibold text-ink-2">
          {selected || 0} / {count}
        </span>
      </div>
      {render?.status === 'started' && <div className="mt-2 text-[12.5px] text-ink-3">{render.msg}</div>}
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        <button className="btn" disabled={!count || saving} onClick={save}>
          {saving ? <Spinner size={14} /> : <Archive size={15} />} Save frames
        </button>
        <button className="btn btn-primary" disabled={!count || rendering} onClick={onRender}>
          <Play size={15} /> Render
        </button>
      </div>
    </Card>
  )
}

function Ring({ value }: { value: number }) {
  const r = 26
  const c = 2 * Math.PI * r
  return (
    <svg width={68} height={68} viewBox="0 0 68 68" role="img" aria-label={`Rendering, ${Math.round(value)}%`}>
      <circle cx={34} cy={34} r={r} fill="none" stroke="rgb(255 255 255 / 0.25)" strokeWidth={5} />
      <circle cx={34} cy={34} r={r} fill="none" stroke="var(--accent)" strokeWidth={5} strokeLinecap="round" strokeDasharray={`${(value / 100) * c} ${c}`} transform="rotate(-90 34 34)" />
      <text x={34} y={39} textAnchor="middle" fill="#fff" fontSize={14} fontWeight={700}>
        {Math.round(value)}%
      </text>
    </svg>
  )
}

// ---------------------------------------------------------------------------------------------------------------

function Row({ title, sub, blocked, children }: { title: ReactNode; sub?: ReactNode; blocked?: boolean; children: ReactNode }) {
  return (
    <div className="flex min-h-[46px] items-center gap-3 border-b border-line py-2 last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="font-semibold">{title}</div>
        {(blocked || sub) && <div className="text-[12px] text-ink-3">{blocked ? MANAGED : sub}</div>}
      </div>
      <div className="flex flex-none items-center">{children}</div>
    </div>
  )
}

function post(settings: Partial<TimelapseSettings>) {
  return attempt(() => postTimelapseSettings(settings))
}

/** Fluidd's Timelapse Settings card. Camera, mode, and parking are on the Settings screen, as in Fluidd. */
function SettingsCard({ onRenderSettings }: { onRenderSettings: () => void }) {
  const raw = useTimelapse((s) => s.settings)
  const s = settingsOrDefaults(raw)
  return (
    <Card title="Timelapse settings" icon={<Settings2 size={14} />}>
      <Row title="Enabled" blocked={isBlocked(raw, 'enabled') && !!raw}>
        <Toggle checked={s.enabled} disabled={isBlocked(raw, 'enabled')} onChange={(v) => post({ enabled: v })} label="Enabled" />
      </Row>
      <Row title="Auto render" blocked={isBlocked(raw, 'autorender') && !!raw}>
        <Toggle checked={s.autorender} disabled={isBlocked(raw, 'autorender')} onChange={(v) => post({ autorender: v })} label="Auto render" />
      </Row>
      <Row title="Render settings">
        <button className="btn btn-sm" onClick={onRenderSettings} disabled={!raw}>
          <Pencil size={13} /> Edit
        </button>
      </Row>
      <Row title="Camera, mode, and parking">
        <a className="btn btn-sm" href={href('settings', 'timelapse')}>
          <Settings2 size={13} /> Settings
        </a>
      </Row>
    </Card>
  )
}

/** A number setting that saves on Enter or leaving the field, when it's valid. */
function NumField({ value, onSave, min = 0, max, above, unit, disabled, step }: { value: number; onSave: (v: number) => void; min?: number; max?: number; above?: number; unit?: string; disabled?: boolean; step?: number }) {
  const [draft, setDraft] = useState<string | null>(null)
  const text = draft ?? String(value)
  const n = Number(text)
  const valid = text.trim() !== '' && Number.isFinite(n) && n >= min && (max == null || n <= max) && (above == null || n > above)
  const commit = () => {
    if (draft != null && valid && n !== value) onSave(n)
    setDraft(null)
  }
  return (
    <span className="relative inline-block w-[132px]">
      <input
        className="field num text-right"
        inputMode="decimal"
        value={text}
        step={step}
        disabled={disabled}
        aria-invalid={!valid}
        style={{ paddingRight: unit ? 16 + unit.length * 7.5 : undefined, ...(valid ? {} : { boxShadow: 'inset 0 0 0 1.5px var(--crimson)' }) }}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
          if (e.key === 'Escape') setDraft(null)
        }}
      />
      {unit && <span className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-[12px] font-semibold text-ink-3">{unit}</span>}
    </span>
  )
}

/** Fluidd's Render Settings dialog; opened from Render, it ends with Render and the estimated length. */
function RenderSettingsDialog({ renderable, onClose }: { renderable: boolean; onClose: () => void }) {
  const raw = useTimelapse((s) => s.settings)
  const info = useTimelapse((s) => s.lastFrame)
  const s = settingsOrDefaults(raw)
  const b = (k: string) => isBlocked(raw, k)
  const frames = lastFrame(info)?.count ?? 0
  const go = async () => {
    onClose()
    await attempt(renderTimelapse)
  }
  return (
    <Dialog
      open
      onClose={onClose}
      title="Render settings"
      footer={
        renderable ? (
          <>
            <span className="mr-auto text-[12.5px] text-ink-3">Estimated length: {renderLength(frames, s)}</span>
            <button className="btn btn-primary" onClick={go}>
              <Play size={15} /> Render
            </button>
          </>
        ) : undefined
      }
    >
      <Row title="Variable FPS" blocked={b('variable_fps')}>
        <Toggle checked={s.variable_fps} disabled={b('variable_fps')} onChange={(v) => post({ variable_fps: v })} label="Variable FPS" />
      </Row>
      {!s.variable_fps ? (
        <Row title="Output framerate" blocked={b('output_framerate')}>
          <NumField value={s.output_framerate} unit="fps" disabled={b('output_framerate')} onSave={(v) => post({ output_framerate: v })} />
        </Row>
      ) : (
        <>
          <Row title="Target length" blocked={b('targetlength')}>
            <NumField value={s.targetlength} unit="s" disabled={b('targetlength')} onSave={(v) => post({ targetlength: v })} />
          </Row>
          <Row title="Minimum framerate" blocked={b('variable_fps_min')}>
            <NumField value={s.variable_fps_min} unit="fps" disabled={b('variable_fps_min')} onSave={(v) => post({ variable_fps_min: v })} />
          </Row>
          <Row title="Maximum framerate" blocked={b('variable_fps_max')}>
            <NumField value={s.variable_fps_max} unit="fps" disabled={b('variable_fps_max')} onSave={(v) => post({ variable_fps_max: v })} />
          </Row>
        </>
      )}
      <Row title="Save frames" blocked={b('saveframes')}>
        <Toggle checked={s.saveframes} disabled={b('saveframes')} onChange={(v) => post({ saveframes: v })} label="Save frames" />
      </Row>
      <Row title="Duplicate last frames" blocked={b('duplicatelastframe')}>
        <NumField value={s.duplicatelastframe} unit={s.duplicatelastframe === 1 ? 'frame' : 'frames'} disabled={b('duplicatelastframe')} onSave={(v) => post({ duplicatelastframe: v })} />
      </Row>
      <Row
        title={
          <span className="flex items-center gap-2">
            Constant rate factor
            {s.constant_rate_factor !== TIMELAPSE_DEFAULTS.constant_rate_factor && !b('constant_rate_factor') && (
              <button className="btn btn-sm btn-ghost !px-1.5 !py-0 text-[12px]" onClick={() => post({ constant_rate_factor: TIMELAPSE_DEFAULTS.constant_rate_factor })}>
                Reset
              </button>
            )}
          </span>
        }
        sub="Lower is better quality and a bigger file"
        blocked={b('constant_rate_factor')}
      >
        <span className="flex items-center gap-2.5">
          <CrfSlider value={s.constant_rate_factor} disabled={b('constant_rate_factor')} />
        </span>
      </Row>
      <Row title="Generate thumbnail" blocked={b('previewimage')}>
        <Toggle checked={s.previewimage} disabled={b('previewimage')} onChange={(v) => post({ previewimage: v })} label="Generate thumbnail" />
      </Row>
    </Dialog>
  )
}

function CrfSlider({ value, disabled }: { value: number; disabled: boolean }) {
  const [v, setV] = useState(value)
  useEffect(() => setV(value), [value])
  return (
    <>
      <input type="range" min={0} max={51} value={v} disabled={disabled} className="w-[120px] accent-[var(--accent)] sm:w-[160px]" aria-label="Constant rate factor" onChange={(e) => setV(Number(e.target.value))} onPointerUp={() => v !== value && post({ constant_rate_factor: v })} onKeyUp={() => v !== value && post({ constant_rate_factor: v })} />
      <span className="num w-6 text-right font-semibold">{v}</span>
    </>
  )
}

// ---------------------------------------------------------------------------------------------------------------

/** The timelapse folder: each video with its thumbnail, to play, download, or delete. */
function FilesCard() {
  const files = useTimelapseFiles()
  const [open, setOpen] = useState<TimelapseItem | null>(null)
  const items = files ? timelapseItems(files) : null
  const videos = items?.filter((i) => i.kind === 'video').length ?? 0
  return (
    <Card title="Timelapses" icon={<Video size={14} />} aside={items ? `${videos} ${videos === 1 ? 'video' : 'videos'}` : undefined}>
      {!items ? (
        <div className="grid place-items-center py-10">
          <Spinner size={22} />
        </div>
      ) : items.length === 0 ? (
        <Empty>No timelapses yet. With timelapse enabled, each print’s video appears here after it renders.</Empty>
      ) : (
        <div className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 xl:grid-cols-3">
          {items.map((it) => (
            <FileTile key={it.key} item={it} onOpen={() => (it.kind === 'archive' || it.kind === 'other' ? download(it.file.path) : setOpen(it))} />
          ))}
        </div>
      )}
      {open && <PlayerDialog item={open} onClose={() => setOpen(null)} />}
    </Card>
  )
}

const fileUrl = (path: string) => client.fileUrl('timelapse', path)

function download(path: string) {
  const a = document.createElement('a')
  a.href = fileUrl(path)
  a.download = path.split('/').pop() ?? path
  a.click()
}

async function remove(item: TimelapseItem) {
  const paired = item.kind === 'video' && item.poster ? item.poster : null
  const ok = await confirm({ title: 'Delete this timelapse?', body: `${item.file.path}${paired ? ' and its thumbnail' : ''} will be deleted.`, confirm: 'Delete', danger: true })
  if (!ok) return false
  await attempt(async () => {
    await client.call('server.files.delete_file', { path: `timelapse/${item.file.path}` })
    if (paired) await client.call('server.files.delete_file', { path: `timelapse/${paired.path}` }).catch(() => {})
  })
  return true
}

function FileTile({ item, onOpen }: { item: TimelapseItem; onOpen: () => void }) {
  const [broken, setBroken] = useState(false)
  const Icon = item.kind === 'archive' ? Archive : item.kind === 'image' ? ImageIcon : Film
  return (
    <div className="overflow-hidden rounded-xl bg-inset" style={{ boxShadow: 'inset 0 0 0 1px var(--line)' }}>
      <button className="group relative grid aspect-video w-full place-items-center border-0 bg-black/20 p-0 text-ink-3" onClick={onOpen} aria-label={item.kind === 'video' ? `Play ${item.title}` : `Open ${item.title}`}>
        {item.poster && !broken ? <img src={fileUrl(item.poster.path)} alt="" loading="lazy" className="h-full w-full object-cover" onError={() => setBroken(true)} /> : <Icon size={30} />}
        {item.kind === 'video' && (
          <span className="absolute grid h-11 w-11 place-items-center rounded-full bg-black/55 text-white transition group-hover:scale-110 group-hover:bg-black/70">
            <Play size={20} className="ml-0.5" fill="currentColor" />
          </span>
        )}
      </button>
      <div className="flex items-start gap-2 p-2.5">
        <div className="min-w-0 flex-1">
          <div className="truncate font-semibold" title={item.file.path}>
            {item.title}
          </div>
          <div className="text-[12px] text-ink-3">
            {fmtDateTime(item.when ?? item.file.modified)} · {fmtSize(item.file.size)}
            {item.kind === 'archive' ? ' · frames' : item.kind === 'image' ? ' · image' : ''}
          </div>
        </div>
        <MenuButton
          button={<Ellipsis size={16} />}
          label="Timelapse actions"
          items={[
            { label: 'Download', icon: <Download size={15} />, onSelect: () => download(item.file.path) },
            { label: 'Delete', icon: <Trash2 size={15} />, danger: true, onSelect: () => remove(item) },
          ]}
        />
      </div>
    </div>
  )
}

function PlayerDialog({ item, onClose }: { item: TimelapseItem; onClose: () => void }) {
  return (
    <Dialog
      open
      onClose={onClose}
      size="wide"
      title={item.title}
      footer={
        <>
          <span className="mr-auto text-[12.5px] text-ink-3">
            {fmtDateTime(item.when ?? item.file.modified)} · {fmtSize(item.file.size)}
          </span>
          <button className="btn btn-danger" onClick={() => remove(item).then((gone) => gone && onClose())}>
            <Trash2 size={15} /> Delete
          </button>
          <a className="btn" href={fileUrl(item.file.path)} download={item.file.path.split('/').pop()}>
            <Download size={15} /> Download
          </a>
        </>
      }
    >
      {item.kind === 'video' ? (
        <video key={item.file.path} className="block max-h-[70dvh] w-full rounded-lg bg-black" src={fileUrl(item.file.path)} poster={item.poster ? fileUrl(item.poster.path) : undefined} controls autoPlay playsInline />
      ) : (
        <img className="block max-h-[70dvh] w-full rounded-lg bg-black object-contain" src={fileUrl(item.file.path)} alt={item.title} />
      )}
    </Dialog>
  )
}

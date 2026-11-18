import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { Check, Minus, Plus, Scan, ScanSearch, Settings2 } from 'lucide-react'
import { Card, Choices, MenuButton, SectionLabel, Toggle, confirm } from '../ui'
import { attempt } from '../toast'
import { runGcode } from '../../state/printer'
import { encodeGcodeParamValue } from '../control/gcode'
import { PreviewCanvas, type PreviewCanvasHandle } from './PreviewCanvas'
import { usePreviewModel, usePrintFollow } from './hooks'
import { fanStops, layerByFilePosition, moveIndexByFilePosition, roleNames, speedStops } from './model'
import { ExtrusionRole, type ColorMode, type ParsedGcode } from './types'
import type { PreviewSettings } from './settings'
import type { FileMeta } from '../../state/files'
import './preview.css'

type Props = {
  path: string
  meta: FileMeta | null | undefined
  data: ParsedGcode
  settings: PreviewSettings
  set: <K extends keyof PreviewSettings>(key: K, value: PreviewSettings[K]) => void
}

const COLOR_MODES: { value: ColorMode; label: string }[] = [
  { value: 'tool', label: 'Tool' },
  { value: 'role', label: 'Feature type' },
  { value: 'speed', label: 'Speed' },
  { value: 'fan', label: 'Fan speed' },
]

/** A slider with − and + steppers either side. */
export function RangeStepper({ value, min, max, onChange, label, disabled }: { value: number; min: number; max: number; onChange: (v: number) => void; label: string; disabled?: boolean }) {
  const pct = max > min ? ((value - min) / (max - min)) * 100 : 0
  const clamp = (v: number) => Math.min(max, Math.max(min, v))
  return (
    <div className="flex items-center gap-2">
      <button type="button" className="pm" disabled={disabled || value <= min} onClick={() => onChange(clamp(value - 1))} aria-label={`Previous ${label.toLowerCase()}`}>
        <Minus size={16} />
      </button>
      <input
        type="range"
        className="gp-range min-w-0 flex-1"
        style={{ ['--pct' as string]: `${pct}%` }}
        min={min}
        max={max}
        step={1}
        value={value}
        disabled={disabled || max <= min}
        aria-label={label}
        onChange={(e) => onChange(clamp(Number(e.target.value)))}
      />
      <button type="button" className="pm" disabled={disabled || value >= max} onClick={() => onChange(clamp(value + 1))} aria-label={`Next ${label.toLowerCase()}`}>
        <Plus size={16} />
      </button>
    </div>
  )
}

export function GcodeViewer({ path, meta, data, settings, set }: Props) {
  const { layers, bounds, toolColors, colors, bed, isDark } = usePreviewModel(meta, data, settings)
  const print = usePrintFollow(path)
  const view = useRef<PreviewCanvasHandle>(null)
  const moves = data.moves
  const follow = settings.followProgress && print.isPrinterFile

  const [picked, setPicked] = useState({ layer: 0, move: 0 })
  const followed = useMemo(() => {
    if (!follow || !layers.length) return null
    return { layer: layerByFilePosition(layers, print.filePosition), move: moveIndexByFilePosition(moves, print.filePosition) }
  }, [follow, layers, moves, print.filePosition])
  const at = followed ?? picked
  const layer = Math.min(at.layer, Math.max(0, layers.length - 1))
  const range = useMemo(() => ({ min: layers[layer]?.move ?? 0, max: layers[layer + 1]?.move ?? Math.max(0, moves.length - 1) }), [layers, layer, moves.length])
  const moveEnd = Math.min(Math.max(at.move, range.min), range.max)

  // A new file starts on its first layer, drawn whole, and follows the print if Fluidd's option says so.
  const loadedFor = useRef<ParsedGcode | null>(null)
  useEffect(() => {
    if (loadedFor.current === data) return
    loadedFor.current = data
    setPicked({ layer: 0, move: layers[1]?.move ?? Math.max(0, moves.length - 1) })
    if (settings.autoFollowOnFileLoad && print.isPrinterFile && !settings.followProgress) set('followProgress', true)
  }, [data, layers, moves.length, settings.autoFollowOnFileLoad, settings.followProgress, print.isPrinterFile, set])

  // Picking a layer or move by hand stops following, as in Fluidd.
  const pickLayer = useCallback(
    (l: number) => {
      if (follow) set('followProgress', false)
      setPicked({ layer: l, move: layers[l + 1]?.move ?? Math.max(0, moves.length - 1) })
    },
    [follow, set, layers, moves.length],
  )
  const pickMove = useCallback(
    (m: number) => {
      if (follow) set('followProgress', false)
      setPicked({ layer, move: m })
    },
    [follow, set, layer],
  )
  const setFollow = (on: boolean) => {
    if (!on && followed) setPicked(followed)
    set('followProgress', on)
  }

  const onKey = (e: KeyboardEvent) => {
    const k = e.key
    if (k === 'ArrowUp' || k === 'PageUp') pickLayer(Math.min(layers.length - 1, layer + 1))
    else if (k === 'ArrowDown' || k === 'PageDown') pickLayer(Math.max(0, layer - 1))
    else if (k === 'ArrowRight') pickMove(Math.min(range.max, moveEnd + 1))
    else if (k === 'ArrowLeft') pickMove(Math.max(range.min, moveEnd - 1))
    else return
    e.preventDefault()
  }

  const exclude = useCallback(async (name: string) => {
    const ok = await confirm({ title: 'Exclude object', body: <>Are you sure you want to exclude <b>{name}</b> from printing?</>, confirm: 'Exclude', danger: true })
    if (ok) attempt(() => runGcode(`EXCLUDE_OBJECT NAME=${encodeGcodeParamValue(name.toUpperCase().replace(/\s/g, '_'))}`))
  }, [])

  // Fluidd hides a lone part's outline when asked to.
  const effective = useMemo(
    () => (settings.hideSinglePartBoundingBox && data.parts.length <= 1 && settings.showParts ? { ...settings, showParts: false } : settings),
    [settings, data.parts.length],
  )
  const toolhead = useMemo(() => (follow && print.x != null && print.y != null ? { x: print.x, y: print.y } : null), [follow, print.x, print.y])
  const z = layers[layer]?.z ?? 0
  const currentTool = moves.length ? `T${moves.tool[Math.min(moveEnd, moves.length - 1)]}` : 'T0'

  return (
    <div className="grid gap-3.5 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="gp-view" tabIndex={0} onKeyDown={onKey} aria-label="Layer view. Up and down arrows change the layer, left and right step through its moves.">
        <PreviewCanvas
          ref={view}
          className="absolute inset-0"
          data={data}
          layers={layers}
          bounds={bounds}
          bed={bed}
          layer={layer}
          moveEnd={moveEnd}
          settings={effective}
          colors={colors}
          isDark={isDark}
          objects={print.objects}
          onExclude={print.objects ? exclude : undefined}
          toolhead={toolhead}
          fitPrint={settings.autoZoom}
        />
        <div className="gp-tools">
          <button className="iconbtn" title={settings.autoZoom ? 'Fit the bed' : 'Fit the print'} aria-label={settings.autoZoom ? 'Fit the bed' : 'Fit the print'} onClick={() => set('autoZoom', !settings.autoZoom)}>
            {settings.autoZoom ? <Scan size={18} /> : <ScanSearch size={18} />}
          </button>
          <button className="iconbtn" title="Zoom in" aria-label="Zoom in" onClick={() => view.current?.zoom(1.5)}>
            <Plus size={18} />
          </button>
          <button className="iconbtn" title="Zoom out" aria-label="Zoom out" onClick={() => view.current?.zoom(1 / 1.5)}>
            <Minus size={18} />
          </button>
          <MenuButton
            label="View options"
            button={<Settings2 size={18} />}
            items={[
              { label: 'Flip horizontally', icon: <Mark on={settings.flip.horizontal} />, onSelect: () => set('flip', { ...settings.flip, horizontal: !settings.flip.horizontal }) },
              { label: 'Flip vertically', icon: <Mark on={settings.flip.vertical} />, onSelect: () => set('flip', { ...settings.flip, vertical: !settings.flip.vertical }) },
              { label: 'Draw origin', icon: <Mark on={settings.drawOrigin} />, onSelect: () => set('drawOrigin', !settings.drawOrigin) },
              { label: 'Draw background', icon: <Mark on={settings.drawBackground} />, onSelect: () => set('drawBackground', !settings.drawBackground) },
            ]}
          />
        </div>
        {layers.length > 0 && (
          <div className="gp-badge num">
            Layer {layer + 1} of {layers.length} <span className="text-ink-3">· Z {z.toFixed(2)} mm</span>
          </div>
        )}
      </div>

      <div className="grid min-w-0 content-start gap-3.5">
        <Card>
          <div className="flex items-end justify-between gap-3">
            <div>
              <SectionLabel>Layer</SectionLabel>
              <div className="mt-1 flex items-baseline gap-1.5">
                <span className="big num">{layers.length ? layer + 1 : 0}</span>
                <span className="font-semibold text-ink-3">/ {layers.length}</span>
              </div>
            </div>
            <div className="text-right">
              <SectionLabel>Layer height</SectionLabel>
              <div className="mt-1 text-[18px] font-bold num">
                {z.toFixed(2)} <small className="text-[12px] text-ink-3">mm</small>
              </div>
            </div>
          </div>
          <div className="mt-3">
            <RangeStepper label="Layer" value={layer} min={0} max={Math.max(0, layers.length - 1)} onChange={pickLayer} disabled={!layers.length} />
          </div>
          <SectionLabel className="mt-3">Progress</SectionLabel>
          <div className="mt-1">
            <RangeStepper label="Move" value={moveEnd} min={range.min} max={range.max} onChange={pickMove} disabled={!layers.length} />
          </div>
          {print.isPrinterFile && (
            <label className="mt-3 flex items-center justify-between gap-3 border-t border-line pt-3">
              <span>
                <span className="font-semibold">Follow progress</span>
                <span className="block text-[12px] text-ink-3">Show the layer and move the printer is on.</span>
              </span>
              <Toggle checked={settings.followProgress} onChange={setFollow} label="Follow progress" />
            </label>
          )}
          {data.truncated && <div className="mt-3 rounded-lg bg-warn-tint px-3 py-2 text-[12.5px] text-warn">G-code file is too large, only part of it is previewed.</div>}
        </Card>

        <Card title="Show">
          <div className="flex flex-wrap gap-1.5">
            <Pill on={settings.showPreviousLayer} onClick={() => set('showPreviousLayer', !settings.showPreviousLayer)}>
              Previous layer
            </Pill>
            <Pill on={settings.showCurrentLayer} onClick={() => set('showCurrentLayer', !settings.showCurrentLayer)}>
              Current layer
            </Pill>
            <Pill on={settings.showNextLayer} onClick={() => set('showNextLayer', !settings.showNextLayer)}>
              Next layer
            </Pill>
            <Pill on={settings.showExtrusions} onClick={() => set('showExtrusions', !settings.showExtrusions)}>
              Extrusions
            </Pill>
            <Pill on={settings.showMoves} onClick={() => set('showMoves', !settings.showMoves)}>
              Moves
            </Pill>
            <Pill on={settings.showRetractions} onClick={() => set('showRetractions', !settings.showRetractions)}>
              Retractions
            </Pill>
            <Pill on={settings.showParts} onClick={() => set('showParts', !settings.showParts)}>
              Parts
            </Pill>
          </div>
        </Card>

        <Card title="Color by">
          <Choices value={settings.colorMode} options={COLOR_MODES} onChange={(v) => set('colorMode', v)} />
          <Legend mode={settings.colorMode} data={data} colors={colors} toolColors={toolColors} currentTool={currentTool} />
        </Card>

        {print.objects && (
          <Card title="Objects" aside={`${print.objects.filter((o) => o.excluded).length} excluded`}>
            <div className="grid min-w-0 gap-1">
              {print.objects.map((o) => (
                <div key={o.name} className="flex min-w-0 items-center gap-2.5 py-1">
                  <span className="h-2.5 w-2.5 flex-none rounded-full" style={{ background: o.excluded ? 'var(--red)' : o.current ? 'var(--blue)' : 'var(--green)' }} />
                  <span className={`min-w-0 flex-1 truncate ${o.excluded ? 'text-ink-3 line-through' : ''}`} title={o.name}>
                    {o.name}
                  </span>
                  {o.current && !o.excluded && <span className="pill tone-info">Printing</span>}
                  {o.excluded ? (
                    <span className="pill tone-bad">Excluded</span>
                  ) : (
                    <button className="btn btn-sm btn-danger" onClick={() => exclude(o.name)}>
                      Exclude
                    </button>
                  )}
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
    </div>
  )
}

function Mark({ on }: { on: boolean }) {
  return <Check size={15} className={on ? 'text-accent-ink' : 'opacity-0'} />
}

function Pill({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" className={`choice ${on ? 'on' : ''}`} aria-pressed={on} onClick={onClick}>
      {children}
    </button>
  )
}

function Legend({ mode, data, colors, toolColors, currentTool }: { mode: ColorMode; data: ParsedGcode; colors: Record<string, string>; toolColors: Record<string, string>; currentTool: string }) {
  let items: { key: string; label: ReactNode; color: string; active?: boolean }[] = []
  let caption = ''
  if (mode === 'role') {
    // Fluidd leaves the legend out when the slicer marked no features.
    const roles = data.roles.some((r) => r !== ExtrusionRole.None) ? data.roles : []
    items = roles.map((r) => ({ key: `R${r}`, label: roleNames[r], color: colors[`R${r}`] }))
    if (!items.length) caption = 'This file marks no feature types.'
  } else if (mode === 'speed') {
    items = speedStops(data.speedRange).map((s) => ({ key: s.color, label: s.value.toFixed(1), color: s.color }))
    caption = 'mm/s'
  } else if (mode === 'fan') {
    items = fanStops(data.fanRange).map((s) => ({ key: s.color, label: Math.round(s.value), color: s.color }))
    caption = '%'
  } else if (data.tools.length > 0) {
    items = Object.entries(toolColors).map(([tool, color]) => ({ key: tool, label: tool, color, active: tool === currentTool }))
  }
  if (!items.length && !caption) return null
  return (
    <div className="mt-3 border-t border-line pt-3">
      {caption && <div className="mb-1.5 text-[12px] text-ink-3">{caption}</div>}
      <div className={mode === 'speed' || mode === 'fan' ? 'flex flex-wrap gap-x-3 gap-y-1.5' : 'grid grid-cols-2 gap-x-3 gap-y-1.5'}>
        {items.map((it) => (
          <span key={it.key} className={`flex min-w-0 items-center gap-2 text-[12.5px] num ${it.active ? 'font-bold text-ink' : 'text-ink-2'}`}>
            <i className="swatch" style={{ background: it.color, ...(it.active ? { boxShadow: '0 0 0 2px var(--accent)' } : {}) }} />
            <span className="truncate">{it.label}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

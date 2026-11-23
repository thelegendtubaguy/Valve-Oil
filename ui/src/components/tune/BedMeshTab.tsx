import { useMemo, useRef, useState, type ReactNode } from 'react'
import { Download, FolderOpen, Grid3x3, House, RotateCcw, Trash2, TriangleAlert } from 'lucide-react'
import { Card, Dialog, Segmented, Choices, Toggle, Empty, confirm } from '../ui'
import { attempt } from '../toast'
import { runGcode, useObject } from '../../state/printer'
import { useStore } from '../../lib/store'
import voProbeReportPoints, { type VoProbeReportPoint } from '../../qidi/vo-probe-report'
import { MeshSurface3D, type MeshSurfaceHandle } from './MeshSurface3D'
import { MeshHeatmap } from './MeshHeatmap'
import { SaveConfigNotice } from './SaveConfigNotice'
import { bedBounds, fmtMm, gcodeParam, MESH_GRADIENT, meshGrid, meshPreview, meshProfiles, meshScale, meshStats, meshView, type BedMeshStatus, type MeshMarker, type MeshView } from './mesh'
import { usePrinterFlags } from './hooks'
import { useMedia } from '../../lib/browser'
import { locale, t } from '../../i18n'
import { tr } from '../../i18n/rich'

const pointName = (p: { x: number; y: number }) => `X${Math.round(p.x)} Y${Math.round(p.y)}`
const bold = (c: ReactNode) => <b className="text-ink-2">{c}</b>

export function BedMeshTab() {
  return (
    <div className="grid items-start gap-3.5 xl:grid-cols-[minmax(0,1fr)_380px]">
      <MeshCard />
      <div className="grid content-start gap-3.5">
        <SaveConfigNotice only={(s) => s.startsWith('bed_mesh')} />
        <ProfilesCard />
      </div>
    </div>
  )
}

function useProblemPoints() {
  return voProbeReportPoints(useObject('vo_probe_report'))
}

function MeshCard() {
  const bedMesh = useObject('bed_mesh') as BedMeshStatus
  const toolhead = useObject('toolhead')
  const report = useObject('vo_probe_report')
  const preview = useStore(meshPreview, (p) => p)
  const view = useStore(meshView, (v) => v)
  const setView = (v: Partial<MeshView>) => meshView.set((s) => ({ ...s, ...v }))
  const problems = useProblemPoints()
  const wide = useMedia('(min-width: 768px)')
  const surface = useRef<MeshSurfaceHandle>(null)

  const grid = useMemo(
    () => (preview ? meshGrid(preview.matrix, preview.mesh_min, preview.mesh_max) : meshGrid(bedMesh[view.matrix], bedMesh.mesh_min, bedMesh.mesh_max)),
    [preview, bedMesh, view.matrix],
  )
  const stats = useMemo(() => (grid ? meshStats(grid.z) : null), [grid])
  const bed = useMemo(() => (grid ? bedBounds(toolhead.axis_minimum as number[] | undefined, toolhead.axis_maximum as number[] | undefined, grid) : null), [grid, toolhead.axis_minimum, toolhead.axis_maximum])
  const scale = stats ? meshScale(stats, view.colorScale, view.boxScale, !!preview) : null
  const markers: MeshMarker[] = preview ? preview.markers : problems.map((p) => ({ x: p.x, y: p.y, label: t('tune.mesh.point_tries', { point: pointName(p), count: p.tries }) }))
  const unit = preview ? 'µm' : 'mm'
  const height = wide ? 460 : 300

  return (
    <Card
      title={t('tune.mesh.title')}
      icon={<Grid3x3 size={14} />}
      aside={
        <span className="flex items-center justify-end gap-1.5">
          {!preview && problems.length > 0 && <ProblemChip count={problems.length} />}
          {!preview && bedMesh.profile_name && <span className="pill tone-accent">{bedMesh.profile_name}</span>}
        </span>
      }
    >
      {preview && (
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded-lg bg-inset px-3 py-2">
          <span className="min-w-0 flex-1 text-ink-2">{t('tune.mesh.showing', { title: preview.title })}</span>
          <button className="btn btn-sm" onClick={() => meshPreview.set(null)}>
            {t('tune.mesh.show_loaded')}
          </button>
        </div>
      )}
      {grid && stats && bed && scale ? (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Segmented
              label={t('tune.mesh.view')}
              value={view.mode}
              onChange={(mode) => setView({ mode })}
              options={[
                { value: '3d', label: '3D' },
                { value: 'map', label: t('tune.mesh.heat_map') },
              ]}
            />
            {!preview && (
              <Segmented
                label={t('tune.mesh.matrix')}
                value={view.matrix}
                onChange={(matrix) => setView({ matrix })}
                options={[
                  { value: 'probed_matrix', label: t('tune.mesh.probed') },
                  { value: 'mesh_matrix', label: t('tune.mesh.interpolated') },
                ]}
              />
            )}
            <span className="flex-1" />
            {view.mode === '3d' && (
              <>
                <button className="iconbtn" title={t('tune.mesh.reset_view')} aria-label={t('tune.mesh.reset_view')} onClick={() => surface.current?.resetView()}>
                  <RotateCcw size={16} />
                </button>
                <button className="iconbtn" title={t('tune.mesh.download_image')} aria-label={t('tune.mesh.download_image')} onClick={() => surface.current?.download(['bedmesh', preview ? 'bed-soak' : bedMesh.profile_name].filter(Boolean).join('-'))}>
                  <Download size={16} />
                </button>
              </>
            )}
          </div>
          {view.mode === '3d' ? (
            <MeshSurface3D ref={surface} grid={grid} bed={bed} {...scale} wireframe={view.wireframe} flatAt={view.flat ? stats.mid : null} markers={markers} height={height} unit={unit} />
          ) : (
            <MeshHeatmap grid={grid} bed={bed} zMin={scale.zMin} zMax={scale.zMax} markers={markers} height={height} unit={unit} />
          )}
          <div className="mt-2 flex items-center gap-2 text-[12px] text-ink-3">
            <span className="num">{fmtZ(scale.zMin, unit)}</span>
            <div className="h-2 flex-1 rounded-full" style={{ background: MESH_GRADIENT }} />
            <span className="num">
              {fmtZ(scale.zMax, unit)} {unit}
            </span>
          </div>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Stat label={t('tune.shared.range')} value={fmtZ(stats.range, unit)} unit={unit} />
            <Stat label={t('tune.shared.lowest')} value={fmtZ(stats.min, unit)} unit={unit} />
            <Stat label={t('tune.shared.highest')} value={fmtZ(stats.max, unit)} unit={unit} />
            <Stat label={t('tune.shared.deviation')} value={fmtZ(stats.deviation, unit)} unit={unit} />
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2.5">
            <label className="flex items-center gap-2">
              <Toggle checked={view.wireframe} onChange={(wireframe) => setView({ wireframe })} disabled={view.mode !== '3d'} label={t('tune.mesh.wireframe')} />
              <span className={view.mode !== '3d' ? 'text-ink-3' : ''}>{t('tune.mesh.wireframe')}</span>
            </label>
            <label className="flex items-center gap-2">
              <Toggle checked={view.flat} onChange={(flat) => setView({ flat })} disabled={view.mode !== '3d'} label={t('tune.mesh.flat_plane')} />
              <span className={view.mode !== '3d' ? 'text-ink-3' : ''}>{t('tune.mesh.flat_plane')}</span>
            </label>
          </div>
          {!preview && (
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <span className="section-label">{t('tune.mesh.color_scale')}</span>
                <Choices
                  value={view.colorScale}
                  onChange={(colorScale) => setView({ colorScale })}
                  options={[
                    { value: 0, label: t('tune.mesh.fit') },
                    { value: 0.1, label: '0.1 mm' },
                    { value: 0.2, label: '0.2 mm' },
                  ]}
                />
              </div>
              <div className="grid gap-1.5">
                <span className="section-label">{t('tune.mesh.z_height')}</span>
                <Choices
                  value={view.boxScale}
                  disabled={view.mode !== '3d'}
                  onChange={(boxScale) => setView({ boxScale })}
                  options={[
                    { value: 'fit', label: t('tune.mesh.fit') },
                    { value: 1, label: '1 mm' },
                    { value: 1.5, label: '1.5 mm' },
                    { value: 2, label: '2 mm' },
                  ]}
                />
              </div>
            </div>
          )}
          {!preview && problems.length > 0 && <ProblemPoints points={problems} measured={report.measured as number | null | undefined} />}
          <p className="mt-3 mb-0 text-[12px] text-ink-3">{view.mode === '3d' ? t('tune.mesh.hint_3d') : t('tune.mesh.hint_map')}</p>
        </>
      ) : (
        <Empty>{t('tune.mesh.empty')}</Empty>
      )}
    </Card>
  )
}

function fmtZ(v: number, unit: 'mm' | 'µm') {
  return unit === 'µm' ? `${v < 0 ? '−' : ''}${Math.abs(v * 1000).toFixed(0)}` : fmtMm(v)
}

function Stat({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <div className="tile">
      <span className="section-label">{label}</span>
      <span className="v num">
        {value} <small>{unit}</small>
      </span>
    </div>
  )
}

function ProblemChip({ count, title }: { count: number; title?: string }) {
  return (
    <span className="chip" title={title}>
      <TriangleAlert size={12} className="text-accent-ink" />
      {t('tune.mesh.problem_points', { count })}
    </span>
  )
}

/** The fork's note under the Bed mesh card: retried points of the loaded mesh. */
function ProblemPoints({ points, measured }: { points: VoProbeReportPoint[]; measured?: number | null }) {
  const list = points.map((p) => t('tune.mesh.retry_item', { point: pointName(p), count: p.tries, um: Math.round(p.spread * 1000) })).join(', ')
  return (
    <div className="tone-warn mt-3 flex items-start gap-2.5 rounded-lg px-3 py-2.5">
      <TriangleAlert size={15} className="mt-0.5 flex-none" />
      <div className="text-ink">
        {t('tune.mesh.retries', { count: points.length, list })}
        {measured ? <div className="mt-0.5 text-[12px] text-ink-2">{t('tune.mesh.measured', { time: new Date(measured * 1000).toLocaleString(locale()) })}</div> : null}
      </div>
    </div>
  )
}

function ProfilesCard() {
  const bedMesh = useObject('bed_mesh') as BedMeshStatus
  const report = useObject('vo_probe_report')
  const problems = useProblemPoints()
  const { printing, busy, homed } = usePrinterFlags()
  const profiles = meshProfiles(bedMesh)
  const loaded = !!bedMesh.profile_name
  const [saveOpen, setSaveOpen] = useState(false)
  const run = (g: string) => attempt(() => runGcode(g))
  const problemTitle = problems.length
    ? report.measured
      ? t('tune.mesh.problem_points_measured', { count: problems.length, time: new Date((report.measured as number) * 1000).toLocaleString(locale()) })
      : t('tune.mesh.problem_points', { count: problems.length })
    : undefined

  const load = async (name: string) => {
    if (printing && !(await confirm({ title: t('tune.mesh.load_profile'), body: t('tune.mesh.load_body', { name }), confirm: t('tune.mesh.load') }))) return
    run(`BED_MESH_PROFILE LOAD=${gcodeParam(name)}`)
  }
  const remove = async (name: string) => {
    if (await confirm({ title: t('tune.mesh.remove_profile_named', { name }), body: t('tune.mesh.remove_body'), confirm: t('common.remove'), danger: true }))
      run(`BED_MESH_PROFILE REMOVE=${gcodeParam(name)}`)
  }
  const clear = async () => {
    if (printing && !(await confirm({ title: t('tune.mesh.clear_title'), body: t('tune.mesh.clear_body'), confirm: t('tune.mesh.clear'), danger: true }))) return
    run('BED_MESH_CLEAR')
  }

  return (
    <Card title={t('tune.mesh.profiles')} aside={profiles.length ? String(profiles.length) : undefined}>
      {profiles.length ? (
        <div className="-mx-2 overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>{t('tune.mesh.name')}</th>
                <th className="text-right">{t('tune.shared.range')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {profiles.map((p) => (
                <tr key={p.name}>
                  <td>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-semibold">{p.name}</span>
                      {p.active && <span className="pill tone-accent py-0.5!">{t('tune.mesh.active')}</span>}
                      {p.adaptive && <span className="chip">{t('tune.mesh.not_saved')}</span>}
                      {p.active && problems.length > 0 && (
                        <span className="chip" title={problemTitle}>
                          <TriangleAlert size={12} className="text-accent-ink" />
                          {problems.length}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="num text-right">{p.range.toFixed(4)}</td>
                  <td className="text-right whitespace-nowrap">
                    {!p.active && (
                      <button className="iconbtn inline-grid" title={t('tune.mesh.load_profile')} aria-label={t('tune.mesh.load_named', { name: p.name })} onClick={() => load(p.name)}>
                        <FolderOpen size={16} />
                      </button>
                    )}
                    <button className="iconbtn inline-grid" title={t('tune.mesh.remove_profile')} aria-label={t('tune.mesh.remove_named', { name: p.name })} disabled={p.adaptive || printing} onClick={() => remove(p.name)}>
                      <Trash2 size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>{t('tune.mesh.no_profiles')}</Empty>
      )}
      <div className="mt-3 grid grid-cols-2 gap-2">
        <button className={`btn ${homed ? '' : 'btn-primary'}`} disabled={busy} onClick={() => run('G28')}>
          <House size={15} /> {t('tune.shared.home_all')}
        </button>
        <button className="btn" disabled={busy || !homed} onClick={() => run('BED_MESH_CALIBRATE')} title={homed ? undefined : t('tune.shared.home_first')}>
          {t('tune.shared.calibrate')}
        </button>
        <button className="btn" disabled={!loaded} onClick={clear}>
          {t('tune.mesh.clear')}
        </button>
        <button className="btn btn-primary" disabled={!loaded || printing} onClick={() => setSaveOpen(true)}>
          {t('tune.mesh.save_as')}
        </button>
      </div>
      <ul className="mt-3 mb-0 grid gap-1 pl-4 text-[12.5px] text-ink-3">
        <li>{tr('tune.mesh.help_calibrate', {}, { b: bold })}</li>
        <li>{tr('tune.mesh.help_clear', {}, { b: bold })}</li>
        <li>{tr('tune.mesh.help_save_as', {}, { b: bold })}</li>
      </ul>
      {saveOpen && bedMesh.profile_name && (
        <SaveMeshDialog
          existing={bedMesh.profile_name}
          adaptive={!!profiles.find((p) => p.name === bedMesh.profile_name)?.adaptive}
          onClose={() => setSaveOpen(false)}
          onSave={(name, removeExisting) => {
            const lines: string[] = []
            if (name !== bedMesh.profile_name) lines.push(`BED_MESH_PROFILE SAVE=${gcodeParam(name)}`)
            if (removeExisting) lines.push(`BED_MESH_PROFILE REMOVE=${gcodeParam(bedMesh.profile_name!)}`)
            if (lines.length) run(lines.join('\n'))
            setSaveOpen(false)
          }}
        />
      )}
    </Card>
  )
}

function SaveMeshDialog({ existing, adaptive, onClose, onSave }: { existing: string; adaptive: boolean; onClose: () => void; onSave: (name: string, removeExisting: boolean) => void }) {
  const [name, setName] = useState('default')
  const [removeExisting, setRemoveExisting] = useState(false)
  const valid = name.trim() !== ''
  return (
    <Dialog
      open
      onClose={onClose}
      title={t('tune.mesh.save_as')}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button className="btn btn-primary" disabled={!valid} onClick={() => onSave(name.trim(), removeExisting && name.trim() !== existing)}>
            {t('common.save')}
          </button>
        </>
      }
    >
      <label className="grid gap-1.5">
        <span className="lbl">{t('tune.mesh.profile_name')}</span>
        <input className="field" autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && valid && onSave(name.trim(), removeExisting && name.trim() !== existing)} />
      </label>
      {!adaptive && (
        <>
          <label className={`mt-3 flex items-center gap-2.5 ${name.trim() === existing ? 'opacity-50' : ''}`}>
            <Toggle checked={removeExisting && name.trim() !== existing} disabled={name.trim() === existing} onChange={setRemoveExisting} label={t('tune.mesh.remove_profile_named', { name: existing })} />
            {t('tune.mesh.remove_profile_named', { name: existing })}
          </label>
          <p className="mt-2 mb-0 text-[12.5px] text-ink-3">
            {t('tune.mesh.remove_hint', { name: existing })}
          </p>
        </>
      )}
    </Dialog>
  )
}

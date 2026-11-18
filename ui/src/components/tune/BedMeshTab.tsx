import { useMemo, useRef, useState } from 'react'
import { Download, FolderOpen, Grid3x3, House, RotateCcw, Trash2, TriangleAlert } from 'lucide-react'
import { Card, Dialog, Segmented, Choices, Toggle, Empty, confirm } from '../ui'
import { attempt } from '../toast'
import { runGcode, useObject } from '../../state/printer'
import { useStore } from '../../lib/store'
import tltgProbeReportPoints, { type TltgProbeReportPoint } from '../../qidi/tltg-probe-report'
import { MeshSurface3D, type MeshSurfaceHandle } from './MeshSurface3D'
import { MeshHeatmap } from './MeshHeatmap'
import { SaveConfigNotice } from './SaveConfigNotice'
import { bedBounds, fmtMm, gcodeParam, MESH_GRADIENT, meshGrid, meshPreview, meshProfiles, meshScale, meshStats, meshView, type BedMeshStatus, type MeshMarker, type MeshView } from './mesh'
import { plural, useMedia, usePrinterFlags } from './hooks'

const pointName = (p: { x: number; y: number }) => `X${Math.round(p.x)} Y${Math.round(p.y)}`

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
  return tltgProbeReportPoints(useObject('tltg_probe_report'))
}

function MeshCard() {
  const bedMesh = useObject('bed_mesh') as BedMeshStatus
  const toolhead = useObject('toolhead')
  const report = useObject('tltg_probe_report')
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
  const markers: MeshMarker[] = preview ? preview.markers : problems.map((p) => ({ x: p.x, y: p.y, label: `${pointName(p)} took ${p.tries} tries` }))
  const unit = preview ? 'µm' : 'mm'
  const height = wide ? 460 : 300

  return (
    <Card
      title="Bed mesh"
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
          <span className="min-w-0 flex-1 text-ink-2">Showing {preview.title}.</span>
          <button className="btn btn-sm" onClick={() => meshPreview.set(null)}>
            Show the loaded mesh
          </button>
        </div>
      )}
      {grid && stats && bed && scale ? (
        <>
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Segmented
              label="View"
              value={view.mode}
              onChange={(mode) => setView({ mode })}
              options={[
                { value: '3d', label: '3D' },
                { value: 'map', label: 'Heat map' },
              ]}
            />
            {!preview && (
              <Segmented
                label="Matrix"
                value={view.matrix}
                onChange={(matrix) => setView({ matrix })}
                options={[
                  { value: 'probed_matrix', label: 'Probed' },
                  { value: 'mesh_matrix', label: 'Interpolated' },
                ]}
              />
            )}
            <span className="flex-1" />
            {view.mode === '3d' && (
              <>
                <button className="iconbtn" title="Reset the view" aria-label="Reset the view" onClick={() => surface.current?.resetView()}>
                  <RotateCcw size={16} />
                </button>
                <button className="iconbtn" title="Download bed mesh image" aria-label="Download bed mesh image" onClick={() => surface.current?.download(['bedmesh', preview ? 'bed-soak' : bedMesh.profile_name].filter(Boolean).join('-'))}>
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
            <Stat label="Range" value={fmtZ(stats.range, unit)} unit={unit} />
            <Stat label="Lowest" value={fmtZ(stats.min, unit)} unit={unit} />
            <Stat label="Highest" value={fmtZ(stats.max, unit)} unit={unit} />
            <Stat label="Std. deviation" value={fmtZ(stats.deviation, unit)} unit={unit} />
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2.5">
            <label className="flex items-center gap-2">
              <Toggle checked={view.wireframe} onChange={(wireframe) => setView({ wireframe })} disabled={view.mode !== '3d'} label="Wireframe" />
              <span className={view.mode !== '3d' ? 'text-ink-3' : ''}>Wireframe</span>
            </label>
            <label className="flex items-center gap-2">
              <Toggle checked={view.flat} onChange={(flat) => setView({ flat })} disabled={view.mode !== '3d'} label="Show flat plane" />
              <span className={view.mode !== '3d' ? 'text-ink-3' : ''}>Show flat plane</span>
            </label>
          </div>
          {!preview && (
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1.5">
                <span className="section-label">Color scale</span>
                <Choices
                  value={view.colorScale}
                  onChange={(colorScale) => setView({ colorScale })}
                  options={[
                    { value: 0, label: 'Fit' },
                    { value: 0.1, label: '0.1 mm' },
                    { value: 0.2, label: '0.2 mm' },
                  ]}
                />
              </div>
              <div className="grid gap-1.5">
                <span className="section-label">Z axis height</span>
                <Choices
                  value={view.boxScale}
                  disabled={view.mode !== '3d'}
                  onChange={(boxScale) => setView({ boxScale })}
                  options={[
                    { value: 'fit', label: 'Fit' },
                    { value: 1, label: '1 mm' },
                    { value: 1.5, label: '1.5 mm' },
                    { value: 2, label: '2 mm' },
                  ]}
                />
              </div>
            </div>
          )}
          {!preview && problems.length > 0 && <ProblemPoints points={problems} measured={report.measured as number | null | undefined} />}
          <p className="mt-3 mb-0 text-[12px] text-ink-3">{view.mode === '3d' ? 'Drag to turn the bed, scroll or pinch to zoom. Front of the bed at the bottom.' : 'Seen from above, back of the bed at the top.'}</p>
        </>
      ) : (
        <Empty>No mesh loaded. Load a profile or calibrate one.</Empty>
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
      {plural(count, '{count} problem point', '{count} problem points')}
    </span>
  )
}

/** The fork's note under the Bed mesh card: retried points of the loaded mesh. */
function ProblemPoints({ points, measured }: { points: TltgProbeReportPoint[]; measured?: number | null }) {
  const list = points.map((p) => `${pointName(p)} (${p.tries} tries, ${Math.round(p.spread * 1000)} µm apart)`).join(', ')
  return (
    <div className="tone-warn mt-3 flex items-start gap-2.5 rounded-lg px-3 py-2.5">
      <TriangleAlert size={15} className="mt-0.5 flex-none" />
      <div className="text-ink">
        {points.length === 1 ? `A probe point needed retries in this mesh, ${list}. Check the plate there.` : `Probe points needed retries in this mesh, ${list}. Check the plate there.`}
        {measured ? <div className="mt-0.5 text-[12px] text-ink-2">Measured {new Date(measured * 1000).toLocaleString()}</div> : null}
      </div>
    </div>
  )
}

function ProfilesCard() {
  const bedMesh = useObject('bed_mesh') as BedMeshStatus
  const report = useObject('tltg_probe_report')
  const problems = useProblemPoints()
  const { printing, busy, homed } = usePrinterFlags()
  const profiles = meshProfiles(bedMesh)
  const loaded = !!bedMesh.profile_name
  const [saveOpen, setSaveOpen] = useState(false)
  const run = (g: string) => attempt(() => runGcode(g))
  const problemTitle = problems.length
    ? `${plural(problems.length, '{count} problem point', '{count} problem points')}${report.measured ? ` in the mesh measured ${new Date((report.measured as number) * 1000).toLocaleString()}` : ''}`
    : undefined

  const load = async (name: string) => {
    if (printing && !(await confirm({ title: 'Load profile', body: `The printer is printing. Load profile ${name} anyway?`, confirm: 'Load' }))) return
    run(`BED_MESH_PROFILE LOAD=${gcodeParam(name)}`)
  }
  const remove = async (name: string) => {
    if (await confirm({ title: `Remove ${name} profile`, body: 'Klipper forgets the profile now. printer.cfg keeps it until you save the config.', confirm: 'Remove', danger: true }))
      run(`BED_MESH_PROFILE REMOVE=${gcodeParam(name)}`)
  }
  const clear = async () => {
    if (printing && !(await confirm({ title: 'Clear mesh', body: 'The printer is printing. Clear the bed mesh anyway?', confirm: 'Clear', danger: true }))) return
    run('BED_MESH_CLEAR')
  }

  return (
    <Card title="Profiles" aside={profiles.length ? String(profiles.length) : undefined}>
      {profiles.length ? (
        <div className="-mx-2 overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th className="text-right">Range</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {profiles.map((p) => (
                <tr key={p.name}>
                  <td>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-semibold">{p.name}</span>
                      {p.active && <span className="pill tone-accent py-0.5!">active</span>}
                      {p.adaptive && <span className="chip">not saved</span>}
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
                      <button className="iconbtn inline-grid" title="Load profile" aria-label={`Load ${p.name}`} onClick={() => load(p.name)}>
                        <FolderOpen size={16} />
                      </button>
                    )}
                    <button className="iconbtn inline-grid" title="Remove profile" aria-label={`Remove ${p.name}`} disabled={p.adaptive || printing} onClick={() => remove(p.name)}>
                      <Trash2 size={15} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty>No existing bed meshes found.</Empty>
      )}
      <div className="mt-3 grid grid-cols-2 gap-2">
        <button className={`btn ${homed ? '' : 'btn-primary'}`} disabled={busy} onClick={() => run('G28')}>
          <House size={15} /> Home all
        </button>
        <button className="btn" disabled={busy || !homed} onClick={() => run('BED_MESH_CALIBRATE')} title={homed ? undefined : 'Home first'}>
          Calibrate
        </button>
        <button className="btn" disabled={!loaded} onClick={clear}>
          Clear
        </button>
        <button className="btn btn-primary" disabled={!loaded || printing} onClick={() => setSaveOpen(true)}>
          Save as
        </button>
      </div>
      <ul className="mt-3 mb-0 grid gap-1 pl-4 text-[12.5px] text-ink-3">
        <li>
          <b className="text-ink-2">Calibrate</b> probes the whole bed and saves it as profile default, replacing it. QIDI&apos;s BED_MESH_CALIBRATE checks the mesh area first.
        </li>
        <li>
          <b className="text-ink-2">Clear</b> unloads the mesh; no profile changes.
        </li>
        <li>
          <b className="text-ink-2">Save as</b> keeps the loaded mesh under a new name. Profiles reach printer.cfg with Save config.
        </li>
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
      title="Save as"
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button className="btn btn-primary" disabled={!valid} onClick={() => onSave(name.trim(), removeExisting && name.trim() !== existing)}>
            Save
          </button>
        </>
      }
    >
      <label className="grid gap-1.5">
        <span className="lbl">Profile name</span>
        <input className="field" autoFocus value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && valid && onSave(name.trim(), removeExisting && name.trim() !== existing)} />
      </label>
      {!adaptive && (
        <>
          <label className={`mt-3 flex items-center gap-2.5 ${name.trim() === existing ? 'opacity-50' : ''}`}>
            <Toggle checked={removeExisting && name.trim() !== existing} disabled={name.trim() === existing} onChange={setRemoveExisting} label={`Remove ${existing} profile`} />
            Remove {existing} profile
          </label>
          <p className="mt-2 mb-0 text-[12.5px] text-ink-3">
            If saving as something other than {existing}, you can choose to also remove the {existing} profile.
          </p>
        </>
      )}
    </Dialog>
  )
}

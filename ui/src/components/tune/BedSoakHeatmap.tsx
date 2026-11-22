import { useMemo, useState } from 'react'
import { TriangleAlert } from 'lucide-react'
import { Toggle } from '../ui'
import { navigate } from '../../lib/router'
import { useTheme } from '../../lib/theme'
import { BedSoakColors, bedSoakChangeGrid, bedSoakHeatmapColor, bedSoakPointPosition, bedSoakProblemPoints, type BedSoakMesh, type BedSoakProblemPoint, type BedSoakRun } from '../../qidi/vo-bed-soak'
import { bedSoakClock, bedSoakMeshList, bedSoakMeshName, bedSoakPointName, formatChange } from './bed-soak-format'
import { meshPreview, type MeshMarker } from './mesh'
import { t } from '../../i18n'

const SIZE = 260
const PAD = 2

/** The fork's change-from-cold heat map: one mesh at a time, its flagged points, and the run's problems. */
export function BedSoakHeatmap({ run }: { run: BedSoakRun }) {
  const [theme] = useTheme()
  const [withHeight, setWithHeight] = useState(false)
  const [showAll, setShowAll] = useState(false)
  // null follows the latest mesh
  const [selected, setSelected] = useState<number | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const neutral = theme === 'dark' ? '#2b3440' : '#e4e7eb'
  const strong = theme === 'dark' ? '#f0f0f0' : '#0d1117'

  const last = run.meshes.length - 1
  const index = selected != null && selected < last ? selected : last
  const mesh: BedSoakMesh | undefined = run.meshes[index]
  const rows = mesh?.matrix.length ?? 0
  const cols = mesh?.matrix[0]?.length ?? 0
  const count = Math.max(rows, cols, 1)
  const cell = (SIZE - PAD * (count - 1)) / count
  const badge = Math.min(11, cell / 4)

  // µm
  const changeGrid = (m: BedSoakMesh) => bedSoakChangeGrid(run, m, withHeight)?.map((r) => r.map((v) => v * 1000)) ?? null
  const grid = mesh ? changeGrid(mesh) : null
  // The same scale for every mesh, so moving the slider doesn't rescale it
  const maxAbs = useMemo(
    () =>
      Math.max(
        5,
        ...run.meshes
          .filter((m) => m.used)
          .flatMap((m) => bedSoakChangeGrid(run, m, withHeight)?.flat() ?? [])
          .map((v) => Math.abs(v * 1000)),
      ),
    [run, withHeight],
  )

  const flags = new Map<string, string[]>()
  const add = (r: number, c: number, text: string) => flags.set(`${r}:${c}`, [...(flags.get(`${r}:${c}`) ?? []), text])
  for (const p of mesh?.retried ?? []) add(p.row, p.col, t('tune.soak.took_tries', { count: p.tries, um: Math.round(p.spread * 1000) }))
  for (const p of mesh?.out_of_line ?? []) add(p.row, p.col, t('tune.soak.out_of_line', { change: formatChange(p.change * 1000) }))

  const step = cell + PAD
  const cells = (grid ?? []).flatMap((row, r) =>
    row.map((value, c) => {
      const key = `${r}:${c}`
      return {
        key,
        row: r,
        col: c,
        x: c * step,
        // Front of the bed (the first row) at the bottom
        y: (rows - 1 - r) * step,
        value,
        color: bedSoakHeatmapColor(value, maxAbs, neutral),
        name: bedSoakPointName(bedSoakPointPosition(run.settings, rows, cols, r, c)),
        flag: flags.get(key) ?? null,
      }
    }),
  )
  const hoverCell = cells.find((c) => c.key === hover)

  const meshLabel = !mesh ? '' : mesh.index === 0 ? bedSoakMeshName(0) : [bedSoakMeshName(mesh.index), bedSoakClock(mesh.time), ...(mesh.used ? [] : [t('tune.soak.not_used')])].join(' · ')

  const meshes = (indices: number[]) => (indices.length <= 6 ? t('tune.soak.in_meshes', { count: indices.length, list: bedSoakMeshList(indices) }) : t('tune.soak.in_n_of', { n: indices.length, total: run.meshes.length }))
  const problemText = (p: BedSoakProblemPoint) =>
    [
      p.retried.length ? t('tune.soak.problem_retried', { meshes: meshes(p.retried), count: p.maxTries, um: Math.round(p.maxSpread * 1000) }) : '',
      p.outOfLine.length ? t('tune.soak.problem_out_of_line', { meshes: meshes(p.outOfLine), change: formatChange(p.maxChange * 1000) }) : '',
    ]
      .filter(Boolean)
      .join('; ')
  const problems = [
    ...bedSoakProblemPoints(run).map((p) => ({ key: `point:${p.row}:${p.col}`, name: bedSoakPointName(p), text: problemText(p) })),
    ...run.jumps.map((j) => ({ key: `jump:${j.mesh}`, name: bedSoakMeshName(j.mesh), text: t('tune.soak.problem_jump', { um: Math.round(j.size * 1000) }) })),
  ]
  const shown = showAll ? problems : problems.slice(0, 4)

  const viewIn3d = () => {
    if (!grid || !mesh) return
    const markers: MeshMarker[] = cells.filter((c) => c.flag).map((c) => ({ ...bedSoakPointPosition(run.settings, rows, cols, c.row, c.col), label: `${c.name} · ${c.flag?.join(' ')}` }))
    meshPreview.set({
      title: t(withHeight ? 'tune.soak.preview_shape_height' : 'tune.soak.preview_shape', { mesh: bedSoakMeshName(mesh.index) }),
      mesh_min: run.settings.mesh_min,
      mesh_max: run.settings.mesh_max,
      matrix: grid.map((r) => r.map((v) => v / 1000)),
      markers,
    })
    navigate('tune')
  }

  return (
    <div className="min-w-0">
      <div className="flex items-center gap-1.5">
        <span className="text-[13px] font-semibold">{t('tune.soak.change_from_cold')}</span>
      </div>
      <div className="text-[12px] text-ink-3">{t('tune.soak.top_view')}</div>
      {grid ? (
        <div className="relative mt-2" style={{ width: SIZE }}>
          <svg viewBox={`0 0 ${SIZE} ${SIZE}`} width={SIZE} height={SIZE} role="img" aria-label={t('tune.soak.change_from_cold')} className="block">
            {cells.map((c) => (
              <g key={c.key}>
                <rect
                  x={c.x}
                  y={c.y}
                  width={cell}
                  height={cell}
                  rx={2}
                  fill={c.color}
                  stroke={hover === c.key ? strong : 'none'}
                  strokeWidth={1.5}
                  tabIndex={0}
                  className="outline-none"
                  onPointerEnter={() => setHover(c.key)}
                  onPointerLeave={() => setHover(null)}
                  onFocus={() => setHover(c.key)}
                  onBlur={() => setHover(null)}
                />
                {c.flag && (
                  <g pointerEvents="none">
                    <rect x={c.x + 1.5} y={c.y + 1.5} width={cell - 3} height={cell - 3} rx={2} fill="none" stroke={BedSoakColors.warning} strokeWidth={2.5} />
                    <circle cx={c.x + cell - badge} cy={c.y + badge} r={badge - 3} fill={BedSoakColors.warning} />
                    <text x={c.x + cell - badge} y={c.y + badge + 3.5} textAnchor="middle" fontSize={10} fontWeight={700} fill="#0b0b0b">
                      !
                    </text>
                  </g>
                )}
              </g>
            ))}
          </svg>
          {hoverCell && (
            <div className="pointer-events-none absolute z-10 max-w-[230px] rounded-lg bg-surface px-2.5 py-1.5 text-[12px] leading-snug" style={{ left: Math.min(hoverCell.x + 8, SIZE - 200), top: hoverCell.y + cell - 4, boxShadow: 'var(--shadow-overlay)' }}>
              <div>
                <b className="num">{formatChange(hoverCell.value)}</b>
                <span className="ml-2 text-ink-2">{hoverCell.name}</span>
              </div>
              {hoverCell.flag?.map((f) => (
                <div key={f} className="mt-1 text-ink-2">
                  {f}
                </div>
              ))}
            </div>
          )}
          <div className="mt-1.5 flex items-center gap-1.5 text-[12px] text-ink-3">
            <span className="num">−{Math.round(maxAbs)}</span>
            <div className="h-1.5 flex-1 rounded-sm" style={{ background: `linear-gradient(90deg, #3987e5, ${neutral}, #e66767)` }} />
            {/* i18n-ignore: a unit */}
            <span className="num">+{Math.round(maxAbs)} µm</span>
          </div>
          {run.meshes.length > 1 && (
            <input
              type="range"
              className="mt-2 w-full accent-[var(--accent)]"
              min={0}
              max={last}
              step={1}
              value={index}
              aria-label={t('tune.soak.mesh')}
              onChange={(e) => setSelected(Number(e.target.value) >= last ? null : Number(e.target.value))}
            />
          )}
          <div className="text-[12.5px] text-ink-2">{meshLabel}</div>
          <div className="mt-2 flex items-center gap-2">
            <label className="flex items-center gap-2">
              <Toggle checked={withHeight} onChange={setWithHeight} label={t('tune.soak.height')} />
              {t('tune.soak.height')}
            </label>
            <span className="flex-1" />
            <button className="btn btn-sm" onClick={viewIn3d}>
              {t('tune.soak.view_3d')}
            </button>
          </div>
        </div>
      ) : (
        <div className="mt-2 text-ink-3">{t('tune.soak.shows_after_cold')}</div>
      )}

      {problems.length > 0 && (
        <div className="mt-3.5">
          <div className="mb-1 flex items-center gap-1.5 text-[13px] font-semibold">
            <TriangleAlert size={14} style={{ color: BedSoakColors.warning }} />
            {t('tune.soak.problems', { count: problems.length })}
          </div>
          {shown.map((p) => (
            <div key={p.key} className="my-0.5 ml-5 text-[12.5px]">
              <b>{p.name}</b> <span className="text-ink-2">{p.text}</span>
            </div>
          ))}
          {(problems.length > shown.length || showAll) && (
            <button className="btn btn-ghost btn-sm mt-1 ml-3" onClick={() => setShowAll((v) => !v)}>
              {showAll ? t('tune.soak.show_fewer') : t('tune.soak.show_all', { n: problems.length })}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

import { useEffect, useRef, useState } from 'react'
import { CircleCheck, Download, FolderOpen, Trash2, TriangleAlert } from 'lucide-react'
import { confirm } from '../ui'
import { attempt } from '../toast'
import { client } from '../../state/printer'
import { BED_SOAK_FOLDER, BED_SOAK_ROOT, BedSoakColors, BedSoakRunColors, bedSoakMilestones, bedSoakProblemPoints, type BedSoakRun } from '../../qidi/vo-bed-soak'
import { BedSoakChart, type BedSoakChartSeries } from './BedSoakChart'
import { bedSoakClock, bedSoakDuration, bedSoakStateLabel, bedSoakTargetsShort, fmtDateTime } from './bed-soak-format'
import { t } from '../../i18n'

export type BedSoakRunEntry = {
  /** Relative to the config root */
  path: string
  filename: string
  started: number
  bed: number
  chamber: number | null
  run: BedSoakRun | null
}

/** Deletes a run file. Over HTTP, as Moonraker's DELETE /server/files/config/<path>. */
async function deleteRun(filename: string) {
  const r = await fetch(client.fileUrl(BED_SOAK_ROOT, `${BED_SOAK_FOLDER}/${filename}`), { method: 'DELETE' })
  if (!r.ok) throw new Error(t('tune.soak.delete_failed', { name: filename, status: r.status }))
}

/** The fork's Past runs tab: every saved run, and up to three compared on charts. */
export function BedSoakPastRuns({ entries, liveFile, onOpen, onDeleted }: { entries: BedSoakRunEntry[]; liveFile: string; onOpen: (path: string) => void; onDeleted: () => void }) {
  const [compare, setCompare] = useState<string[]>([])
  const chosen = useRef(false)
  const [hoverX, setHoverX] = useState<number | null>(null)

  // Until runs are picked, compare the newest three
  useEffect(() => {
    const paths = entries.map((e) => e.path)
    setCompare((c) =>
      chosen.current
        ? c.filter((p) => paths.includes(p))
        : entries
            .filter((e) => e.run && e.run.meshes.length > 1)
            .slice(0, 3)
            .map((e) => e.path),
    )
  }, [entries])

  const toggle = (path: string) => {
    chosen.current = true
    setCompare((c) => (c.includes(path) ? c.filter((p) => p !== path) : [...c, path].slice(-3)))
  }
  const colorFor = (path: string) => {
    const i = compare.indexOf(path)
    return i >= 0 ? BedSoakRunColors[i] : null
  }

  if (!entries.length) return <div className="text-ink-3">{t('tune.soak.no_runs')}</div>

  const rows = entries.map((entry) => {
    const run = entry.run
    const m = run ? bedSoakMilestones(run) : null
    const duration = (s: number | null | undefined) => (s != null ? bedSoakDuration(s) : '—')
    const problems = run ? bedSoakProblemPoints(run).length : 0
    const jumps = run?.jumps.length ?? 0
    const state = run?.result?.state
    let settled: { text: string; icon?: 'good' | 'warning' } | null = null
    if (m?.settled != null) settled = { text: duration(m.settled), icon: 'good' }
    else if (state === 'finished') settled = { text: t('tune.soak.still_moving_at', { limit: run?.settings.time_limit ?? '—' }), icon: 'warning' }
    else if (run) settled = { text: '—' }
    let note = ''
    if (entry.path === liveFile) note = bedSoakStateLabel('running') ?? ''
    else if (run && !run.result) note = bedSoakStateLabel('incomplete') ?? ''
    else if (state === 'stopped' || state === 'failed') note = bedSoakStateLabel(state) ?? ''
    const probeCount = run?.meshes[0]?.matrix.length ?? run?.settings.probe_count
    return {
      ...entry,
      color: colorFor(entry.path),
      date: fmtDateTime(run?.settings.started || entry.started),
      note,
      bed: run?.settings.bed ?? entry.bed,
      chamber: run ? run.settings.chamber : entry.chamber,
      mesh: probeCount ? `${run?.meshes[0]?.matrix[0].length ?? probeCount} × ${probeCount}` : '—',
      bedAt: run ? duration(m?.bed) : '…',
      settled,
      ready: run ? duration(m?.ready) : '',
      problemCount: problems + jumps,
      problems: [problems ? t('tune.soak.points', { count: problems }) : '', jumps ? t('tune.soak.jumps', { count: jumps }) : ''].filter(Boolean).join(' · '),
    }
  })

  const compareRuns = compare
    .map((p) => entries.find((e) => e.path === p))
    .filter((e): e is BedSoakRunEntry & { run: BedSoakRun } => e?.run != null)
    .map((e) => ({ entry: e, run: e.run, color: colorFor(e.path) ?? BedSoakRunColors[0], name: bedSoakTargetsShort(e.run.settings.bed, e.run.settings.chamber) }))
  const times = compareRuns.flatMap(({ run }) => [...run.meshes.map((m) => m.time), run.temps.length ? run.temps[run.temps.length - 1][0] : 0])
  const xMax = Math.max(10, Math.ceil(Math.max(0, ...times) / 60 / 5) * 5)
  const timeLabel = (minutes: number) => t('tune.soak.time_after', { time: bedSoakClock(minutes * 60) })
  const heightSeries: BedSoakChartSeries[] = compareRuns.map(({ run, color, name }) => ({
    name,
    color,
    format: (v) => `${Math.round(v)} µm`,
    data: [{ x: 0, y: 0 }, ...run.meshes.filter((m) => m.used && m.index > 0 && m.height != null && m.time >= 0).map((m) => ({ x: m.time / 60, y: (m.height ?? 0) * 1000 }))],
  }))
  const chamberSeries: BedSoakChartSeries[] = compareRuns.map(({ run, color, name }) => {
    let last = -Infinity
    return {
      name,
      color,
      format: (v) => `${v.toFixed(1)} °C`,
      // Every 30 s is plenty to compare runs
      data: run.temps
        .filter((s) => {
          if (s[0] < 0 || s[2] == null || s[0] - last < 30) return false
          last = s[0]
          return true
        })
        .map((s) => ({ x: s[0] / 60, y: s[2] ?? 0 })),
    }
  })

  const remove = async (filename: string) => {
    if (await confirm({ title: t('tune.soak.delete_title'), body: t('tune.soak.delete_body', { name: filename }), confirm: t('common.delete'), danger: true })) {
      await attempt(() => deleteRun(filename))
      onDeleted()
    }
  }

  return (
    <div className="min-w-0">
      <div className="-mx-4 overflow-x-auto">
        <table className="table text-[12.5px]">
          <thead>
            <tr>
              <th className="w-8 pl-4!" />
              <th>{t('tune.soak.col_date')}</th>
              <th className="text-right">{t('tune.soak.bed')}</th>
              <th className="text-right">{t('tune.soak.chamber')}</th>
              <th>{t('tune.soak.mesh')}</th>
              <th className="text-right">{t('tune.soak.col_bed_at_temp')}</th>
              <th>{t('tune.soak.stopped_moving')}</th>
              <th className="text-right">{t('tune.soak.col_ready')}</th>
              <th>{t('tune.soak.col_problems')}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.path} style={r.color ? { background: 'color-mix(in srgb, var(--blue) 7%, transparent)' } : undefined}>
                <td className="pl-4!">
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-[var(--accent)]"
                    checked={!!r.color}
                    disabled={!r.run || (!r.color && compare.length >= 3)}
                    onChange={() => toggle(r.path)}
                    aria-label={t('tune.soak.compare_named', { name: r.filename })}
                  />
                </td>
                <td className="whitespace-nowrap">
                  <i className="mr-1.5 inline-block h-0.5 w-3 rounded-sm align-middle" style={{ background: r.color ?? 'transparent' }} />
                  {r.date}
                  {r.note && <span className="ml-1.5 text-ink-3">{r.note}</span>}
                </td>
                <td className="num text-right whitespace-nowrap">{r.bed} °C</td>
                <td className="num text-right whitespace-nowrap">{r.chamber ? `${r.chamber} °C` : <span className="text-ink-3">{t('tune.soak.off')}</span>}</td>
                <td className="whitespace-nowrap">{r.mesh}</td>
                <td className="num text-right whitespace-nowrap">{r.bedAt}</td>
                <td className="whitespace-nowrap">
                  {r.settled && (
                    <span className="inline-flex items-center gap-1">
                      {r.settled.icon === 'good' && <CircleCheck size={13} style={{ color: BedSoakColors.good }} />}
                      {r.settled.icon === 'warning' && <TriangleAlert size={13} style={{ color: BedSoakColors.warning }} />}
                      {r.settled.text}
                    </span>
                  )}
                </td>
                <td className="num text-right whitespace-nowrap">{r.ready}</td>
                <td className="whitespace-nowrap">
                  {r.problems ? (
                    <span className="inline-flex items-center gap-1" title={r.problems}>
                      <TriangleAlert size={13} style={{ color: BedSoakColors.warning }} />
                      {r.problemCount}
                    </span>
                  ) : r.run ? (
                    <span className="text-ink-3">{t('tune.soak.none')}</span>
                  ) : null}
                </td>
                <td className="text-right whitespace-nowrap">
                  <button className="iconbtn inline-grid text-accent-ink!" disabled={!r.run} title={t('tune.soak.open')} aria-label={t('tune.soak.open_named', { name: r.filename })} onClick={() => onOpen(r.path)}>
                    <FolderOpen size={15} />
                  </button>
                  <a className="iconbtn inline-grid" href={client.fileUrl(BED_SOAK_ROOT, r.path)} download={r.filename} title={t('tune.shared.download')} aria-label={t('tune.soak.download_named', { name: r.filename })}>
                    <Download size={15} />
                  </a>
                  <button className="iconbtn inline-grid" disabled={r.path === liveFile} title={t('common.delete')} aria-label={t('tune.soak.delete_named', { name: r.filename })} onClick={() => remove(r.filename)}>
                    <Trash2 size={15} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="sep my-3" />
      {compareRuns.length ? (
        <div className="grid gap-6 xl:grid-cols-2">
          <BedSoakChart title={t('tune.soak.chart_height_since')} unit="µm" series={heightSeries} xMin={0} xMax={xMax} timeLabel={timeLabel} height={180} hoverX={hoverX} onHover={setHoverX} />
          <BedSoakChart title={t('tune.soak.chart_chamber_since')} unit="°C" series={chamberSeries} xMin={0} xMax={xMax} timeLabel={timeLabel} height={180} hoverX={hoverX} onHover={setHoverX} />
        </div>
      ) : (
        <div className="text-ink-3">{t('tune.soak.compare_hint')}</div>
      )}
    </div>
  )
}

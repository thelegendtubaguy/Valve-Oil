import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, Flame, Square } from 'lucide-react'
import { Card, Segmented, Spinner, confirm } from '../ui'
import { attempt } from '../toast'
import { client, useObject, usePrinter } from '../../state/printer'
import { BED_SOAK_FOLDER, BED_SOAK_PATH, BED_SOAK_ROOT, parseBedSoakFilename, parseBedSoakRun, type BedSoakRun, type BedSoakTemp, type VoBedSoakState } from '../../qidi/vo-bed-soak'
import { BedSoakSetup } from './BedSoakSetup'
import { BedSoakRunView, useChamberTemp } from './BedSoakRun'
import { BedSoakPastRuns, type BedSoakRunEntry } from './BedSoakPastRuns'

type CachedRun = { modified: number; run: BedSoakRun | null }

/** Run files in config/tltg_bed_soak, from Moonraker's GET /server/files/directory. Empty until the folder exists. */
async function listRuns(): Promise<{ filename: string; modified: number }[]> {
  try {
    const r = await fetch(`${client.httpBase}/server/files/directory?path=${encodeURIComponent(BED_SOAK_PATH)}`, { cache: 'no-store' })
    // Moonraker answers 400 while the folder doesn't exist yet
    if (!r.ok) return []
    const body = await r.json()
    return ((body.result?.files ?? []) as { filename: string; modified: number }[]).filter((f) => f.filename.endsWith('.jsonl')).map((f) => ({ filename: f.filename, modified: f.modified }))
  } catch {
    return []
  }
}

async function readRun(path: string): Promise<BedSoakRun | null> {
  const r = await fetch(client.fileUrl(BED_SOAK_ROOT, path), { cache: 'no-store' })
  if (!r.ok) throw new Error(`${r.status}`)
  return parseBedSoakRun(await r.text())
}

/** The fork's Bed soak card: setup or the run in progress, and past runs. */
export function BedSoakTab() {
  const status = useObject('tltg_bed_soak') as unknown as VoBedSoakState
  const filesRev = usePrinter((s) => s.filesRev)
  const bedNow = usePrinter((s) => s.status.heater_bed?.temperature as number | undefined)
  const chamberNow = useChamberTemp()
  const [tab, setTab] = useState<'run' | 'past'>('run')
  // The ended run whose results were put away with New test
  const [dismissed, setDismissed] = useState<string | null>(null)
  // The past run open in the Past runs tab
  const [opened, setOpened] = useState<string | null>(null)
  const [files, setFiles] = useState<{ filename: string; modified: number }[]>([])
  const [runs, setRuns] = useState<Record<string, CachedRun | undefined>>({})
  const [liveTemps, setLiveTemps] = useState<BedSoakTemp[]>([])
  const [stopping, setStopping] = useState(false)
  const runsRef = useRef(runs)
  runsRef.current = runs
  const loading = useRef(new Set<string>())
  const reload = useRef(new Set<string>())

  const running = status.state === 'running'
  const showSetup = !running && (status.state === 'idle' || !status.file || dismissed === status.file)

  const refreshList = useCallback(() => {
    listRuns().then(setFiles)
  }, [])

  const loadRun = useCallback(async (path: string, modified: number): Promise<void> => {
    if (loading.current.has(path)) {
      reload.current.add(path)
      return
    }
    loading.current.add(path)
    try {
      const run = await readRun(path)
      setRuns((r) => ({ ...r, [path]: { modified, run } }))
    } catch {
      // Not readable yet; the next change tries again
    } finally {
      loading.current.delete(path)
    }
    if (reload.current.delete(path)) await loadRun(path, modified)
  }, [])

  // The folder changes as runs are written and deleted.
  useEffect(refreshList, [refreshList, filesRev, status.file])

  // Read new and changed run files
  useEffect(() => {
    for (const f of files) {
      const path = `${BED_SOAK_FOLDER}/${f.filename}`
      if (runsRef.current[path]?.modified !== f.modified) loadRun(path, f.modified)
    }
  }, [files, loadRun])

  const entries: BedSoakRunEntry[] = useMemo(
    () =>
      files
        .map((f) => {
          const path = `${BED_SOAK_FOLDER}/${f.filename}`
          const parsed = parseBedSoakFilename(f.filename)
          return { path, filename: f.filename, started: parsed?.started ?? f.modified, bed: parsed?.bed ?? 0, chamber: parsed?.chamber ?? null, run: runs[path]?.run ?? null }
        })
        .sort((a, b) => b.filename.localeCompare(a.filename)),
    [files, runs],
  )
  useEffect(() => {
    if (opened && !entries.some((e) => e.path === opened)) setOpened(null)
  }, [entries, opened])

  // The live status carries no grids, so read the run file each time it reports a new mesh. Klipper may report the
  // mesh before the file has it, so try again a few times.
  const liveKey = [status.file, status.meshes, status.state, status.problems?.jumps].join('|')
  useEffect(() => {
    const file = status.file
    if (!file) return
    let tries = 0
    let timer: ReturnType<typeof setTimeout>
    const attemptLoad = async () => {
      await loadRun(file, Date.now())
      const run = runsRef.current[file]?.run
      if ((run == null || run.meshes.length < status.meshes) && tries++ < 5) timer = setTimeout(attemptLoad, 2000)
    }
    timer = setTimeout(attemptLoad, 300)
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveKey, loadRun])

  useEffect(() => setLiveTemps([]), [status.file])
  useEffect(() => {
    if (running) (setTab('run'), setDismissed(null))
  }, [running])

  const liveRun = status.file ? (runs[status.file]?.run ?? null) : null

  // Temperatures since the run file's last mesh, sampled as the test reports its time
  useEffect(() => {
    const elapsed = status.elapsed
    if (!running || elapsed == null) return
    const temps = liveRun?.temps ?? []
    const last = temps.length ? temps[temps.length - 1][0] : -Infinity
    setLiveTemps((t) => [...t.filter((s) => s[0] > last && s[0] < elapsed), [elapsed, bedNow ?? null, chamberNow ?? null]])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status.elapsed])

  const setupRuns = entries.filter((e): e is BedSoakRunEntry & { run: BedSoakRun } => e.run != null).map((e) => ({ started: e.run.settings.started || e.started, run: e.run }))
  const openedEntry = opened ? entries.find((e) => e.path === opened) : undefined

  const stop = async () => {
    if (!(await confirm({ title: 'Stop the bed soak test', body: 'Stop the bed soak test? The heaters it turned on turn off now, and the results so far are kept.', confirm: 'Stop', danger: true }))) return
    setStopping(true)
    await attempt(() => client.call('printer.tltg_bed_soak.stop'))
    setStopping(false)
  }

  if (!status.state) return null

  const action = running && tab === 'run' ? (
    <button className="btn btn-sm btn-danger" disabled={status.stop_requested || stopping} onClick={stop}>
      {stopping ? <Spinner size={12} /> : <Square size={12} />} Stop
    </button>
  ) : tab === 'run' && !showSetup ? (
    <button className="btn btn-sm" onClick={() => setDismissed(status.file)}>
      New test
    </button>
  ) : tab === 'past' && openedEntry ? (
    <button className="btn btn-sm" onClick={() => setOpened(null)}>
      <ArrowLeft size={13} /> Back
    </button>
  ) : null

  return (
    <Card>
      <div className="mb-3.5 flex flex-wrap items-center gap-2">
        <Flame size={15} className="text-accent-ink" />
        <Segmented
          label="Bed soak"
          value={tab}
          onChange={setTab}
          options={[
            { value: 'run', label: 'Bed soak' },
            { value: 'past', label: `Past runs (${entries.length})` },
          ]}
        />
        <span className="flex-1" />
        {action}
      </div>
      {tab === 'run' && (
        <>
          <p className="mt-0 mb-3.5 max-w-[80ch] text-[12.5px] text-ink-3">
            Measures the bed from cold while it heats, to show how long the bed and chamber take to reach temperature and how long the bed keeps moving after that. Times count from when the heaters turn on.
          </p>
          {showSetup ? <BedSoakSetup status={status} runs={setupRuns} /> : <BedSoakRunView run={liveRun} status={status} live={running} liveTemps={liveTemps} file={status.file} />}
        </>
      )}
      {tab === 'past' &&
        (openedEntry ? (
          <BedSoakRunView run={openedEntry.run} file={openedEntry.path} />
        ) : (
          <BedSoakPastRuns entries={entries} liveFile={running ? status.file : ''} onOpen={setOpened} onDeleted={refreshList} />
        ))}
    </Card>
  )
}

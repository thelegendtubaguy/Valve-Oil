import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from 'react'
import {
  ArrowDown,
  ArrowUp,
  Box,
  ChevronRight,
  Columns3,
  Copy,
  Download,
  Ellipsis,
  Eye,
  File,
  FileArchive,
  FilePlus,
  FileText,
  Filter,
  Flame,
  Folder,
  FolderInput,
  FolderPlus,
  FolderUp,
  House,
  Image,
  Info,
  LayoutGrid,
  List,
  ListPlus,
  Lock,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  SquarePen,
  Trash2,
  Upload,
  X,
} from 'lucide-react'
import { Card, Empty, Segmented, Spinner, confirm, prompt } from '../ui'
import { attempt, toast } from '../toast'
import { FileCard } from '../FileCard'
import { ContextMenu, type MenuItem } from './ContextMenu'
import { Check, Popover } from './Popover'
import { InfoDialog, MoveDialog, ViewDialog } from './dialogs'
import { Cell } from './cells'
import { filesFromDrop, filesWithPath, hasFiles } from './drop'
import {
  alwaysHidden,
  baseName,
  canPreview,
  columnsFor,
  copyName,
  editsOnClick,
  filterLabel,
  headersKey,
  isFiltered,
  isImage,
  isLocked,
  isPrintable,
  isReadonly,
  isVideo,
  joinPath,
  mergeColumns,
  parentOf,
  rootProps,
  sortEntries,
  validName,
  type Column,
  type ConfiguredHeader,
  type FilterType,
  type FsEntry,
} from './fs'
import {
  copyItem,
  createZip,
  deleteItem,
  downloadFile,
  downloadZip,
  enqueueJobs,
  makeDir,
  moveItem,
  rescanMeta,
  thumbnailUrl,
  uploadFiles,
  useDirectory,
  useFileMeta,
  useHistory,
  useRootFiles,
  writeText,
  type HistoryJob,
} from '../../state/files'
import { client, runGcode, usePrinter } from '../../state/printer'
import { FLUIDD, VALVE_OIL_UI, useDbItem } from '../../state/database'
import { useJob, isActive } from '../../state/job'
import { displayName, fmtDuration } from '../../lib/format'
import { fmtDate, fmtSize } from './cells'
import { gcode } from '../control/gcode'
import { t, type MessageKey } from '../../i18n'

const EditorDialog = lazy(() => import('../editor/ConfigEditor').then((m) => ({ default: m.EditorDialog })))

const ROOTS_NAMED = ['gcodes', 'config', 'config_examples', 'docs', 'logs', 'timelapse']

/** A Moonraker root's name for people: "config" -> "Configuration"; a root without one shows as itself. */
export const rootLabel = (root: string) => (ROOTS_NAMED.includes(root) ? t(`files.roots.${root}` as MessageKey) : root)

type Props = {
  root: string
  /** The folder shown, relative to the root. Without it the browser keeps its own place. */
  path?: string
  /** Link for a folder; when given, folder changes go through the URL. */
  dirHref?: (path: string) => string
  /** Link that opens a file in the editor; without it the editor opens in a dialog. */
  editHref?: (path: string) => string
  /** Link to the G-code preview of a file. */
  previewHref?: (path: string) => string
  /** Starts a print (opens the print dialog). */
  onPrint?: (path: string) => void
  title?: ReactNode
}

type Menu = { x: number; y: number; items: FsEntry[] }
const DRAG_TYPE = 'application/x-valve-oil-files'

export function FileBrowser({ root, path: given, dirHref, editHref, previewHref, onPrint, title }: Props) {
  const [own, setOwn] = useState('')
  const path = given ?? own
  const go = useCallback((p: string) => (dirHref ? (location.hash = dirHref(p)) : setOwn(p)), [dirHref])
  const props = rootProps(root)
  const gcodes = root === 'gcodes'
  const label = rootLabel(root)

  const { dir, error, loading } = useDirectory(root, path)
  const components = usePrinter((s) => s.server?.components ?? [])
  const klippyReady = usePrinter((s) => s.klippy === 'ready')
  const job = useJob()
  const busy = isActive(job.state)
  const hasQueue = components.includes('job_queue')

  // Fluidd's own settings, so a sort, filter, or column choice made in either UI shows in both.
  const [filters, setFilters] = useDbItem<FilterType[]>(FLUIDD, `uiSettings.fileSystem.activeFilters.${root}`, [])
  const [sortByRaw, setSortBy] = useDbItem<string | null>(FLUIDD, `uiSettings.fileSystem.sortBy.${root}`, null)
  const [sortDescRaw, setSortDesc] = useDbItem<boolean | null>(FLUIDD, `uiSettings.fileSystem.sortDesc.${root}`, null)
  const [savedHeaders, setSavedHeaders] = useDbItem<ConfiguredHeader[] | null>(FLUIDD, headersKey(root), null)
  const [view, setView] = useDbItem<'list' | 'grid'>(VALVE_OIL_UI, `files.view.${root}`, gcodes ? 'grid' : 'list')
  const sortBy = sortByRaw ?? 'modified'
  const sortDesc = sortDescRaw ?? true
  const columns = useMemo(() => mergeColumns(columnsFor(root), savedHeaders), [root, savedHeaders])
  const shownColumns = columns.filter((c) => c.visible)

  const jobs = useHistory(500, gcodes)
  const byFile = useMemo(() => {
    const m = new Map<string, { last: HistoryJob; count: number }>()
    for (const j of jobs ?? []) {
      const e = m.get(j.filename)
      if (!e) m.set(j.filename, { last: j, count: j.status === 'completed' ? 1 : 0 })
      else if (j.status === 'completed') e.count++
    }
    return m
  }, [jobs])
  const jobOf = useCallback((e: FsEntry) => (gcodes ? byFile.get(e.path)?.last : undefined), [byFile, gcodes])

  const [query, setQuery] = useState('')
  const q = query.trim().toLowerCase()
  const all = useRootFiles(root, !!q)

  const entries = useMemo(() => {
    let list: FsEntry[]
    if (q) {
      list = (all ?? [])
        .filter((f) => f.path.toLowerCase().includes(q) && !f.path.split('/').some((seg) => seg === '.thumbs' || seg === '.git'))
        .slice(0, 300)
        .map((f) => ({ kind: 'file', name: baseName(f.path), path: f.path, modified: f.modified, size: f.size, permissions: f.permissions ?? 'rw' }))
    } else list = [...(dir?.dirs ?? []), ...(dir?.files ?? [])]
    list = list.filter((e) => !alwaysHidden(e) && !isFiltered(e, filters))
    return sortEntries(list, sortBy, sortDesc, jobOf)
  }, [q, all, dir, filters, sortBy, sortDesc, jobOf])
  const names = useMemo(() => new Set([...(dir?.dirs ?? []), ...(dir?.files ?? [])].map((e) => e.name)), [dir])

  // ---- selection ----
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const anchor = useRef<number | null>(null)
  useEffect(() => {
    setSelected(new Set())
    anchor.current = null
  }, [root, path, q])
  const picked = entries.filter((e) => selected.has(e.path))
  const select = (e: FsEntry, on: boolean, shift: boolean) => {
    const i = entries.indexOf(e)
    setSelected((s) => {
      const next = new Set(s)
      if (shift && anchor.current != null) {
        const [a, b] = [Math.min(anchor.current, i), Math.max(anchor.current, i)]
        for (const x of entries.slice(a, b + 1)) on ? next.add(x.path) : next.delete(x.path)
      } else on ? next.add(e.path) : next.delete(e.path)
      return next
    })
    anchor.current = i
  }

  // ---- dialogs ----
  const [menu, setMenu] = useState<Menu | null>(null)
  const [moving, setMoving] = useState<FsEntry[] | null>(null)
  const [info, setInfo] = useState<FsEntry | null>(null)
  const [viewing, setViewing] = useState<FsEntry | null>(null)
  const [editing, setEditing] = useState<string | null>(null)

  // ---- actions ----
  const canWrite = (e: FsEntry) => !isReadonly(root, e)
  const writable = (list: FsEntry[]) => {
    const ok = list.filter(canWrite)
    if (ok.length < list.length) toast(list.length === 1 ? t('files.browser.read_only_named', { name: list[0].name }) : t('files.browser.skipped_read_only', { count: list.length - ok.length }), 'error')
    return ok
  }
  const exists = (name: string) => names.has(name) && (toast(t('files.browser.exists', { name }), 'error'), true)

  const edit = async (e: FsEntry) => {
    if (e.size > 5 * 1024 * 1024 && !(await confirm({ title: t('files.browser.open_large_title', { name: e.name }), body: t('files.browser.open_large_body', { size: fmtSize(e.size) }), confirm: t('files.browser.open') }))) return
    if (editHref) location.hash = editHref(e.path)
    else setEditing(e.path)
  }
  const open = (e: FsEntry, x = 0, y = 0) => {
    if (e.kind === 'dir') return go(e.path)
    if (gcodes && onPrint && isPrintable(e.name)) return onPrint(e.path)
    if (editsOnClick(e.name)) return edit(e)
    if (isImage(e.name) || isVideo(e.name)) return setViewing(e)
    setMenu({ x, y, items: [e] })
  }
  const rename = async (e: FsEntry) => {
    const name = await prompt({ title: e.kind === 'dir' ? t('files.browser.rename_dir') : t('files.browser.rename_file'), input: { label: e.kind === 'dir' ? t('files.browser.dir_name') : t('files.browser.file_name'), value: e.name } })
    if (!name || name === e.name) return
    const err = validName(name)
    if (err) return toast(err, 'error')
    if (exists(name)) return
    await attempt(() => moveItem(root, e.path, joinPath(parentOf(e.path), name)))
  }
  const duplicate = async (e: FsEntry) => {
    const name = await prompt({ title: e.kind === 'dir' ? t('files.browser.duplicate_dir') : t('files.browser.duplicate_file'), input: { label: e.kind === 'dir' ? t('files.browser.dir_name') : t('files.browser.file_name'), value: copyName(e.name, names) } })
    if (!name) return
    const err = validName(name)
    if (err) return toast(err, 'error')
    if (exists(name)) return
    await attempt(() => copyItem(root, e.path, joinPath(parentOf(e.path), name)))
  }
  const remove = async (list: FsEntry[]) => {
    list = writable(list)
    if (!list.length) return
    const ok = await confirm({
      title: list.length === 1 ? t('files.browser.delete_title_named', { name: list[0].name }) : t('files.browser.delete_title_items', { count: list.length }),
      body: list.some((e) => e.kind === 'dir') ? t('files.browser.delete_body_folders') : t('files.browser.delete_body'),
      confirm: t('common.delete'),
      danger: true,
    })
    if (!ok) return
    for (const e of list) await attempt(() => deleteItem(root, e))
    setSelected(new Set())
  }
  const moveTo = async (list: FsEntry[], dest: string) => {
    list = writable(list).filter((e) => parentOf(e.path) !== dest && !(e.kind === 'dir' && (dest === e.path || dest.startsWith(`${e.path}/`))))
    for (const e of list) await attempt(() => moveItem(root, e.path, joinPath(dest, e.name)))
    if (list.length) toast(list.length === 1 ? t('files.browser.moved_named', { name: list[0].name }) : t('files.browser.moved_items', { count: list.length }))
    setSelected(new Set())
  }
  const download = (list: FsEntry[]) => {
    if (list.length === 1 && list[0].kind === 'file') return downloadFile(root, list[0].path)
    toast(t('files.browser.preparing_zip'))
    attempt(() => downloadZip(root, path, list))
  }
  const zip = (list: FsEntry[]) => {
    const stamp = new Date().toISOString().replace(/[-:]/g, '').replace('T', '_').slice(0, 15)
    const name = list.length === 1 ? `${list[0].name}-${stamp}.zip` : `${stamp}.zip`
    attempt(async () => (await createZip(root, joinPath(path, name), list), toast(t('files.browser.created', { name }))))
  }
  const enqueue = (list: FsEntry[]) => {
    const files = list.filter((e) => e.kind === 'file' && isPrintable(e.name)).map((e) => e.path)
    if (files.length) attempt(async () => (await enqueueJobs(files), toast(files.length === 1 ? t('files.browser.queued_named', { name: displayName(files[0]) }) : t('files.browser.queued_files', { count: files.length }))))
  }
  const rescan = (list: FsEntry[]) => {
    const files = list.filter((e) => e.kind === 'file' && isPrintable(e.name))
    attempt(async () => {
      for (const f of files) await rescanMeta(f.path)
      toast(files.length === 1 ? t('files.browser.refreshed_named', { name: f0(files) }) : t('files.browser.refreshed_files', { count: files.length }))
    })
  }
  const preheat = (e: FsEntry) => {
    const m = e.meta
    if (!m) return
    const lines = [Number(m.first_layer_extr_temp) > 0 && gcode`M104 S${m.first_layer_extr_temp}`, Number(m.first_layer_bed_temp) > 0 && gcode`M140 S${m.first_layer_bed_temp}`, Number(m.chamber_temp) > 0 && gcode`M141 S${m.chamber_temp}`].filter(Boolean)
    if (lines.length) attempt(() => runGcode(lines.join('\n')))
  }
  const copyToConfig = async (e: FsEntry) => {
    if (isLocked('config', e.name)) return toast(t('files.browser.copy_locked'), 'error')
    attempt(async () => (await client.call('server.files.copy', { source: joinPath(root, e.path), dest: joinPath('config', e.name) }), toast(t('files.browser.copied', { name: e.name }))))
  }
  const newFile = async () => {
    // i18n-ignore: the placeholder is an example file name
    const name = await prompt({ title: t('files.browser.add_file'), input: { label: t('files.browser.file_name'), value: '', placeholder: root === 'config' ? 'my_macros.cfg' : '' } })
    if (!name) return
    const err = validName(name)
    if (err) return toast(err, 'error')
    if (exists(name)) return
    if (!(await attempt(async () => (await writeText(root, joinPath(path, name), ''), true)))) return
    if (editsOnClick(name)) edit({ kind: 'file', name, path: joinPath(path, name), modified: Date.now() / 1000, size: 0, permissions: 'rw' })
  }
  const newFolder = async () => {
    const name = await prompt({ title: t('files.browser.add_dir'), input: { label: t('files.browser.dir_name'), value: '' } })
    if (!name) return
    const err = validName(name)
    if (err) return toast(err, 'error')
    if (exists(name)) return
    await attempt(() => makeDir(root, joinPath(path, name)))
  }

  const menuFor = (list: FsEntry[]): MenuItem[] => {
    const one = list.length === 1 ? list[0] : null
    const file = one?.kind === 'file' ? one : null
    const printable = list.some((e) => e.kind === 'file' && isPrintable(e.name))
    const ro = props.readonly || list.every((e) => !canWrite(e))
    const I = 15
    return [
      one?.kind === 'dir' && { label: t('files.browser.open'), icon: <Folder size={I} />, onSelect: () => go(one.path) },
      gcodes && file && isPrintable(file.name) && !!onPrint && { label: t('files.menu.print'), icon: <Play size={I} />, onSelect: () => onPrint(file.path), disabled: busy || !klippyReady },
      gcodes && printable && hasQueue && { label: t('files.browser.add_to_queue'), icon: <ListPlus size={I} />, onSelect: () => enqueue(list) },
      gcodes && file && file.meta?.first_layer_extr_temp != null && file.meta?.first_layer_bed_temp != null && { label: t('files.menu.preheat'), icon: <Flame size={I} />, onSelect: () => preheat(file), disabled: busy || !klippyReady },
      gcodes && file && canPreview(file.name) && !!previewHref && { label: t('files.menu.preview'), icon: <Box size={I} />, onSelect: () => void (location.hash = previewHref(file.path)) },
      file && (isImage(file.name) || isVideo(file.name)) && { label: t('files.menu.view'), icon: <Image size={I} />, onSelect: () => setViewing(file) },
      file && file.permissions.includes('r') && !isBinary(file.name) && { label: canWrite(file) ? t('common.edit') : t('files.menu.open_read_only'), icon: canWrite(file) ? <SquarePen size={I} /> : <Eye size={I} />, onSelect: () => edit(file) },
      one && { label: t('files.menu.info'), icon: <Info size={I} />, onSelect: () => setInfo(one) },
      gcodes && printable && { label: t('files.menu.refresh_metadata'), icon: <RefreshCw size={I} />, onSelect: () => rescan(list) },
      'sep',
      { label: file ? t('files.browser.download') : t('files.menu.download_zip'), icon: <Download size={I} />, onSelect: () => download(list) },
      !props.readonly && !(file && file.name.endsWith('.zip')) && { label: t('files.menu.create_zip'), icon: <FileArchive size={I} />, onSelect: () => zip(list) },
      root === 'config_examples' && file && { label: t('files.menu.copy_to_config'), icon: <Copy size={I} />, onSelect: () => copyToConfig(file) },
      !ro && one && { label: t('common.rename'), icon: <Pencil size={I} />, onSelect: () => rename(one) },
      !props.readonly && one && { label: t('files.menu.duplicate'), icon: <Copy size={I} />, onSelect: () => duplicate(one) },
      !ro && { label: t('files.browser.move'), icon: <FolderInput size={I} />, onSelect: () => setMoving(list) },
      !ro && 'sep',
      !ro && { label: t('common.delete'), icon: <Trash2 size={I} />, danger: true, onSelect: () => remove(list) },
    ]
  }
  const openMenu = (e: FsEntry, x: number, y: number) => setMenu({ x, y, items: selected.has(e.path) && selected.size > 1 ? picked : [e] })

  // ---- drag and drop: files from the desktop upload here; rows dragged onto a folder move into it ----
  const [dropping, setDropping] = useState(false)
  const [dropDir, setDropDir] = useState<string | null>(null)
  const depth = useRef(0)
  const onDragEnter = (e: DragEvent) => hasFiles(e.dataTransfer) && !props.readonly && (depth.current++, setDropping(true))
  const onDragLeave = (e: DragEvent) => hasFiles(e.dataTransfer) && --depth.current <= 0 && ((depth.current = 0), setDropping(false))
  const onDrop = async (e: DragEvent) => {
    depth.current = 0
    setDropping(false)
    if (!hasFiles(e.dataTransfer) || props.readonly) return
    e.preventDefault()
    uploadFiles(root, path, await filesFromDrop(e.dataTransfer))
  }
  const dragRow = (e: FsEntry) => ({
    draggable: !props.readonly && canWrite(e),
    onDragStart: (ev: DragEvent) => {
      const list = selected.has(e.path) ? picked : [e]
      ev.dataTransfer.setData(DRAG_TYPE, JSON.stringify(list.map((x) => x.path)))
      ev.dataTransfer.effectAllowed = 'move'
    },
  })
  const dropOn = (dest: string) => ({
    onDragOver: (ev: DragEvent) => ev.dataTransfer.types.includes(DRAG_TYPE) && (ev.preventDefault(), setDropDir(dest)),
    onDragLeave: () => setDropDir((d) => (d === dest ? null : d)),
    onDrop: (ev: DragEvent) => {
      const raw = ev.dataTransfer.getData(DRAG_TYPE)
      setDropDir(null)
      if (!raw) return
      ev.preventDefault()
      ev.stopPropagation()
      const paths: string[] = JSON.parse(raw)
      const list = [...(dir?.dirs ?? []), ...(dir?.files ?? []), ...entries].filter((x, i, a) => paths.includes(x.path) && a.findIndex((y) => y.path === x.path) === i)
      moveTo(list, dest)
    },
  })

  // ---- toolbar pieces ----
  const filesInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const printInput = useRef<HTMLInputElement>(null)
  const parts = path ? path.split('/') : []
  const crumbs = (
    <nav className="flex min-w-0 flex-1 flex-wrap items-center gap-0.5" aria-label={t('files.browser.folder')}>
      <a href={dirHref?.('')} onClick={dirHref ? undefined : (ev) => (ev.preventDefault(), go(''))} {...dropOn('')} className={`btn btn-ghost btn-sm ${parts.length ? '' : 'text-ink!'} ${dropDir === '' ? 'bg-accent-tint!' : ''}`}>
        <House size={14} /> {label}
      </a>
      {parts.map((p, i) => {
        const to = parts.slice(0, i + 1).join('/')
        return (
          <span key={to} className="flex min-w-0 items-center gap-0.5">
            <ChevronRight size={14} className="flex-none text-ink-3" />
            <a href={dirHref?.(to)} onClick={dirHref ? undefined : (ev) => (ev.preventDefault(), go(to))} {...dropOn(to)} className={`btn btn-ghost btn-sm max-w-[220px] ${i === parts.length - 1 ? 'text-ink!' : ''} ${dropDir === to ? 'bg-accent-tint!' : ''}`}>
              <span className="truncate">{p}</span>
            </a>
          </span>
        )
      })}
    </nav>
  )

  const sortOptions: Column[] = [{ value: 'name', label: t('files.browser.name'), visible: true }, ...columns.filter((c) => c.value === 'modified' || c.value === 'size' || c.visible)]
  const sortPicker = (
    <div className={`flex items-center gap-1 ${view === 'list' ? 'md:hidden' : ''}`}>
      <select className="field h-[30px] w-auto py-0 text-[12.5px]" value={sortBy} onChange={(e) => setSortBy(e.target.value)} aria-label={t('files.browser.sort_by')}>
        {sortOptions.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
      <button className="iconbtn" onClick={() => setSortDesc(!sortDesc)} aria-label={sortDesc ? t('files.browser.descending') : t('files.browser.ascending')} title={sortDesc ? t('files.browser.descending') : t('files.browser.ascending')}>
        {sortDesc ? <ArrowDown size={16} /> : <ArrowUp size={16} />}
      </button>
    </div>
  )

  const toolbar = picked.length ? (
    <div className="flex flex-wrap items-center gap-2 rounded-xl bg-accent-tint px-2.5 py-2">
      <button className="iconbtn" onClick={() => setSelected(new Set())} aria-label={t('files.browser.clear_selection')}>
        <X size={16} />
      </button>
      <b className="mr-auto">{t('files.browser.selected', { count: picked.length })}</b>
      {gcodes && hasQueue && picked.some((e) => isPrintable(e.name)) && (
        <button className="btn btn-sm" onClick={() => enqueue(picked)}>
          <ListPlus size={14} /> {t('files.browser.add_to_queue')}
        </button>
      )}
      <button className="btn btn-sm" onClick={() => download(picked)}>
        <Download size={14} /> {t('files.browser.download')}
      </button>
      {!props.readonly && (
        <>
          <button className="btn btn-sm" onClick={() => setMoving(picked)}>
            <FolderInput size={14} /> {t('files.browser.move')}
          </button>
          <button className="btn btn-sm btn-danger" onClick={() => remove(picked)}>
            <Trash2 size={14} /> {t('common.delete')}
          </button>
        </>
      )}
      <button className="iconbtn" onClick={(e) => setMenu({ x: e.clientX, y: e.clientY, items: picked })} aria-label={t('files.browser.more')}>
        <Ellipsis size={16} />
      </button>
    </div>
  ) : (
    <div className="flex flex-wrap items-center gap-2">
      <label className="relative min-w-[180px] flex-1">
        <Search size={15} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
        <input className="field pr-8 pl-8" placeholder={q ? '' : t('files.browser.search')} value={query} onChange={(e) => setQuery(e.target.value)} />
        {query && (
          <button className="iconbtn absolute top-1/2 right-1 h-6 w-6 -translate-y-1/2" onClick={() => setQuery('')} aria-label={t('files.browser.clear_search')}>
            <X size={14} />
          </button>
        )}
      </label>
      <div className="flex flex-wrap items-center gap-1.5">
        {sortPicker}
        <Segmented
          value={view}
          onChange={(v) => setView(v)}
          label={t('files.browser.view')}
          options={[
            { value: 'list', label: <List size={16} aria-label={t('files.browser.list')} /> },
            { value: 'grid', label: <LayoutGrid size={16} aria-label={t('files.browser.grid')} /> },
          ]}
        />
        <Popover label={t('files.browser.filters')} button={<Filter size={16} className={filters.length ? 'text-accent-ink' : ''} />} className="iconbtn">
          <div className="section-label px-2.5 pt-1 pb-1">{t('files.browser.filters')}</div>
          {props.filterTypes.map((f) => (
            <Check key={f} checked={filters.includes(f)} onChange={(on) => setFilters(on ? [...filters, f] : filters.filter((x) => x !== f))}>
              {filterLabel(f)}
            </Check>
          ))}
        </Popover>
        {view === 'list' && (
          <span className="hidden md:inline-flex">
            <Popover label={t('files.browser.columns')} button={<Columns3 size={16} />} className="iconbtn">
              <div className="section-label px-2.5 pt-1 pb-1">{t('files.browser.columns')}</div>
              {columns.map((c) => (
                <Check key={c.value} checked={c.visible} onChange={(on) => setSavedHeaders(columns.map((x) => ({ value: x.value, visible: x.value === c.value ? on : x.visible })))}>
                  {c.label}
                </Check>
              ))}
            </Popover>
          </span>
        )}
        {props.readonly ? (
          <span className="pill tone-muted">
            <Lock size={12} /> {t('files.read_only')}
          </span>
        ) : (
          <>
            <Popover label={t('common.add')} button={<Plus size={17} />} className="iconbtn">
              <MenuLine icon={<FilePlus size={15} />} onClick={newFile}>
                {t('files.browser.add_file')}
              </MenuLine>
              <MenuLine icon={<FolderPlus size={15} />} onClick={newFolder}>
                {t('files.browser.add_dir')}
              </MenuLine>
              <MenuLine icon={<Upload size={15} />} onClick={() => filesInput.current?.click()}>
                {t('files.browser.upload_files')}
              </MenuLine>
              <MenuLine icon={<FolderUp size={15} />} onClick={() => folderInput.current?.click()}>
                {t('files.browser.upload_folder')}
              </MenuLine>
              {gcodes && (
                <MenuLine icon={<Play size={15} />} onClick={() => printInput.current?.click()} disabled={busy || !klippyReady}>
                  {t('files.browser.upload_and_print')}
                </MenuLine>
              )}
            </Popover>
            <button className="btn btn-primary btn-sm h-[30px]" onClick={() => filesInput.current?.click()} aria-label={t('files.browser.upload_files')}>
              <Upload size={14} /> <span className="hidden sm:inline">{t('files.browser.upload')}</span>
            </button>
          </>
        )}
      </div>
      <input ref={filesInput} type="file" multiple hidden accept={gcodes ? props.accepts.join(',') : undefined} onChange={(e) => (e.target.files && uploadFiles(root, path, filesWithPath(e.target.files)), (e.target.value = ''))} />
      <input ref={folderInput} type="file" multiple hidden {...{ webkitdirectory: '' }} onChange={(e) => (e.target.files && uploadFiles(root, path, filesWithPath(e.target.files)), (e.target.value = ''))} />
      <input ref={printInput} type="file" hidden accept={props.accepts.join(',')} onChange={(e) => (e.target.files?.[0] && uploadFiles(root, path, [e.target.files[0]], true), (e.target.value = ''))} />
    </div>
  )

  // ---- body ----
  const empty = !loading && entries.length === 0
  const listBody = (
    <>
      <div className="hidden overflow-x-auto md:block">
        <table className="table">
          <thead>
            <tr>
              <th className="w-8 pr-0!">
                <input type="checkbox" className="h-4 w-4 accent-[var(--accent)]" aria-label={t('files.browser.select_all')} checked={entries.length > 0 && picked.length === entries.length} onChange={(e) => setSelected(new Set(e.target.checked ? entries.map((x) => x.path) : []))} />
              </th>
              <SortHead value="name" label={t('files.browser.name')} sortBy={sortBy} desc={sortDesc} onSort={(by, desc) => (setSortBy(by), setSortDesc(desc))} />
              {shownColumns.map((c) => (
                <SortHead key={c.value} value={c.value} label={c.label} numeric={c.numeric} sortBy={sortBy} desc={sortDesc} onSort={(by, desc) => (setSortBy(by), setSortDesc(desc))} />
              ))}
              <th className="sticky right-0 w-10 bg-surface" />
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr
                key={e.path}
                {...dragRow(e)}
                {...(e.kind === 'dir' ? dropOn(e.path) : {})}
                className={`cursor-pointer ${selected.has(e.path) ? '[&>td]:bg-accent-tint!' : ''} ${dropDir === e.path ? '[&>td]:bg-accent-tint!' : ''}`}
                onClick={(ev) => (ev.shiftKey || ev.metaKey || ev.ctrlKey ? select(e, !selected.has(e.path), ev.shiftKey) : open(e, ev.clientX, ev.clientY))}
                onContextMenu={(ev) => (ev.preventDefault(), openMenu(e, ev.clientX, ev.clientY))}
              >
                <td className="pr-0!" onClick={(ev) => ev.stopPropagation()}>
                  <input type="checkbox" className="h-4 w-4 accent-[var(--accent)]" aria-label={t('files.browser.select', { name: e.name })} checked={selected.has(e.path)} onChange={(ev) => select(e, ev.target.checked, (ev.nativeEvent as MouseEvent).shiftKey)} />
                </td>
                <td className="max-w-[420px] min-w-[200px]">
                  <NameCell root={root} entry={e} searching={!!q} />
                </td>
                {shownColumns.map((c) => (
                  <td key={c.value} className={`whitespace-nowrap ${c.numeric ? 'num' : ''} text-ink-2`}>
                    <MetaCell root={root} entry={e} column={c.value} job={jobOf(e)} />
                  </td>
                ))}
                <td className="sticky right-0 bg-surface" onClick={(ev) => ev.stopPropagation()}>
                  <button className="iconbtn h-7 w-7" aria-label={t('files.browser.more_for', { name: e.name })} onClick={(ev) => openMenu(e, ev.clientX, ev.clientY)}>
                    <Ellipsis size={16} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="grid gap-0.5 md:hidden">
        {entries.map((e) => (
          <div key={e.path} className={`flex items-center gap-2 rounded-lg px-1 ${selected.has(e.path) ? 'bg-accent-tint' : ''}`} onContextMenu={(ev) => (ev.preventDefault(), openMenu(e, ev.clientX, ev.clientY))}>
            <input type="checkbox" className="h-4 w-4 flex-none accent-[var(--accent)]" aria-label={t('files.browser.select', { name: e.name })} checked={selected.has(e.path)} onChange={(ev) => select(e, ev.target.checked, false)} />
            <button className="flex min-w-0 flex-1 items-center gap-2.5 border-0 bg-transparent py-2 text-left text-inherit" onClick={(ev) => open(e, ev.clientX, ev.clientY)}>
              <NameCell root={root} entry={e} searching={!!q} sub={<SubLine root={root} entry={e} job={jobOf(e)} />} />
            </button>
            <button className="iconbtn flex-none" aria-label={t('files.browser.more_for', { name: e.name })} onClick={(ev) => openMenu(e, ev.clientX, ev.clientY)}>
              <Ellipsis size={16} />
            </button>
          </div>
        ))}
      </div>
    </>
  )

  // Folders sit in a compact strip above the cards, so a folder doesn't take a whole card's space.
  const gridDirs = entries.filter((e) => e.kind === 'dir')
  const gridFiles = entries.filter((e) => e.kind === 'file')
  const check = (e: FsEntry) => (
    <input type="checkbox" className="h-4 w-4 flex-none accent-[var(--accent)]" checked={selected.has(e.path)} onChange={(ev) => select(e, ev.target.checked, (ev.nativeEvent as MouseEvent).shiftKey)} onClick={(ev) => ev.stopPropagation()} aria-label={t('files.browser.select', { name: e.name })} />
  )
  const gridBody = (
    <div className="grid gap-3">
      {gridDirs.length > 0 && (
        <div className="grid grid-cols-1 gap-2 min-[420px]:grid-cols-2 md:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
          {gridDirs.map((e) => (
            <div
              key={e.path}
              {...dragRow(e)}
              {...dropOn(e.path)}
              className={`group flex items-center gap-2.5 rounded-xl bg-surface py-1 pr-1 pl-3 shadow-[var(--shadow-card)] ${selected.has(e.path) || dropDir === e.path ? 'shadow-[0_0_0_2px_var(--accent)]!' : ''}`}
              onContextMenu={(ev) => (ev.preventDefault(), openMenu(e, ev.clientX, ev.clientY))}
            >
              {check(e)}
              <button className="flex min-w-0 flex-1 items-center gap-2.5 border-0 bg-transparent py-2 text-left text-inherit" onClick={() => go(e.path)}>
                <Folder size={18} className="flex-none text-accent-ink" />
                <b className="truncate text-[13px]">{e.name}</b>
              </button>
              <button className="iconbtn h-8 w-8" aria-label={t('files.browser.more_for', { name: e.name })} onClick={(ev) => openMenu(e, ev.clientX, ev.clientY)}>
                <Ellipsis size={15} />
              </button>
            </div>
          ))}
        </div>
      )}
      {gridFiles.length > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
          {gridFiles.map((e) =>
            gcodes && isPrintable(e.name) ? (
              <div key={e.path} {...dragRow(e)}>
                <FileCard
                  path={e.path}
                  meta={e.meta}
                  last={byFile.get(e.path)?.last}
                  count={byFile.get(e.path)?.count ?? 0}
                  onOpen={() => open(e)}
                  selected={selected.has(e.path)}
                  onSelect={(on, shift) => select(e, on, shift)}
                  onMenu={(x, y) => openMenu(e, x, y)}
                />
              </div>
            ) : (
              <div key={e.path} {...dragRow(e)} className={`group relative rounded-xl ${selected.has(e.path) ? 'shadow-[0_0_0_2px_var(--accent)]' : ''}`} onContextMenu={(ev) => (ev.preventDefault(), openMenu(e, ev.clientX, ev.clientY))}>
                <button className="flex h-full w-full flex-col items-stretch overflow-hidden rounded-xl border-0 bg-surface p-0 text-left text-inherit shadow-[var(--shadow-card)] hover:shadow-[0_0_0_1px_var(--line-strong)]" onClick={(ev) => open(e, ev.clientX, ev.clientY)}>
                  <div className="grid aspect-[4/3] place-items-center overflow-hidden border-b border-line bg-inset">{isImage(e.name) ? <img src={client.fileUrl(root, e.path)} alt="" loading="lazy" className="max-h-[85%] max-w-[85%] object-contain" /> : <EntryIcon entry={e} size={34} />}</div>
                  <div className="grid gap-0.5 px-3 py-2.5">
                    <b className="truncate text-[12.5px]" title={e.path}>
                      {e.name}
                    </b>
                    <span className="truncate text-[12px] text-ink-3">
                      {fmtSize(e.size)} · {fmtDate(e.modified)}
                    </span>
                  </div>
                </button>
                <label className={`absolute top-1.5 left-1.5 grid h-7 w-7 cursor-pointer place-items-center rounded-lg bg-surface/90 shadow-[0_0_0_1px_var(--line)] ${selected.has(e.path) ? '' : 'sm:opacity-0 sm:group-hover:opacity-100 sm:focus-within:opacity-100'}`}>{check(e)}</label>
                <button className="iconbtn absolute top-1.5 right-1.5 h-7 w-7 bg-surface/90 shadow-[0_0_0_1px_var(--line)]" aria-label={t('files.browser.more_for', { name: e.name })} onClick={(ev) => openMenu(e, ev.clientX, ev.clientY)}>
                  <Ellipsis size={15} />
                </button>
              </div>
            ),
          )}
        </div>
      )}
    </div>
  )

  const disk = dir?.disk
  const low = disk ? disk.free < Math.min(1073741824, disk.total * 0.2) : false

  return (
    <Card className="relative" title={title}>
      <div className="relative grid grid-cols-[minmax(0,1fr)] gap-3" onDragEnter={onDragEnter} onDragLeave={onDragLeave} onDragOver={(e) => hasFiles(e.dataTransfer) && !props.readonly && e.preventDefault()} onDrop={onDrop}>
        <div className="flex flex-wrap items-center gap-2">
          {crumbs}
          <span className="text-[12px] font-semibold text-ink-3">
            {loading && <Spinner size={12} className="mr-1.5 align-[-2px]" />}
            {q ? t('files.browser.found', { count: entries.length }) : dir ? t('files.browser.items', { count: entries.length }) : ''}
          </span>
        </div>
        {toolbar}
        {error ? <Empty>{root === 'gcodes' || !dir ? t('files.browser.open_failed', { error }) : error}</Empty> : empty ? <Empty>{q ? t('files.browser.no_results') : gcodes && !path ? t('files.browser.empty_gcodes') : t('files.browser.empty')}</Empty> : view === 'grid' ? gridBody : listBody}
        {disk && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-3 text-[12px] text-ink-3">
            <span className={low ? 'font-semibold text-warn' : ''}>{t(low ? 'files.browser.disk_low' : 'files.browser.disk', { free: fmtSize(disk.free), total: fmtSize(disk.total) })}</span>
            <div className="bar h-1.5 w-[140px]">
              <i style={{ width: `${Math.round((disk.used / disk.total) * 100)}%` }} />
            </div>
          </div>
        )}
        {dropping && (
          <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-xl border-2 border-dashed border-accent bg-[color-mix(in_srgb,var(--surface)_88%,transparent)]">
            <div className="grid place-items-center gap-2 text-center">
              <Upload size={28} className="text-accent-ink" />
              <b className="text-[15px]">{t('files.browser.drop_title')}</b>
              <span className="text-ink-3">{t('files.browser.drop_to', { path: joinPath(label, path) })}</span>
            </div>
          </div>
        )}
      </div>
      {menu && <ContextMenu x={menu.x} y={menu.y} items={menuFor(menu.items)} header={menu.items.length === 1 ? menu.items[0].name : t('files.browser.items', { count: menu.items.length })} onClose={() => setMenu(null)} />}
      {moving && (
        <MoveDialog
          root={root}
          rootLabel={label}
          items={moving}
          from={path}
          onClose={() => setMoving(null)}
          onMove={(dest) => {
            const list = moving
            setMoving(null)
            moveTo(list, dest)
          }}
        />
      )}
      {info && <InfoDialog root={root} entry={info} job={jobOf(info)} onClose={() => setInfo(null)} />}
      {viewing && <ViewDialog root={root} entry={viewing} onClose={() => setViewing(null)} />}
      {editing && (
        <Suspense fallback={null}>
          <EditorDialog root={root} path={editing} onClose={() => setEditing(null)} />
        </Suspense>
      )}
    </Card>
  )
}

// Archives, images, and video aren't text; the editor would show noise.
const isBinary = (name: string) => /\.(3mf|zip|gz|tgz|tar|ufp|png|jpe?g|gif|bmp|webp|mp4|webm|mkv|mov|bin|elf)$/i.test(name)

const f0 = (files: FsEntry[]) => displayName(files[0]?.name ?? '')

function MenuLine({ icon, children, onClick, disabled }: { icon: ReactNode; children: ReactNode; onClick: () => void; disabled?: boolean }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick}>
      {icon}
      {children}
    </button>
  )
}

function SortHead({ value, label, numeric, sortBy, desc, onSort }: { value: string; label: string; numeric?: boolean; sortBy: string; desc: boolean; onSort: (by: string, desc: boolean) => void }) {
  const on = sortBy === value
  return (
    <th aria-sort={on ? (desc ? 'descending' : 'ascending') : undefined}>
      <button className={`inline-flex items-center gap-1 border-0 bg-transparent p-0 text-[11px] font-bold tracking-[0.06em] uppercase ${on ? 'text-accent-ink' : 'text-ink-3 hover:text-ink'}`} onClick={() => onSort(value, on ? !desc : !!numeric)}>
        {label}
        {on && (desc ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}
      </button>
    </th>
  )
}

/** G-code metadata for a row, from the folder listing or, for search results, fetched when shown. */
function useEntryMeta(root: string, e: FsEntry) {
  const fetched = useFileMeta(root === 'gcodes' && e.kind === 'file' && !e.meta && isPrintable(e.name) ? e.path : null)
  return e.meta ?? fetched ?? undefined
}

function EntryIcon({ entry, size = 18 }: { entry: FsEntry; size?: number }) {
  if (entry.kind === 'dir') return <Folder size={size} className="text-accent-ink" />
  if (isImage(entry.name)) return <Image size={size} className="text-ink-3" />
  if (/\.(zip|gz|tar|tgz)$/i.test(entry.name)) return <FileArchive size={size} className="text-ink-3" />
  if (editsOnClick(entry.name)) return <FileText size={size} className="text-ink-3" />
  return <File size={size} className="text-ink-3" />
}

function NameCell({ root, entry, searching, sub }: { root: string; entry: FsEntry; searching: boolean; sub?: ReactNode }) {
  const meta = useEntryMeta(root, entry)
  const thumb = root === 'gcodes' && entry.kind === 'file' ? thumbnailUrl(entry.path, meta, 32) : null
  const locked = isLocked(root, entry.path) || entry.permissions === 'r'
  return (
    <span className="flex min-w-0 flex-1 items-center gap-2.5">
      <span className="grid h-9 w-9 flex-none place-items-center overflow-hidden rounded-md bg-inset shadow-[inset_0_0_0_1px_var(--line)]">{thumb ? <img src={thumb} alt="" loading="lazy" className="max-h-[90%] max-w-[90%] object-contain" /> : <EntryIcon entry={entry} />}</span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className="truncate font-medium" title={entry.path}>
            {entry.name}
          </span>
          {locked && !rootProps(root).readonly && <Lock size={12} className="flex-none text-ink-3" aria-label={t('files.read_only')} />}
        </span>
        {searching && parentOf(entry.path) && <span className="block truncate text-[12px] text-ink-3">{parentOf(entry.path)}</span>}
        {sub}
      </span>
    </span>
  )
}

function MetaCell({ root, entry, column, job }: { root: string; entry: FsEntry; column: string; job?: HistoryJob }) {
  const meta = useEntryMeta(root, entry)
  return <Cell entry={meta && !entry.meta ? { ...entry, meta } : entry} column={column} job={job} />
}

function SubLine({ root, entry, job }: { root: string; entry: FsEntry; job?: HistoryJob }) {
  const meta = useEntryMeta(root, entry)
  if (entry.kind === 'dir') return <span className="block text-[12px] text-ink-3">{t('files.browser.folder')}</span>
  const parts: ReactNode[] = []
  if (meta?.estimated_time != null) parts.push(fmtDuration(Number(meta.estimated_time)))
  if (meta?.filament_type) parts.push([...new Set(String(meta.filament_type).split(';'))].join(', '))
  parts.push(fmtSize(entry.size))
  if (!meta) parts.push(fmtDate(entry.modified))
  return (
    <span className="block truncate text-[12px] text-ink-3">
      {parts.join(' · ')}
      {job && <span className={job.status === 'completed' ? 'text-good' : job.status === 'cancelled' ? '' : 'text-bad'}> · {job.status === 'completed' ? t('files.browser.printed') : job.status === 'cancelled' ? t('files.outcome.cancelled') : job.status === 'in_progress' ? t('files.outcome.printing') : t('files.outcome.failed')}</span>}
    </span>
  )
}

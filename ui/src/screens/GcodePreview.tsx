import { useEffect, useState } from 'react'
import { ArrowLeft, RotateCw } from 'lucide-react'
import { Empty, PageHeader } from '../components/ui'
import { href } from '../lib/router'
import { displayName, fmtBytes } from '../lib/format'
import { fetchMeta, type FileMeta } from '../state/files'
import { useConnected, usePrinter } from '../state/printer'
import { isActive } from '../state/job'
import { parentOf } from '../components/files/fs'
import { loadGcode, useAutoLoad, usePreviewFile } from '../components/gcode-preview/loader'
import { usePreviewSettings } from '../components/gcode-preview/settings'
import { GcodeViewer } from '../components/gcode-preview/GcodeViewer'
import { LoadState } from '../components/gcode-preview/LoadState'
import { t } from '../i18n'
import '../components/gcode-preview/preview.css'

/** The G-code viewer for one file, at #/files/preview/<path>. */
export function GcodePreview({ path }: { path: string }) {
  const filesRev = usePrinter((s) => s.filesRev)
  const open = useConnected()
  const printing = usePrinter((s) => s.status.print_stats?.filename === path && isActive(s.status.print_stats?.state))
  // undefined while loading, null when Moonraker doesn't know the file.
  const [meta, setMeta] = useState<FileMeta | null | undefined>(undefined)
  useEffect(() => {
    if (!open) return
    let live = true
    // Forced, as a lookup made before the connection opened is cached as missing.
    fetchMeta(path, true).then((m) => live && setMeta(m))
    return () => {
      live = false
    }
  }, [path, filesRev, open])

  const file = usePreviewFile(path)
  useAutoLoad(path, meta?.size, meta?.modified)
  const [settings, set] = usePreviewSettings()
  const data = file?.phase === 'ready' ? file.data : null

  // Back to the file's folder.
  const dir = parentOf(path)
  const title = (
    <span className="flex min-w-0 items-center gap-2">
      <a href={dir ? href('files', 'gcodes', ...dir.split('/')) : href('files')} className="iconbtn" aria-label={t('preview.screen.back')} title={t('files.screen.title')}>
        <ArrowLeft size={18} />
      </a>
      <span className="truncate" title={path}>
        {displayName(path)}
      </span>
    </span>
  )

  let body
  if (meta === null) body = <Empty>{t('preview.screen.no_file', { path })}</Empty>
  else if (data && data.moves.length === 0) body = <Empty>{t('preview.no_moves')}</Empty>
  else if (data) body = <GcodeViewer path={path} meta={meta} data={data} settings={settings} set={set} />
  else
    body = (
      <div className="grid gap-3.5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="gp-view">
          <div className="gp-overlay">
            <LoadState file={file} size={meta?.size} modified={meta?.modified} />
          </div>
        </div>
      </div>
    )

  return (
    <>
      <PageHeader title={title}>
        {printing && <span className="pill tone-accent">{t('preview.printing')}</span>}
        {meta?.size != null && <span className="chip num hidden sm:inline-flex">{fmtBytes(meta.size)}</span>}
        {meta?.slicer && <span className="chip hidden sm:inline-flex">{meta.slicer}</span>}
        {data && meta?.size != null && meta.modified != null && (
          <button className="btn btn-sm btn-ghost" onClick={() => loadGcode(path, meta.size!, meta.modified!)} title={t('preview.screen.reload_title')}>
            <RotateCw size={14} />
            <span className="hidden sm:inline">{t('preview.screen.reload')}</span>
          </button>
        )}
      </PageHeader>
      {body}
    </>
  )
}

import { fmtBytes } from '../../lib/format'
import { cancelGcode, loadGcode, type PreviewFile } from './loader'

/** Download and parse progress with Cancel, or what went wrong with a way to try again. */
export function LoadState({ file, size, modified, compact }: { file: PreviewFile | null; size?: number; modified?: number; compact?: boolean }) {
  const load = () => size != null && modified != null && file && loadGcode(file.path, size, modified)
  if (!file || file.phase === 'download' || file.phase === 'parse') {
    const downloading = !file || (file.phase === 'download' && file.received < file.size)
    const done = !file ? 0 : downloading ? file.received : file.parsed
    const total = file?.size ?? size ?? 0
    const pct = total ? Math.min(100, Math.floor((done / total) * 100)) : 0
    return (
      <div className={`grid w-full gap-2 ${compact ? 'max-w-[260px]' : 'max-w-[340px] rounded-xl bg-surface p-4'}`} style={compact ? undefined : { boxShadow: 'var(--shadow-overlay)' }}>
        <div className="flex items-baseline justify-between gap-3">
          <span className="font-semibold">{downloading ? 'Downloading' : 'Parsing file'}</span>
          <span className="text-[12.5px] text-ink-3 num">{pct}%</span>
        </div>
        <div className="bar live">
          <i style={{ width: `${pct}%`, transition: 'width 0.1s' }} />
        </div>
        {!compact && (
          <div className="flex items-center justify-between gap-3">
            <span className="text-[12.5px] text-ink-3 num">{total ? `${fmtBytes(done)} of ${fmtBytes(total)}` : ''}</span>
            {file && (
              <button className="btn btn-sm" onClick={() => cancelGcode(file.path)}>
                Cancel
              </button>
            )}
          </div>
        )}
      </div>
    )
  }
  if (file.phase === 'error' || file.phase === 'cancelled') {
    return (
      <div className="grid max-w-[340px] justify-items-center gap-3 text-center">
        <div className="text-ink-2">{file.phase === 'error' ? `Failed to preview G-code file. ${file.error ?? ''}` : 'Preview cancelled.'}</div>
        <button className="btn btn-sm btn-primary" onClick={load}>
          {file.phase === 'error' ? 'Try again' : 'Load preview'}
        </button>
      </div>
    )
  }
  return null
}

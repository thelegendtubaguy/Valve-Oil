import { fmtBytes } from '../../lib/format'
import { cancelGcode, loadGcode, type PreviewFile } from './loader'
import { t, type MessageKey } from '../../i18n'

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
          <span className="font-semibold">{downloading ? t('preview.load.downloading') : t('preview.load.parsing')}</span>
          <span className="text-[12.5px] text-ink-3 num">{pct}%</span>
        </div>
        <div className="bar live">
          <i style={{ width: `${pct}%`, transition: 'width 0.1s' }} />
        </div>
        {!compact && (
          <div className="flex items-center justify-between gap-3">
            <span className="text-[12.5px] text-ink-3 num">{total ? t('preview.load.progress', { done: fmtBytes(done), total: fmtBytes(total) }) : ''}</span>
            {file && (
              <button className="btn btn-sm" onClick={() => cancelGcode(file.path)}>
                {t('common.cancel')}
              </button>
            )}
          </div>
        )}
      </div>
    )
  }
  if (file.phase === 'error' || file.phase === 'cancelled') {
    // The worker can't show text in the UI's language, so it reports what went wrong by code where it knows it.
    const reason = file.errorCode ? t(`preview.errors.${file.errorCode}` as MessageKey, file.errorVars) : (file.error ?? '')
    return (
      <div className="grid max-w-[340px] justify-items-center gap-3 text-center">
        <div className="text-ink-2">{file.phase === 'error' ? t('preview.load.failed', { reason }) : t('preview.load.cancelled')}</div>
        <button className="btn btn-sm btn-primary" onClick={load}>
          {file.phase === 'error' ? t('preview.load.try_again') : t('preview.load.load')}
        </button>
      </div>
    )
  }
  return null
}

import { useMemo, useState } from 'react'
import { Maximize2 } from 'lucide-react'
import { href } from '../../lib/router'
import { fmtBytes } from '../../lib/format'
import { useFileMeta } from '../../state/files'
import { usePrinter } from '../../state/printer'
import { isActive } from '../../state/job'
import { useAutoLoad, usePreviewFile } from './loader'
import { usePreviewSettings } from './settings'
import { usePreviewModel, usePrintFollow } from './hooks'
import { layerByFilePosition, moveIndexByFilePosition } from './model'
import { PreviewCanvas } from './PreviewCanvas'
import { LoadState } from './LoadState'
import { t } from '../../i18n'
import './preview.css'

// Files above this wait for a tap before loading, so opening Home on a tablet doesn't pull a huge file each time.
const AUTO_LOAD_BYTES = 25 * 1024 * 1024

/**
 * The file being printed at its current layer, for Home's print card: fitted to the print, following the printer's
 * position, and opening the full preview when tapped. Renders nothing when no print is running.
 */
export function LivePreview({ className = 'aspect-square' }: { className?: string }) {
  const filename = usePrinter((s) => s.status.print_stats?.filename ?? '')
  const state = usePrinter((s) => s.status.print_stats?.state ?? 'standby')
  const active = !!filename && isActive(state)
  const path = active ? filename : null
  const meta = useFileMeta(path)
  const [asked, setAsked] = useState(false)
  const big = (meta?.size ?? 0) > AUTO_LOAD_BYTES
  useAutoLoad(path, meta?.size, meta?.modified, !big || asked)
  const file = usePreviewFile(path)
  const [stored] = usePreviewSettings()
  // Small, so only what's printed: no travel moves or retraction marks.
  const settings = useMemo(() => ({ ...stored, showMoves: false, showRetractions: false, showNextLayer: false, drawOrigin: false }), [stored])
  const data = file?.phase === 'ready' ? file.data : null
  const { layers, bounds, colors, bed, isDark } = usePreviewModel(meta, data, settings)
  const print = usePrintFollow(path)
  const toolhead = useMemo(() => (print.x != null && print.y != null ? { x: print.x, y: print.y } : null), [print.x, print.y])

  if (!path) return null
  const layer = data && layers.length ? layerByFilePosition(layers, print.filePosition) : 0
  const moveEnd = data ? moveIndexByFilePosition(data.moves, print.filePosition) : 0
  const link = href('files', 'preview', ...path.split('/'))

  return (
    <div className={`relative overflow-hidden rounded-xl bg-canvas ${className}`} style={{ boxShadow: 'inset 0 0 0 1px var(--line)' }}>
      {data && data.moves.length > 0 ? (
        <a href={link} aria-label={t('preview.live.open')} className="absolute inset-0 block">
          <PreviewCanvas
            className="absolute inset-0"
            data={data}
            layers={layers}
            bounds={bounds}
            bed={bed}
            layer={layer}
            moveEnd={moveEnd}
            settings={settings}
            colors={colors}
            isDark={isDark}
            objects={print.objects}
            toolhead={toolhead}
            interactive={false}
            fitPrint
          />
          <span className="gp-badge num">{t('preview.layer_of', { layer: layer + 1, total: layers.length })}</span>
          <span className="iconbtn absolute top-2 right-2" style={{ background: 'color-mix(in srgb, var(--surface) 85%, transparent)' }}>
            <Maximize2 size={15} />
          </span>
        </a>
      ) : (
        <div className="absolute inset-0 grid place-items-center p-4 text-center">
          {big && !asked && !file ? (
            <div className="grid justify-items-center gap-2">
              <span className="text-[12.5px] text-ink-3">{t('preview.live.file_size', { size: fmtBytes(meta!.size!) })}</span>
              <button className="btn btn-sm" onClick={() => setAsked(true)}>
                {t('preview.load.load')}
              </button>
            </div>
          ) : data ? (
            <span className="text-[12.5px] text-ink-3">{t('preview.no_moves')}</span>
          ) : (
            <LoadState file={file} size={meta?.size} modified={meta?.modified} compact />
          )}
        </div>
      )}
    </div>
  )
}

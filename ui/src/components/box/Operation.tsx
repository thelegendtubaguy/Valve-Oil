// A running Box flow, or its failed step with Retry and Stop, at the top of the Box card.
import { useState } from 'react'
import { Box as BoxIcon, CircleAlert, LoaderCircle, RefreshCw, Square } from 'lucide-react'
import { attempt } from '../toast'
import { boxCommands, type BoxState } from '../../state/box'
import { runGcode, usePrinter } from '../../state/printer'
import { t } from '../../i18n'

/** A running Box flow, or the failed step with Retry and Stop, as the fork's QIDI Box card shows them. */
export function Operation({ box }: { box: BoxState }) {
  const [pending, setPending] = useState(false)
  const ready = usePrinter((s) => s.klippy === 'ready')
  const send = async (g: string) => {
    setPending(true)
    await attempt(() => runGcode(g))
    setPending(false)
  }
  const bad = box.flowPaused
  return (
    <div className={`rounded-[14px] px-4 py-3 ${bad ? 'tone-bad' : 'tone-accent'}`} style={{ boxShadow: `inset 0 0 0 1px ${bad ? 'var(--crimson)' : 'color-mix(in srgb, var(--accent) 50%, transparent)'}` }}>
      {bad ? (
        <div className="flex items-start gap-2.5">
          <CircleAlert size={18} className="mt-0.5 flex-none text-bad" />
          <div className="min-w-0 flex-1">
            <div className="font-bold text-ink">{box.taskText}</div>
            {box.problems.map((p) => (
              <div key={p} className="text-ink-2">
                {p}
              </div>
            ))}
            <div className="mt-1 text-[12.5px] text-ink-3">{t('box.operation.retry_hint')}</div>
            <div className="mt-2.5 flex flex-wrap gap-2">
              <button className="btn btn-sm btn-primary" disabled={!ready || pending} onClick={() => send(boxCommands.retry())}>
                <RefreshCw size={13} /> {t('common.retry')}
              </button>
              <button className="btn btn-sm" disabled={!ready || pending} onClick={() => send(boxCommands.stop())}>
                <Square size={12} /> {t('box.operation.stop')}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-2.5">
          <LoaderCircle size={17} className="spin flex-none text-accent-ink" />
          <span className="min-w-0 flex-1">
            <span className="section-label mr-2">{t('box.operation.working')}</span>
            <span className="shimmer font-semibold">{box.taskText}</span>
          </span>
          <BoxIcon size={16} className="flex-none text-ink-3" />
        </div>
      )}
    </div>
  )
}

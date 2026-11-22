import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp } from 'lucide-react'
import { lineTone, segments, type ConsoleEntry } from './model'
import { fillCommand } from './store'
import { locale, t } from '../../i18n'

const TONE = {
  command: 'text-accent-ink',
  error: 'text-bad font-semibold',
  info: 'text-ink-2',
  plain: 'text-ink',
} as const

const time = (t: number) => new Date(t * 1000).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' })

/** One console entry: a sent command (click to send again), or a reply with the commands it names clickable. */
export const ConsoleLineView = memo(function ConsoleLineView({ entry, known, search, showTime }: { entry: ConsoleEntry; known: Set<string>; search?: string; showTime?: boolean }) {
  const error = entry.type === 'response' && entry.message.startsWith('!!')
  return (
    <div className={`flex gap-3 rounded px-1 ${error ? 'bg-bad-tint' : ''}`}>
      {showTime && entry.time > 0 && <span className="hidden flex-none text-ink-3 select-none sm:inline">{time(entry.time)}</span>}
      <div className="min-w-0 flex-1 break-words whitespace-pre-wrap">
        {entry.type === 'command' ? (
          <button type="button" className="console-cmd cursor-pointer border-0 bg-transparent p-0 text-left font-[inherit] text-accent-ink" onClick={() => fillCommand(entry.message)} title={t('console.put_in_line')}>
            <span className="select-none">$ </span>
            {segments(entry.message, EMPTY, search).map((s, i) => (s.mark ? <mark key={i}>{s.text}</mark> : s.text))}
          </button>
        ) : (
          entry.message.split('\n').map((line, i) => (
            <div key={i} className={TONE[lineTone(entry, line)]}>
              {segments(line, known, search).map((s, j) => {
                const text = s.mark ? <mark>{s.text}</mark> : s.text
                return s.command ? (
                  <button key={j} type="button" className="console-cmd cursor-pointer border-0 bg-transparent p-0 font-[inherit] text-accent-ink underline decoration-dotted underline-offset-2" onClick={() => fillCommand(s.command!)} title={t('console.put_in_line')}>
                    {text}
                  </button>
                ) : (
                  <span key={j}>{text}</span>
                )
              })}
            </div>
          ))
        )}
      </div>
    </div>
  )
})
const EMPTY = new Set<string>()

/**
 * Scrollback that follows new lines until you scroll away from them, then offers a way back. Flipped puts the
 * newest line at the top, as Fluidd's Flip layout does.
 */
export function ConsoleOutput({ entries, known, search, flip = false, showTime = true, empty, className = '', style }: { entries: ConsoleEntry[]; known: Set<string>; search?: string; flip?: boolean; showTime?: boolean; empty?: string; className?: string; style?: React.CSSProperties }) {
  const box = useRef<HTMLDivElement>(null)
  const [paused, setPaused] = useState(false)
  const pausedRef = useRef(false)
  const ordered = flip ? [...entries].reverse() : entries

  const toLatest = useCallback(() => {
    const el = box.current
    if (!el) return
    el.scrollTop = flip ? 0 : el.scrollHeight
    pausedRef.current = false
    setPaused(false)
  }, [flip])

  useLayoutEffect(() => {
    if (!pausedRef.current) toLatest()
  }, [entries, toLatest])
  // A new layout starts at the latest line.
  useEffect(() => toLatest(), [flip, toLatest])

  const onScroll = () => {
    const el = box.current
    if (!el) return
    const away = flip ? el.scrollTop > 24 : el.scrollHeight - el.scrollTop - el.clientHeight > 24
    if (away !== pausedRef.current) {
      pausedRef.current = away
      setPaused(away)
    }
  }

  return (
    <div className={`relative min-h-0 ${className}`} style={style}>
      <div ref={box} onScroll={onScroll} className="console-out h-full overflow-auto rounded-lg bg-inset px-2 py-2 font-mono text-[12px] leading-[1.55] shadow-[inset_0_0_0_1px_var(--line)]" role="log" aria-live="off">
        {ordered.length === 0 && <div className="px-1 text-ink-3">{empty ?? t('console.empty')}</div>}
        {ordered.map((e) => (
          <ConsoleLineView key={e.id} entry={e} known={known} search={search} showTime={showTime} />
        ))}
      </div>
      {paused && (
        <button type="button" className="btn btn-sm absolute right-3 shadow-lg" style={flip ? { top: 10 } : { bottom: 10 }} onClick={toLatest}>
          {flip ? <ArrowUp size={14} /> : <ArrowDown size={14} />} {t('console.scroll_latest')}
        </button>
      )}
    </div>
  )
}

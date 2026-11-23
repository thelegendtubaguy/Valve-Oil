import { useEffect, useRef, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { useStore } from '../../lib/store'
import { useConnected } from '../../state/printer'
import { applyCompletion, completions, currentCommand } from './model'
import { draftStore, sendCommand, useCommandHistory, useGcodeHelp } from './store'
import { t } from '../../i18n'

/**
 * The command line: Enter sends, Shift+Enter or a paste adds lines, Up and Down recall what was sent (shared with
 * Fluidd), and Tab completes a command from the ones Klipper knows, with its help shown.
 */
export function CommandInput({ autoFocus, compact, dropUp = true }: { autoFocus?: boolean; compact?: boolean; dropUp?: boolean }) {
  const connected = useConnected()
  const { help, names } = useGcodeHelp()
  const [history, setHistory] = useCommandHistory()
  const focusReq = useStore(draftStore, (d) => d.focus)
  const [value, setValue] = useState(() => draftStore.get().text)
  const [caret, setCaret] = useState(() => draftStore.get().text.length)
  const [open, setOpen] = useState(false)
  const [sel, setSel] = useState(0)
  const hist = useRef({ index: -1, saved: '' })
  const field = useRef<HTMLTextAreaElement>(null)
  const seenFocus = useRef(focusReq)

  // Clicking a command in the output puts it here.
  useEffect(() => {
    if (focusReq === seenFocus.current) return
    seenFocus.current = focusReq
    const text = draftStore.get().text
    setValue(text)
    setCaret(text.length)
    setOpen(false)
    hist.current.index = -1
    const el = field.current
    if (el) {
      el.focus()
      requestAnimationFrame(() => el.setSelectionRange(text.length, text.length))
    }
  }, [focusReq])

  const { matches } = completions(value, caret, names)
  const shown = open ? matches.slice(0, 8) : []
  const cmd = currentCommand(value, caret)
  const cmdHelp = help[cmd]?.split('\n')[0]
  const lines = value.split('\n').length

  const set = (text: string, keepHistory = false) => {
    setValue(text)
    setCaret(text.length)
    draftStore.set((d) => ({ ...d, text }))
    if (!keepHistory) hist.current.index = -1
  }

  const send = () => {
    const text = value.trim()
    if (!text) return
    set('')
    setOpen(false)
    sendCommand(text, history, setHistory)
  }

  const recall = (dir: 1 | -1) => {
    const h = hist.current
    const next = h.index + dir
    if (next < -1 || next >= history.length) return
    if (h.index === -1) h.saved = value
    h.index = next
    set(next === -1 ? h.saved : history[next], true)
    setOpen(false)
    requestAnimationFrame(() => field.current?.setSelectionRange(field.current.value.length, field.current.value.length))
  }

  const take = (c: string) => {
    set(applyCompletion(value, c))
    setOpen(false)
    setSel(0)
    field.current?.focus()
  }

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    const el = e.currentTarget
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      // Enter sends what's typed; Tab takes a completion.
      e.preventDefault()
      return send()
    }
    if (e.key === 'Tab' && !e.shiftKey) {
      // Tab never leaves the field while there's something to complete.
      if (!matches.length) return
      e.preventDefault()
      if (matches.length === 1) return take(matches[0])
      if (open) return take(shown[sel] ?? shown[0])
      setOpen(true)
      setSel(0)
      return
    }
    if (e.key === 'Escape' && open) return e.preventDefault(), setOpen(false)
    if (shown.length && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
      e.preventDefault()
      setSel((i) => (i + (e.key === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length)
      return
    }
    const collapsed = el.selectionStart === el.selectionEnd
    if (e.key === 'ArrowUp' && collapsed && !el.value.slice(0, el.selectionStart).includes('\n')) return e.preventDefault(), recall(1)
    if (e.key === 'ArrowDown' && collapsed && !el.value.slice(el.selectionEnd).includes('\n')) return e.preventDefault(), recall(-1)
  }

  return (
    <div className="relative">
      {shown.length > 0 && (
        <div className={`absolute inset-x-0 z-30 overflow-hidden rounded-xl bg-surface py-1 ${dropUp ? 'bottom-full mb-1.5' : 'top-full mt-1.5'}`} style={{ boxShadow: 'var(--shadow-overlay)' }} role="listbox">
          {shown.map((c, i) => (
            <button
              key={c}
              type="button"
              role="option"
              aria-selected={i === sel}
              onMouseDown={(e) => (e.preventDefault(), take(c))}
              onMouseEnter={() => setSel(i)}
              className={`flex w-full items-baseline gap-2.5 border-0 px-3 py-1.5 text-left ${i === sel ? 'bg-accent-tint' : 'bg-transparent'}`}
            >
              <span className="flex-none font-mono text-[12px] font-semibold text-ink">{c}</span>
              <span className="min-w-0 truncate text-[12px] text-ink-3">{help[c]?.split('\n')[0] ?? ''}</span>
            </button>
          ))}
          {matches.length > shown.length && <div className="px-3 pt-1 pb-0.5 text-[11.5px] text-ink-3">{t('console.input.more', { n: matches.length - shown.length })}</div>}
        </div>
      )}
      <form className="flex items-end gap-1.5" onSubmit={(e) => (e.preventDefault(), send())}>
        <textarea
          ref={field}
          className={`field resize-none font-mono leading-[1.45] ${compact ? 'text-[12.5px]' : 'text-[13px]'}`}
          rows={Math.min(8, lines)}
          placeholder={t('console.input.placeholder')}
          value={value}
          autoFocus={autoFocus}
          onChange={(e) => {
            set(e.target.value)
            setCaret(e.target.selectionStart)
            setSel(0)
            // Suggest while a command is being typed; a pasted script doesn't need them.
            setOpen(!e.target.value.includes('\n'))
          }}
          onSelect={(e) => setCaret(e.currentTarget.selectionStart)}
          onKeyDown={onKey}
          onBlur={() => setOpen(false)}
          spellCheck={false}
          autoCapitalize="characters"
          autoComplete="off"
          autoCorrect="off"
          aria-label={t('console.input.label')}
          disabled={!connected}
        />
        <button className={`btn btn-primary ${compact ? 'px-2.5!' : ''}`} disabled={!value.trim() || !connected} aria-label={t('console.input.send')}>
          {compact ? <ChevronRight size={16} /> : t('console.input.send')}
        </button>
      </form>
      {(!compact || (cmd && cmdHelp)) && (
        <div className="mt-1 flex min-h-[18px] items-baseline gap-2 px-0.5 text-[11.5px] text-ink-3">
          {cmd && cmdHelp ? (
            <span className="min-w-0 truncate">
              <b className="font-mono font-semibold text-ink-2">{cmd}</b> {cmdHelp}
            </span>
          ) : (
            <span className="max-sm:hidden">{t('console.input.hint')}</span>
          )}
        </div>
      )}
    </div>
  )
}

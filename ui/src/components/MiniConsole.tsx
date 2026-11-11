import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight, Terminal } from 'lucide-react'
import { client, runGcode, usePrinter } from '../state/printer'

// Temperature reports and bare acknowledgements would drown out everything else.
const NOISE = /^(ok$|B:|T:|T0:|\/\/ (Klipper state|probe at))/i

let helpCache: Record<string, string> | null = null

/** Recent printer messages, a command line with completion, and the printer's macros. */
export function MiniConsole({ className = '' }: { className?: string }) {
  const lines = usePrinter((s) => s.console)
  const commands = usePrinter((s) => s.commands)
  const macros = usePrinter((s) =>
    s.objects
      .filter((o) => o.startsWith('gcode_macro ') && !o.slice(12).startsWith('_'))
      .map((o) => o.slice(12).toUpperCase())
      .sort(),
  )
  const [input, setInput] = useState('')
  const [hist, setHist] = useState<string[]>([])
  const [hIdx, setHIdx] = useState(-1)
  const [help, setHelp] = useState<Record<string, string>>(helpCache ?? {})
  const [sel, setSel] = useState(0)
  const [macroFilter, setMacroFilter] = useState('')
  const list = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (helpCache) return
    client
      .call<Record<string, string>>('printer.gcode.help')
      .then((h) => {
        helpCache = Object.fromEntries(Object.entries(h).map(([k, v]) => [k.toUpperCase(), v]))
        setHelp(helpCache)
      })
      .catch(() => {})
  }, [])

  const shown = useMemo(() => lines.filter((l) => !NOISE.test(l.text.trim())).slice(-120), [lines])
  useEffect(() => {
    const el = list.current
    if (el) el.scrollTop = el.scrollHeight
  }, [shown.length])

  const word = input.trimStart().split(/\s/)[0].toUpperCase()
  const typingCommand = input.length > 0 && !/\s/.test(input.trimStart())
  const suggestions = typingCommand ? commands.filter((c) => c.startsWith(word) && c !== word).slice(0, 6) : []
  const currentHelp = help[word]

  const send = async () => {
    const cmd = input.trim()
    if (!cmd) return
    setHist((h) => [...h.filter((x) => x !== cmd), cmd].slice(-50))
    setHIdx(-1)
    setInput('')
    try {
      await runGcode(cmd)
    } catch {
      /* the error is already in the console */
    }
  }

  const onKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    // Enter sends what's typed; Tab takes the highlighted completion.
    if (e.key === 'Enter') return e.preventDefault(), send()
    if (e.key === 'Tab' && suggestions.length) {
      e.preventDefault()
      setInput(suggestions[sel] + ' ')
      setSel(0)
      return
    }
    if (e.key === 'ArrowDown' && suggestions.length) return e.preventDefault(), setSel((i) => (i + 1) % suggestions.length)
    if (e.key === 'ArrowUp' && !suggestions.length && hist.length) {
      e.preventDefault()
      const i = hIdx < 0 ? hist.length - 1 : Math.max(0, hIdx - 1)
      setHIdx(i)
      setInput(hist[i])
      return
    }
    if (e.key === 'ArrowUp' && suggestions.length) return e.preventDefault(), setSel((i) => (i - 1 + suggestions.length) % suggestions.length)
    if (e.key === 'ArrowDown' && hIdx >= 0) {
      e.preventDefault()
      const i = hIdx + 1
      if (i >= hist.length) {
        setHIdx(-1)
        setInput('')
      } else {
        setHIdx(i)
        setInput(hist[i])
      }
    }
  }

  const filteredMacros = macros.filter((m) => !macroFilter || m.includes(macroFilter.toUpperCase()))

  return (
    <section className={`card grow flex flex-col gap-2.5 ${className}`}>
      <h3 className="card-title m-0!">
        <Terminal size={14} /> Console
      </h3>
      <div ref={list} className="grow-body min-h-[160px] flex-1 overflow-auto rounded-lg bg-inset px-2.5 py-2 font-mono text-[11.5px] leading-relaxed shadow-[inset_0_0_0_1px_var(--line)] max-md:max-h-[300px] md:max-wide:h-0">
        {shown.length === 0 && <div className="text-ink-3">Printer messages show here.</div>}
        {shown.map((l) => (
          <div key={l.id} className={`break-words whitespace-pre-wrap ${l.kind === 'error' ? 'text-bad' : l.kind === 'command' ? 'text-accent-ink' : 'text-ink-2'}`}>
            {l.kind === 'command' ? '› ' : ''}
            {l.text.replace(/^\/\/\s?/, '')}
          </div>
        ))}
      </div>
      <div className="relative">
        {suggestions.length > 0 && (
          <div className="absolute inset-x-0 bottom-full z-20 mb-1 overflow-hidden rounded-lg bg-surface py-1" style={{ boxShadow: 'var(--shadow-overlay)' }}>
            {suggestions.map((c, i) => (
              <button
                key={c}
                type="button"
                onMouseDown={(e) => (e.preventDefault(), setInput(c + ' '), field.current?.focus())}
                className={`flex w-full items-baseline gap-2 border-0 px-2.5 py-1 text-left ${i === sel ? 'bg-accent-tint' : 'bg-transparent'}`}
              >
                <span className="font-mono text-[12px] font-semibold">{c}</span>
                <span className="truncate text-[11.5px] text-ink-3">{help[c] ?? ''}</span>
              </button>
            ))}
          </div>
        )}
        <form
          className="flex items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault()
            send()
          }}
        >
          <input
            ref={field}
            className="field font-mono text-[12.5px]"
            placeholder="Send G-code…"
            value={input}
            onChange={(e) => (setInput(e.target.value), setSel(0))}
            onKeyDown={onKey}
            spellCheck={false}
            autoCapitalize="characters"
            autoComplete="off"
            aria-label="G-code command"
          />
          <button className="btn btn-primary px-2.5!" disabled={!input.trim()} aria-label="Send">
            <ChevronRight size={16} />
          </button>
        </form>
        {currentHelp && !suggestions.length && <div className="mt-1 truncate text-[11.5px] text-ink-3">{currentHelp}</div>}
      </div>
      {macros.length > 0 && (
        <div className="grid gap-1.5">
          <div className="flex items-center gap-2">
            <span className="lbl">Macros</span>
            <span className="text-[11px] text-ink-3">click to put in the command line</span>
            {macros.length > 12 && <input className="field ml-auto w-28 px-2! py-0.5! text-[11.5px]" placeholder="Filter" value={macroFilter} onChange={(e) => setMacroFilter(e.target.value)} aria-label="Filter macros" />}
          </div>
          <div className="flex max-h-[68px] flex-wrap gap-1 overflow-auto">
            {filteredMacros.map((m) => (
              <button key={m} type="button" title={help[m] ?? m} onClick={() => (setInput(m + ' '), field.current?.focus())} className="chip cursor-pointer border-0 font-mono text-[11px] hover:bg-hover">
                {m}
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

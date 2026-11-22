import { useMemo, useState } from 'react'
import { Maximize2, Terminal } from 'lucide-react'
import { usePrinter } from '../state/printer'
import { href } from '../lib/router'
import { ConsoleOutput } from './console/ConsoleOutput'
import { CommandInput } from './console/CommandInput'
import { fillCommand, useConsoleEntries, useConsoleSettings, useGcodeHelp } from './console/store'
import './console/console.css'
import { FoldTitle, useFold } from './home-deck/fold'

/** Recent printer messages with the Console screen's filters, its command line, and the printer's macros. */
export function MiniConsole({ className = '', noMacros, fold }: { className?: string; noMacros?: boolean; fold?: string }) {
  const [unfolded, toggle] = useFold(fold ?? 'vo-home-console-open')
  const folded = !!fold && !unfolded
  const { shown } = useConsoleEntries()
  const { flip } = useConsoleSettings()
  const { help, known } = useGcodeHelp()
  const recent = useMemo(() => shown.slice(-150), [shown])
  const macros = usePrinter((s) =>
    s.objects
      .filter((o) => o.startsWith('gcode_macro ') && !o.slice(12).startsWith('_'))
      .map((o) => o.slice(12).toUpperCase())
      .sort(),
  )
  const [macroFilter, setMacroFilter] = useState('')
  const filteredMacros = macros.filter((m) => !macroFilter || m.includes(macroFilter.toUpperCase()))
  const input = <CommandInput compact dropUp={!flip} />

  const title = (
    <>
      <Terminal size={14} /> Console
    </>
  )
  if (folded)
    return (
      <section className="card folded">
        <h3 className="card-title m-0!">
          <FoldTitle open={false} onToggle={toggle}>
            {title}
          </FoldTitle>
        </h3>
      </section>
    )
  return (
    <section className={`card grow flex flex-col gap-2.5 ${className}`}>
      <h3 className="card-title m-0!">
        {fold ? (
          <FoldTitle open onToggle={toggle}>
            {title}
          </FoldTitle>
        ) : (
          title
        )}
        <a href={href('console')} className="iconbtn -my-1.5 ml-auto h-7 w-7" title="Open the console" aria-label="Open the console">
          <Maximize2 size={14} />
        </a>
      </h3>
      {flip && input}
      <ConsoleOutput entries={recent} known={known} flip={flip} showTime={false} className="grow-body min-h-[160px] flex-1 max-md:h-[300px] max-md:flex-none md:max-wide:h-0" />
      {!flip && input}
      {!noMacros && macros.length > 0 && (
        <div className="grid gap-1.5">
          <div className="flex items-center gap-2">
            <span className="lbl">Macros</span>
            <span className="text-[11px] text-ink-3">click to put in the command line</span>
            {macros.length > 12 && <input className="field ml-auto w-28 px-2! py-0.5! text-[11.5px]" placeholder="Filter" value={macroFilter} onChange={(e) => setMacroFilter(e.target.value)} aria-label="Filter macros" />}
          </div>
          <div className="flex max-h-[68px] flex-wrap gap-1 overflow-auto">
            {filteredMacros.map((m) => (
              <button key={m} type="button" title={help[m] || m} onClick={() => fillCommand(m + ' ')} className="chip cursor-pointer border-0 font-mono text-[11px] hover:bg-hover">
                {m}
              </button>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

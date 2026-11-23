import { useMemo } from 'react'
import { Maximize2, Terminal } from 'lucide-react'
import { href } from '../lib/router'
import { t } from '../i18n'
import { ConsoleOutput } from './console/ConsoleOutput'
import { CommandInput } from './console/CommandInput'
import { useConsoleEntries, useConsoleSettings, useGcodeHelp } from './console/store'
import './console/console.css'
import { FoldTitle, useFold } from './home-deck/fold'

/** Home's folding card of recent printer messages, with the Console screen's filters and its command line. */
export function MiniConsole({ className = '' }: { className?: string }) {
  const [open, toggle] = useFold('vo-home-console-open')
  const { shown } = useConsoleEntries()
  const { flip } = useConsoleSettings()
  const { known } = useGcodeHelp()
  const recent = useMemo(() => shown.slice(-150), [shown])
  const input = <CommandInput compact dropUp={!flip} />
  return (
    <section className={open ? `card grow flex flex-col gap-2.5 ${className}` : 'card folded'}>
      <h3 className="card-title m-0!">
        <FoldTitle open={open} onToggle={toggle}>
          <Terminal size={14} /> {t('app.mini_console.title')}
        </FoldTitle>
        {open && (
          <a href={href('console')} className="iconbtn -my-1.5 ml-auto h-7 w-7" title={t('app.mini_console.open')} aria-label={t('app.mini_console.open')}>
            <Maximize2 size={14} />
          </a>
        )}
      </h3>
      {open && flip && input}
      {open && <ConsoleOutput entries={recent} known={known} flip={flip} showTime={false} className="grow-body min-h-[160px] flex-1 max-md:h-[300px] max-md:flex-none md:max-wide:h-0" />}
      {open && !flip && input}
    </section>
  )
}

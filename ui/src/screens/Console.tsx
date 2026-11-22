import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowDownUp, BookOpen, Copy, Eraser, Search, X } from 'lucide-react'
import { Dialog, PageHeader } from '../components/ui'
import { toast } from '../components/toast'
import { ConsoleOutput } from '../components/console/ConsoleOutput'
import { CommandInput } from '../components/console/CommandInput'
import { CommandList } from '../components/console/CommandList'
import { FilterMenu, FiltersDialog } from '../components/console/Filters'
import { useConsoleEntries, useConsoleSettings, useGcodeHelp } from '../components/console/store'
import type { ConsoleEntry } from '../components/console/model'
import { copyText } from '../components/console/clipboard'
import '../components/console/console.css'
import { locale, t } from '../i18n'

/** Fluidd's console, full height: scrollback with filters and search, the command line, and every command's help. */
export function ConsoleScreen() {
  const [search, setSearch] = useState('')
  const [manage, setManage] = useState(false)
  const [list, setList] = useState(false)
  const settings = useConsoleSettings()
  const { shown, loaded } = useConsoleEntries(search)
  const { known } = useGcodeHelp()
  const fill = useRef<HTMLDivElement>(null)
  const height = useFillHeight(fill)
  const desktop = useMedia('(min-width: 768px)')
  // Wide screens keep the command list beside the output; narrower ones open it in a dialog.
  const wide = useMedia('(min-width: 1280px)')

  const input = <CommandInput autoFocus={desktop} dropUp={!settings.flip} />
  return (
    <>
      <PageHeader title={t('console.title')}>
        <div className="relative min-w-[140px] flex-1 sm:max-w-[240px] sm:flex-none">
          <Search size={14} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
          <input className="field py-1.5! pr-7! pl-8!" placeholder={t('console.search')} value={search} onChange={(e) => setSearch(e.target.value)} aria-label={t('console.search')} spellCheck={false} />
          {search && (
            <button className="iconbtn absolute top-1/2 right-0.5 h-7 w-7 -translate-y-1/2" onClick={() => setSearch('')} aria-label={t('console.clear_search')}>
              <X size={14} />
            </button>
          )}
        </div>
        <FilterMenu onManage={() => setManage(true)} />
        <button className={`btn btn-sm ${settings.flip ? 'text-accent-ink' : ''}`} onClick={() => settings.setFlip(!settings.flip).catch(() => {})} title={t('console.flip_hint')} aria-pressed={settings.flip}>
          <ArrowDownUp size={14} />
          <span className="max-sm:hidden">{t('console.flip')}</span>
        </button>
        <button className="btn btn-sm" onClick={() => copyLines(shown)} disabled={!shown.length} title={t('console.copy_hint')}>
          <Copy size={14} />
          <span className="max-sm:hidden">{t('console.copy')}</span>
        </button>
        <button className="btn btn-sm" onClick={() => settings.clear().catch(() => {})} title={t('console.clear_hint')}>
          <Eraser size={14} />
          <span className="max-sm:hidden">{t('console.clear')}</span>
        </button>
        {!wide && (
          <button className="btn btn-sm" onClick={() => setList(true)}>
            <BookOpen size={14} />
            {t('console.commands')}
          </button>
        )}
      </PageHeader>
      <div ref={fill} className="grid min-h-0 gap-3.5 xl:grid-cols-[minmax(0,1fr)_340px]" style={{ height }}>
        <section className="card flex min-h-0 flex-col gap-2.5 p-3!">
          {settings.flip && input}
          <ConsoleOutput
            className="flex-1"
            entries={shown}
            known={known}
            search={search}
            flip={settings.flip}
            empty={!loaded ? t('common.loading') : search ? t('console.no_match') : t('console.empty')}
          />
          {!settings.flip && input}
        </section>
        {wide && (
          <section className="card flex min-h-0 flex-col">
            <h3 className="card-title">
              <BookOpen size={14} /> {t('console.commands')}
            </h3>
            <CommandList className="flex-1" />
          </section>
        )}
      </div>
      <FiltersDialog open={manage} onClose={() => setManage(false)} />
      <Dialog open={list && !wide} onClose={() => setList(false)} title={t('console.commands')} size="wide">
        <CommandList className="h-[min(70dvh,640px)]" onPick={() => setList(false)} />
      </Dialog>
    </>
  )
}

function useMedia(query: string) {
  const [on, setOn] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const m = window.matchMedia(query)
    const fn = () => setOn(m.matches)
    m.addEventListener('change', fn)
    return () => m.removeEventListener('change', fn)
  }, [query])
  return on
}

/** Height that reaches the bottom of the scrolling page area, so the console fills the screen whatever is above it. */
function useFillHeight(ref: React.RefObject<HTMLElement | null>) {
  const [h, setH] = useState(480)
  useLayoutEffect(() => {
    const el = ref.current
    let scroller = el?.parentElement ?? null
    while (scroller && getComputedStyle(scroller).overflowY !== 'auto') scroller = scroller.parentElement
    if (!el || !scroller) return
    const sc = scroller
    const measure = () => {
      const top = el.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop
      const pad = window.innerWidth >= 768 ? 18 : 14
      setH(Math.max(360, sc.clientHeight - top - pad))
    }
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(sc)
    if (sc.firstElementChild) ro.observe(sc.firstElementChild)
    window.addEventListener('resize', measure)
    return () => (ro.disconnect(), window.removeEventListener('resize', measure))
  }, [ref])
  return h
}

function copyLines(entries: ConsoleEntry[]) {
  const text = entries.map((e) => `${e.time ? new Date(e.time * 1000).toLocaleTimeString(locale()) + ' ' : ''}${e.type === 'command' ? '$ ' : ''}${e.message}`).join('\n')
  copyText(text).then(
    () => toast(t('console.copied', { count: entries.length })),
    () => toast(t('console.copy_failed'), 'error'),
  )
}

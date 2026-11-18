import { useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import { fillCommand, useGcodeHelp } from './store'

/** Every command Klipper knows, with its help, searchable by name or help text. Picking one fills the command line. */
export function CommandList({ onPick, className = '' }: { onPick?: () => void; className?: string }) {
  const { help, names } = useGcodeHelp()
  const [q, setQ] = useState('')
  const [hideInternal, setHideInternal] = useState(true)
  const shown = useMemo(() => {
    const s = q.trim().toLowerCase()
    return names.filter((n) => (!hideInternal || !n.startsWith('_') || s.startsWith('_')) && (!s || n.toLowerCase().includes(s) || help[n]?.toLowerCase().includes(s)))
  }, [names, help, q, hideInternal])
  const internal = names.filter((n) => n.startsWith('_')).length
  return (
    <div className={`flex min-h-0 flex-col gap-2 ${className}`}>
      <div className="relative">
        <Search size={14} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
        <input className="field pl-8!" placeholder={`Search ${names.length} commands`} value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search commands" spellCheck={false} />
      </div>
      {internal > 0 && (
        <label className="flex items-center gap-2 text-[12px] text-ink-3">
          <input type="checkbox" checked={!hideInternal} onChange={(e) => setHideInternal(!e.target.checked)} className="accent-[var(--accent)]" />
          Show {internal} internal commands (starting with _)
        </label>
      )}
      <div className="min-h-0 flex-1 overflow-auto rounded-lg shadow-[inset_0_0_0_1px_var(--line)]">
        {names.length === 0 && <div className="p-4 text-center text-ink-3">Klipper’s commands show here once it’s running.</div>}
        {names.length > 0 && shown.length === 0 && <div className="p-4 text-center text-ink-3">No command matches.</div>}
        {shown.map((n) => (
          <button
            key={n}
            type="button"
            className="block w-full border-0 border-b border-line bg-transparent px-3 py-2 text-left last:border-b-0 hover:bg-hover"
            onClick={() => (fillCommand(n + ' '), onPick?.())}
            title="Put in the command line"
          >
            <div className="font-mono text-[12.5px] font-semibold break-all text-ink">{n}</div>
            {help[n] && <div className="mt-0.5 text-[12px] whitespace-pre-line text-ink-3">{help[n]}</div>}
          </button>
        ))}
      </div>
    </div>
  )
}

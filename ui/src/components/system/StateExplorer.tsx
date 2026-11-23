import { memo, useEffect, useMemo, useState } from 'react'
import { ChevronDown, ChevronRight, Search } from 'lucide-react'
import { Empty } from '../ui'
import { toast } from '../toast'
import { client, usePrinter, type Status } from '../../state/printer'
import { collectorPath } from './collectors'
import './system.css'
import { t } from '../../i18n'

// Browse every Klipper object's fields as a live tree, as Fluidd's state explorer. Objects this page subscribes to
// update with the page; the rest are queried every two seconds while open. Clicking a field gives its collector path.

export function StateExplorer({ onPick }: { onPick?: (path: string) => void }) {
  const objects = usePrinter((s) => s.objects)
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<Set<string>>(new Set())
  const q = query.trim().toLowerCase()
  const shown = useMemo(() => [...new Set(objects)].filter((o) => !q || o.toLowerCase().includes(q) || open.has(o)).sort((a, b) => a.localeCompare(b)), [objects, q, open])
  const toggle = (o: string) => setOpen((s) => new Set(s.has(o) ? [...s].filter((x) => x !== o) : [...s, o]))
  const pick =
    onPick ??
    ((path: string) => {
      navigator.clipboard?.writeText(path).then(
        () => toast(t('system.explorer.copied', { path })),
        () => toast(path),
      )
    })
  return (
    <div className="grid gap-3">
      <label className="relative">
        <Search size={15} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
        <input className="field pl-8" placeholder={t('system.explorer.search')} value={query} onChange={(e) => setQuery(e.target.value)} aria-label={t('system.explorer.search')} />
      </label>
      <p className="m-0 text-[12.5px] text-ink-3">{onPick ? t('system.explorer.pick_hint') : t('system.explorer.copy_hint')}</p>
      {shown.length === 0 ? (
        <Empty>{objects.length ? t('system.explorer.no_match') : t('system.explorer.not_ready')}</Empty>
      ) : (
        <div className="vo-tree">
          {shown.map((o) => (
            <ObjectNode key={o} name={o} open={open.has(o)} onToggle={() => toggle(o)} query={q} onPick={pick} />
          ))}
        </div>
      )}
    </div>
  )
}

function ObjectNode({ name, open, onToggle, query, onPick }: { name: string; open: boolean; onToggle: () => void; query: string; onPick: (p: string) => void }) {
  const live = usePrinter((s) => (open ? (name === 'configfile' ? { ...s.status.configfile, settings: s.settings } : s.status[name]) : undefined))
  const [polled, setPolled] = useState<Status[string] | null>(null)
  const subscribed = live != null && name !== 'configfile'
  useEffect(() => {
    if (!open || subscribed) return
    let stop = false
    const poll = async () => {
      try {
        const r = await client.call<{ status: Status }>('printer.objects.query', { objects: { [name]: null } })
        if (!stop) setPolled(r.status[name] ?? {})
      } catch {}
    }
    poll()
    // configfile is large and only changes on restart; query it once.
    const timer = name === 'configfile' ? undefined : setInterval(poll, 2000)
    return () => {
      stop = true
      clearInterval(timer)
    }
  }, [open, subscribed, name])
  const value = subscribed ? live : (polled ?? (name === 'configfile' ? live : undefined))
  return (
    <div>
      <button className="vo-key flex items-center gap-1 font-semibold" onClick={onToggle} aria-expanded={open}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        {name}
      </button>
      {open && (value ? <Fields value={value} path={[name]} query={query} onPick={onPick} depth={1} /> : <div className="pl-5 text-ink-3">{t('common.loading')}</div>)}
    </div>
  )
}

const Fields = memo(function Fields({ value, path, query, onPick, depth }: { value: unknown; path: (string | number)[]; query: string; onPick: (p: string) => void; depth: number }) {
  const entries: [string | number, unknown][] = Array.isArray(value) ? value.map((v, i) => [i, v]) : Object.entries(value as object).sort(([a], [b]) => a.localeCompare(b))
  if (!entries.length) return <ul><li className="text-ink-3">{Array.isArray(value) ? '[ ]' : '{ }'}</li></ul>
  return (
    <ul>
      {entries.map(([k, v]) => (
        <Field key={k} k={k} v={v} path={[...path, k]} query={query} onPick={onPick} depth={depth} />
      ))}
    </ul>
  )
})

function contains(v: unknown, q: string, depth = 0): boolean {
  if (!q || depth > 6) return false
  if (v && typeof v === 'object') return Object.entries(v).some(([k, x]) => k.toLowerCase().includes(q) || contains(x, q, depth + 1))
  return false
}

function Field({ k, v, path, query, onPick, depth }: { k: string | number; v: unknown; path: (string | number)[]; query: string; onPick: (p: string) => void; depth: number }) {
  const nested = v != null && typeof v === 'object'
  const size = nested ? (Array.isArray(v) ? v.length : Object.keys(v as object).length) : 0
  // Open small objects near the top; big ones (bed mesh matrices, config sections) wait to be asked for.
  const [userOpen, setOpen] = useState<boolean | null>(null)
  const open = userOpen ?? ((depth < 2 && size <= 12) || (!!query && contains(v, query)))
  const hit = !!query && String(k).toLowerCase().includes(query)
  const label = <span className={hit ? 'rounded-sm bg-accent-tint text-accent-ink' : typeof k === 'number' ? 'text-ink-3' : ''}>{k}</span>
  if (!nested)
    return (
      <li>
        <button className="vo-key" onClick={() => onPick(collectorPath(path))} title={collectorPath(path)}>
          {label}
        </button>
        <span className="text-ink-3">: </span>
        <Value v={v} />
      </li>
    )
  return (
    <li>
      <button className="vo-key inline-flex items-center gap-0.5" onClick={() => setOpen(!open)} aria-expanded={open}>
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        {label}
      </button>
      <span className="text-ink-3"> {Array.isArray(v) ? `[${size}]` : `{${size}}`}</span>
      {open && <Fields value={v} path={path} query={query} onPick={onPick} depth={depth + 1} />}
    </li>
  )
}

function Value({ v }: { v: unknown }) {
  // i18n-ignore: a JSON value
  if (v === null || v === undefined) return <span className="vo-null">null</span>
  if (typeof v === 'number') return <span className="vo-num">{Number.isInteger(v) ? v : Number(v.toFixed(6))}</span>
  if (typeof v === 'boolean') return <span className="vo-bool">{String(v)}</span>
  const s = String(v)
  return <span className="vo-str break-all">"{s.length > 400 ? `${s.slice(0, 400)}…` : s}"</span>
}

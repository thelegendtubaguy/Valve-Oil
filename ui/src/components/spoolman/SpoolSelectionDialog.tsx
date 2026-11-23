// Fluidd's SpoolSelectionDialog: search, sort, and filter the unarchived spools, then make one the active spool. With
// Valve Oil's Moonraker patch the active spool also becomes the loaded Box slot's spool.
import { useEffect, useMemo, useState } from 'react'
import { ArrowDownWideNarrow, ArrowUpNarrowWide, ExternalLink, Search } from 'lucide-react'
import { CancelButton, Choices, Dialog, Empty, confirm } from '../ui'
import { attempt } from '../toast'
import { fmtAgo } from '../../lib/format'
import { printer } from '../../state/printer'
import { fetchMeta } from '../../state/files'
import { FLUIDD, useDbItem } from '../../state/database'
import { setActiveSpool, useSpoolman } from './store'
import { SpoolIcon } from './SpoolIcon'
import { CHECK_TEXT, DEFAULT_SORT, SORT_KEYS, fileFilamentTypes, fmtLength, fmtWeight, materials, selectableSpools, spoolChecks, toSpool, type SortOrder, type Spool } from './spoolman'
import { t } from '../../i18n'

export function SpoolSelectionDialog({ open, onClose, target }: { open: boolean; onClose: () => void; target: string | null }) {
  const raw = useSpoolman((s) => s.spools)
  const active = useSpoolman((s) => s.activeSpool)
  const url = useSpoolman((s) => s.url)
  const version = useSpoolman((s) => s.info?.version)
  const spools = useMemo(() => raw.map(toSpool), [raw])
  const [order, setOrder] = useDbItem<SortOrder>(FLUIDD, 'uiSettings.spoolman.selectionDialogSortOrder', DEFAULT_SORT)
  const [unit] = useDbItem<'weight' | 'length'>(FLUIDD, 'uiSettings.spoolman.remainingFilamentUnit', 'weight')
  const [warnNotEnough] = useDbItem(FLUIDD, 'uiSettings.spoolman.warnOnNotEnoughFilament', true)
  const [warnMismatch] = useDbItem(FLUIDD, 'uiSettings.spoolman.warnOnFilamentTypeMismatch', true)
  const [search, setSearch] = useState('')
  const [material, setMaterial] = useState<string | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)

  // Opens on the active spool, as Fluidd does.
  useEffect(() => {
    if (!open) return
    setSelected(active)
    setBusy(false)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const list = useMemo(() => selectableSpools(spools, search, material, order), [spools, search, material, order])
  const mats = useMemo(() => materials(spools), [spools])
  const sortKey = order.key ?? DEFAULT_SORT.key!
  const sortOptions = [...SORT_KEYS.map((k) => ({ key: k.key, label: t(k.msg) })), ...(SORT_KEYS.some((k) => k.key === sortKey) ? [] : [{ key: sortKey, label: sortKey }])]

  const select = async () => {
    const spool = spools.find((s) => s.id === selected)
    if (!spool) {
      if (!(await confirm({ title: t('spoolman.select.confirm'), body: t(CHECK_TEXT.no_spool), confirm: t('spoolman.select.continue') }))) return
    } else if (!(await checksPass(spool, { mismatch: warnMismatch, notEnough: warnNotEnough }))) return
    setBusy(true)
    const ok = await attempt(() => setActiveSpool(spool?.id ?? null).then(() => true))
    setBusy(false)
    if (ok) onClose()
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="wide"
      title={t('spoolman.select.title')}
      footer={
        <>
          {url && (
            <a className="btn btn-ghost mr-auto" href={url} target="_blank" rel="noopener noreferrer" title={version ? `Spoolman ${version}` : undefined}>
              {t('spoolman.select.manage')} <ExternalLink size={14} />
            </a>
          )}
          <CancelButton onClick={onClose} />
          <button className="btn btn-primary" disabled={busy} onClick={select}>
            {t('spoolman.select.select')}
          </button>
        </>
      }
    >
      {target && <p className="mt-0 mb-3 text-ink-2">{target}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <label className="relative min-w-[180px] flex-1">
          <Search size={15} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
          <input className="field pl-8!" type="search" placeholder={t('spoolman.select.search')} aria-label={t('spoolman.select.search_label')} spellCheck={false} value={search} onChange={(e) => setSearch(e.target.value)} onFocus={(e) => e.target.select()} />
        </label>
        <div className="flex items-center gap-1">
          <select className="field w-auto!" aria-label={t('spoolman.select.sort_by')} value={sortKey} onChange={(e) => attempt(() => setOrder({ key: e.target.value, desc: order.desc ?? false }))}>
            {sortOptions.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </select>
          <button className="iconbtn" title={order.desc ? t('spoolman.select.descending') : t('spoolman.select.ascending')} aria-label={order.desc ? t('spoolman.select.sort_ascending') : t('spoolman.select.sort_descending')} onClick={() => attempt(() => setOrder({ key: sortKey, desc: !order.desc }))}>
            {order.desc ? <ArrowDownWideNarrow size={17} /> : <ArrowUpNarrowWide size={17} />}
          </button>
        </div>
      </div>
      {mats.length > 1 && (
        <div className="mt-2.5">
          <Choices<string> value={material ?? ''} onChange={(v) => setMaterial(v || null)} options={[{ value: '', label: t('spoolman.select.all_materials') }, ...mats.map((m) => ({ value: m, label: m }))]} />
        </div>
      )}
      <div className="mt-3 grid max-h-[min(56dvh,520px)] gap-1 overflow-auto">
        {list.length === 0 ? (
          <Empty>{t('spoolman.select.none')}</Empty>
        ) : (
          list.map((s) => <SpoolRow key={s.id} spool={s} unit={unit} selected={s.id === selected} active={s.id === active} onClick={() => setSelected(selected === s.id ? null : s.id)} />)
        )}
      </div>
    </Dialog>
  )
}

function SpoolRow({ spool, unit, selected, active, onClick }: { spool: Spool; unit: 'weight' | 'length'; selected: boolean; active: boolean; onClick: () => void }) {
  const [left, total, fmt] = unit === 'length' ? [spool.remaining_length, spool.initial_length, fmtLength] : [spool.remaining_weight, spool.initial_weight, fmtWeight]
  const details = [`#${spool.id}`, spool.filament.material, spool.location, spool.last_used ? t('spoolman.select.last_used', { ago: fmtAgo(spool.last_used / 1000) }) : t('spoolman.select.never_used')].filter(Boolean)
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onClick}
      className={`flex w-full min-w-0 items-center gap-3 rounded-xl border-0 px-2.5 py-2 text-left text-ink ${selected ? 'bg-accent-tint shadow-[inset_0_0_0_1px_var(--accent)]' : 'bg-transparent hover:bg-hover'}`}
    >
      <SpoolIcon spool={spool} size={40} />
      <span className="min-w-0 flex-1">
        <span className="flex min-w-0 items-center gap-2">
          <span className="truncate font-semibold">{spool.filament_name}</span>
          {active && <span className="pill tone-accent px-2! py-0.5! text-[11px]!">{t('spoolman.active_spool')}</span>}
        </span>
        <span className="block truncate text-[12px] text-ink-3">
          {details.join(' · ')}
          {spool.comment && ` · ${spool.comment}`}
        </span>
      </span>
      <span className="num flex-none text-right text-[12.5px] text-ink-3">
        <b className="font-semibold text-ink">{fmt(left)}</b>
        <span className="block">/ {fmt(total)}</span>
      </span>
    </button>
  )
}

/** Fluidd's checks while a print is under way, each confirmed before the spool changes. */
async function checksPass(spool: Spool, warn: { mismatch: boolean; notEnough: boolean }) {
  const ps = printer.get().status.print_stats ?? {}
  const filename = typeof ps.filename === 'string' ? ps.filename : ''
  if (!filename || (!warn.mismatch && !warn.notEnough)) return true
  const meta = await fetchMeta(filename)
  if (!meta) return true
  const checks = spoolChecks(
    spool,
    { state: String(ps.state ?? ''), filamentUsed: Number(ps.filament_used ?? 0), filamentTotal: meta.filament_total, filamentTypes: fileFilamentTypes(meta.filament_type) },
    warn,
  )
  for (const check of checks) {
    if (!(await confirm({ title: t('spoolman.select.confirm'), body: t(CHECK_TEXT[check]), confirm: t('spoolman.select.continue') }))) return false
  }
  return true
}

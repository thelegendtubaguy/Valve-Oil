import { useEffect, useState } from 'react'
import { Check, Filter, Pencil, Plus, Trash2 } from 'lucide-react'
import { CancelButton, Choices, Dialog, Field, Toggle, confirm } from '../ui'
import { attempt } from '../toast'
import { usePopup, uuid } from '../../lib/browser'
import { validExpression, type ConsoleFilter, type ConsoleFilterType } from './model'
import { useConsoleSettings } from './store'
import { t, type MessageKey } from '../../i18n'

// Fluidd's console filters: "Hide temp waits" and named filters that hide matching lines, stored in its namespace
// (`uiSettings.general.hideTempWaits`, `console.consoleFilters`), so a filter set up in either UI works in both.

/** The toolbar's filter button: Hide temp waits and each saved filter, on or off. */
export function FilterMenu({ onManage }: { onManage: () => void }) {
  const s = useConsoleSettings()
  const { open, setOpen, ref } = usePopup()
  const active = (s.hideTempWaits ? 1 : 0) + s.filters.filter((f) => f.enabled).length
  const toggle = (f: ConsoleFilter) => attempt(() => s.setFilters(s.filters.map((x) => (x.id === f.id ? { ...x, enabled: !x.enabled } : x))))
  const Row = ({ on, label, onClick }: { on: boolean; label: string; onClick: () => void }) => (
    <button type="button" role="menuitemcheckbox" aria-checked={on} onClick={onClick}>
      <span className={`grid h-4 w-4 flex-none place-items-center rounded ${on ? 'bg-accent text-on-accent' : 'shadow-[inset_0_0_0_1.5px_var(--line-strong)]'}`}>{on && <Check size={12} strokeWidth={3} />}</span>
      <span className="truncate">{label}</span>
    </button>
  )
  return (
    <div className="relative inline-flex" ref={ref}>
      <button type="button" className={`btn btn-sm ${active ? 'text-accent-ink' : ''}`} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)} title={t('console.filters.filter')}>
        <Filter size={14} />
        <span className="max-sm:hidden">{t('console.filters.filter')}</span>
        {active > 0 && <span className="num text-[11px]">{active}</span>}
      </button>
      {open && (
        <div className="menu top-[calc(100%+4px)] right-0 max-w-[min(320px,calc(100vw-24px))]" role="menu">
          <Row on={s.hideTempWaits} label={t('console.filters.hide_temp_waits')} onClick={() => attempt(() => s.setHideTempWaits(!s.hideTempWaits))} />
          {s.filters.length > 0 && <div className="sep my-1" />}
          {s.filters.map((f) => (
            <Row key={f.id} on={f.enabled} label={f.name || f.value} onClick={() => toggle(f)} />
          ))}
          <div className="sep my-1" />
          <button type="button" onClick={() => (setOpen(false), onManage())}>
            <Pencil size={14} /> {t('console.filters.manage')}
          </button>
        </div>
      )}
    </div>
  )
}

const TYPE_LABEL: Record<ConsoleFilterType, MessageKey> = {
  contains: 'console.filters.contains',
  'starts-with': 'console.filters.starts_with',
  expression: 'console.filters.expression',
}
const TYPES = Object.keys(TYPE_LABEL) as ConsoleFilterType[]

/** Add, edit, and remove console filters, as in Fluidd's console settings. */
export function FiltersDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const s = useConsoleSettings()
  const [edit, setEdit] = useState<ConsoleFilter | null>(null)
  useEffect(() => {
    if (!open) setEdit(null)
  }, [open])

  const remove = async (f: ConsoleFilter) => {
    if (await confirm({ title: t('console.filters.remove_title'), body: t('console.filters.remove_body', { name: f.name }), confirm: t('common.remove'), danger: true }))
      attempt(() => s.setFilters(s.filters.filter((x) => x.id !== f.id)))
  }
  const save = (f: ConsoleFilter) =>
    attempt(async () => {
      await s.setFilters(f.id ? s.filters.map((x) => (x.id === f.id ? f : x)) : [...s.filters, { ...f, id: uuid() }])
      setEdit(null)
    })

  if (edit) return <FilterEditor filter={edit} others={s.filters} onCancel={() => setEdit(null)} onSave={save} />
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t('console.filters.title')}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            {t('common.close')}
          </button>
          <button className="btn btn-primary" onClick={() => setEdit({ id: '', name: '', type: 'contains', value: '', enabled: true })}>
            <Plus size={15} /> {t('console.filters.add')}
          </button>
        </>
      }
    >
      <p className="mt-0 mb-3 text-ink-2">{t('console.filters.intro')}</p>
      {s.filters.length === 0 ? (
        <div className="rounded-lg border border-dashed border-line-strong p-4 text-center text-ink-3">{t('console.filters.empty')}</div>
      ) : (
        <div className="grid gap-1.5">
          {s.filters.map((f) => (
            <div key={f.id} className="flex items-center gap-3 rounded-xl bg-inset px-3 py-2 shadow-[inset_0_0_0_1px_var(--line)]">
              <Toggle checked={f.enabled} onChange={(v) => attempt(() => s.setFilters(s.filters.map((x) => (x.id === f.id ? { ...x, enabled: v } : x))))} label={t('console.filters.use', { name: f.name })} />
              <div className="min-w-0 flex-1">
                <div className="truncate font-semibold">{f.name}</div>
                <div className="truncate text-[12px] text-ink-3">
                  {t(TYPE_LABEL[f.type])}: <span className="font-mono">{f.value}</span>
                </div>
              </div>
              <button className="iconbtn" onClick={() => setEdit({ ...f })} aria-label={t('console.filters.edit_named', { name: f.name })} title={t('common.edit')}>
                <Pencil size={15} />
              </button>
              <button className="iconbtn" onClick={() => remove(f)} aria-label={t('console.filters.remove_named', { name: f.name })} title={t('common.remove')}>
                <Trash2 size={15} />
              </button>
            </div>
          ))}
        </div>
      )}
    </Dialog>
  )
}

function FilterEditor({ filter, others, onCancel, onSave }: { filter: ConsoleFilter; others: ConsoleFilter[]; onCancel: () => void; onSave: (f: ConsoleFilter) => void }) {
  const [f, setF] = useState(filter)
  const nameTaken = others.some((o) => o.id !== f.id && o.name.toLowerCase() === f.name.trim().toLowerCase())
  const badExpr = f.type === 'expression' && f.value !== '' && !validExpression(f.value)
  const ok = f.name.trim() !== '' && f.value !== '' && !nameTaken && !badExpr
  return (
    <Dialog
      open
      onClose={onCancel}
      title={f.id ? t('console.filters.edit') : t('console.filters.add')}
      footer={
        <>
          <CancelButton onClick={onCancel} />
          <button className="btn btn-primary" disabled={!ok} onClick={() => onSave({ ...f, name: f.name.trim() })}>
            {t('common.save')}
          </button>
        </>
      }
    >
      <div className="grid gap-4">
        <label className="flex items-center justify-between gap-3">
          <span className="font-semibold">{t('console.filters.enable')}</span>
          <Toggle checked={f.enabled} onChange={(enabled) => setF({ ...f, enabled })} label={t('console.filters.enable')} />
        </label>
        <Field label={t('console.filters.name')}>
          <input className="field" autoFocus value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          {nameTaken && <span className="text-[12px] text-bad">{t('console.filters.name_taken')}</span>}
        </Field>
        <div className="grid gap-1.5">
          <span className="lbl">{t('console.filters.type')}</span>
          <Choices value={f.type} options={TYPES.map((type) => ({ value: type, label: t(TYPE_LABEL[type]) }))} onChange={(type) => setF({ ...f, type })} />
        </div>
        <Field label={t(TYPE_LABEL[f.type])}>
          {/* i18n-ignore: example console lines, which Klipper writes in English */}
          <input className="field font-mono" spellCheck={false} autoCapitalize="off" value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })} placeholder={f.type === 'expression' ? '^// pressure_advance' : 'pressure_advance'} />
          {badExpr ? (
            <span className="text-[12px] text-bad">{t('console.filters.bad_expression')}</span>
          ) : (
            <span className="text-[12px] text-ink-3">{f.type === 'expression' ? t('console.filters.expression_hint') : t('console.filters.case_hint')}</span>
          )}
        </Field>
      </div>
    </Dialog>
  )
}

// QIDI's filament list (01.01.07.01's filament-0.4.json), as the fork's Filaments page edits it. Each change rereads the
// file and writes it back whole, so an edit made elsewhere in the meantime, such as in the config editor, is kept.
import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, Copy, Download, Pencil, Plus, RefreshCw, Search, Trash2, TriangleAlert, Upload } from 'lucide-react'
import { Card, confirm, Empty, Spinner } from '../../ui'
import { attempt, toast } from '../../toast'
import { readText, writeText } from '../../../state/files'
import { client, usePrinter } from '../../../state/printer'
import { useBox } from '../../../state/box'
import { useDbItem, FLUIDD } from '../../../state/database'
import {
  QIDI_FILAMENT_CONFIG_FILE,
  applyQidiFilamentImport,
  diffQidiFilamentImport,
  nextQidiFilamentId,
  parseQidiFilamentConfig,
  qidiFilamentBaseKey,
  qidiFilamentRows,
  removeQidiFilament,
  serializeQidiFilamentConfig,
  setQidiFilament,
  type QidiFilamentConfig,
  type QidiFilamentDraft,
  type QidiFilamentImportItem,
  type QidiFilamentRow,
} from '../../../qidi/qidi-filament-config'
import { FilamentEditDialog, type FilamentSave } from './FilamentEditDialog'
import { FilamentImportDialog } from './FilamentImportDialog'

const QIDI_CLIENT_SERVICE = 'qidi-client'

class InvalidFilamentConfig extends Error {}

async function readConfig(): Promise<QidiFilamentConfig> {
  const config = parseQidiFilamentConfig(await readText('config', QIDI_FILAMENT_CONFIG_FILE))
  if (config == null) throw new InvalidFilamentConfig()
  return config
}

type Editor = { draft: QidiFilamentDraft; previousKey?: string; sourceKey?: string }
type Imported = { config: QidiFilamentConfig; items: QidiFilamentImportItem[] }

export function FilamentsPage() {
  const box = useBox()
  const printing = usePrinter((s) => ['printing', 'paused'].includes(s.status.print_stats?.state))
  const [uiSettings] = useDbItem<{ general?: { instanceName?: string } }>(FLUIDD, 'uiSettings', {})
  const [config, setConfig] = useState<QidiFilamentConfig | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [loadError, setLoadError] = useState<'invalid' | 'unavailable' | null>(null)
  const [restartNeeded, setRestartNeeded] = useState(false)
  const [search, setSearch] = useState('')
  const [vendor, setVendor] = useState<string | null>(null)
  const [editor, setEditor] = useState<Editor | null>(null)
  const [imported, setImported] = useState<Imported | null>(null)
  const [services, setServices] = useState<string[]>([])
  const file = useRef<HTMLInputElement>(null)

  const load = async () => {
    setLoading(true)
    try {
      setConfig(await readConfig())
      setLoadError(null)
    } catch (e) {
      setConfig(null)
      setLoadError(e instanceof InvalidFilamentConfig ? 'invalid' : 'unavailable')
    }
    setLoading(false)
  }
  useEffect(() => {
    load()
    client
      .call<{ system_info: { available_services?: string[] } }>('machine.system_info')
      .then((r) => setServices(r.system_info.available_services ?? []))
      .catch(() => {})
  }, [])

  const rows = useMemo(() => (config ? qidiFilamentRows(config) : []), [config])
  const vendors = useMemo(() => [...new Set(rows.map((r) => r.vendor))].filter(Boolean).sort((a, b) => a.localeCompare(b)), [rows])
  const q = search.trim().toLowerCase()
  const shown = rows.filter((r) => (!vendor || r.vendor === vendor) && (!q || [r.vendor, r.name, r.type, r.id].some((v) => v.toLowerCase().includes(q))))

  const update = async (change: (c: QidiFilamentConfig) => QidiFilamentConfig) => {
    setBusy(true)
    try {
      const next = change(await readConfig())
      await writeText('config', QIDI_FILAMENT_CONFIG_FILE, serializeQidiFilamentConfig(next))
      setConfig(next)
      setRestartNeeded(true)
      return true
    } catch (e) {
      toast(e instanceof InvalidFilamentConfig ? INVALID : `Couldn't save the filament list: ${e instanceof Error ? e.message : String(e)}`, 'error')
      return false
    } finally {
      setBusy(false)
    }
  }

  const add = () => {
    if (!config) return
    const baseKey = qidiFilamentBaseKey(config, 'PLA')
    const base = baseKey != null ? rows.find((r) => r.key === baseKey) : undefined
    setEditor({
      draft: { vendor: '', name: '', id: (base && nextQidiFilamentId(config, base.id)) ?? '', type: base?.type ?? '', nozzleMin: base?.nozzleMin ?? null, nozzleMax: base?.nozzleMax ?? null, boxMax: base?.boxMax ?? 0, chamber: base?.chamber ?? 0, support: false },
    })
  }
  const edit = ({ key, ...draft }: QidiFilamentRow) => setEditor({ draft, previousKey: key })
  const duplicate = ({ key, ...draft }: QidiFilamentRow) => config && setEditor({ draft: { ...draft, name: `${draft.name} (copy)`, id: nextQidiFilamentId(config, draft.id) ?? '' }, sourceKey: key })
  const save = async ({ draft, previousKey, baseKey }: FilamentSave) => {
    if (await update((c) => setQidiFilament(c, draft, { previousKey, baseKey }))) setEditor(null)
  }
  const remove = async (row: QidiFilamentRow) => {
    const slots = box.slots.filter((s) => s.filamentId === row.id).map((s) => s.label)
    const ok = await confirm({ title: 'Remove filament', body: `Remove ${row.key} from the filament list?${slots.length ? ` Slots set to it: ${slots.join(', ')}.` : ''}`, confirm: 'Remove', danger: true })
    if (ok) await update((c) => removeQidiFilament(c, row.key))
  }

  const exportList = async () => {
    try {
      const text = serializeQidiFilamentConfig(await readConfig())
      const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
      const a = document.createElement('a')
      a.href = url
      a.download = `qidi-filaments-${uiSettings.general?.instanceName || location.hostname}.json`
      document.body.append(a)
      a.click()
      a.remove()
      // Deferred, as revoking right after the click can cancel the download.
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch {
      toast("Couldn't export the filament list.", 'error')
    }
  }
  const importFile = async (f: File | undefined) => {
    if (!f) return
    try {
      const parsed = parseQidiFilamentConfig(await f.text())
      if (!parsed) return toast("That file isn't a QIDI filament list.", 'error')
      setImported({ config: parsed, items: diffQidiFilamentImport(await readConfig(), parsed) })
    } catch {
      toast(UNAVAILABLE, 'error')
    }
  }
  const doImport = async (keys: string[]) => {
    const imp = imported
    if (imp && (await update((c) => applyQidiFilamentImport(c, imp.config, keys)))) setImported(null)
  }

  // Klipper and the touchscreen read the filament list only when they start.
  const canRestartScreen = services.includes(QIDI_CLIENT_SERVICE)
  const restart = async () => {
    if (canRestartScreen) attempt(() => client.call('machine.services.restart', { service: QIDI_CLIENT_SERVICE }))
    await attempt(() => client.call('printer.restart'))
    setRestartNeeded(false)
  }

  const off = config == null || busy
  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3.5">
      {restartNeeded && (
        <div className="tone-info flex flex-wrap items-center gap-3 rounded-xl px-4 py-2.5">
          <span className="min-w-0 flex-1 text-ink">Klipper and the touchscreen use the changes once they restart.</span>
          <button className="btn btn-sm btn-primary" disabled={printing} onClick={restart}>
            <RefreshCw size={13} /> {canRestartScreen ? 'Restart Klipper and touchscreen' : 'Restart Klipper'}
          </button>
        </div>
      )}
      {loadError && <div className="tone-bad rounded-xl px-4 py-2.5 text-ink">{loadError === 'invalid' ? INVALID : UNAVAILABLE}</div>}
      <div className="tone-warn flex items-start gap-2.5 rounded-xl px-4 py-2.5">
        <TriangleAlert size={16} className="mt-0.5 flex-none" />
        <span className="text-ink">Firmware updates replace this list and remove your changes. Export it before updating, and import it afterwards.</span>
      </div>
      <Card
        title="QIDI filaments"
        aside={
          <span className="flex flex-wrap justify-end gap-1.5">
            <button className="btn btn-sm btn-primary" disabled={off} onClick={add}>
              <Plus size={14} /> Add
            </button>
            <button className="btn btn-sm" disabled={off} onClick={() => file.current?.click()}>
              <Upload size={13} /> Import
            </button>
            <button className="btn btn-sm" disabled={off} onClick={exportList}>
              <Download size={13} /> Export
            </button>
            <button className="btn btn-sm btn-ghost" disabled={busy} onClick={load} aria-label="Refresh" title="Refresh">
              <RefreshCw size={13} />
            </button>
          </span>
        }
      >
        <div className="mb-3 flex flex-wrap items-center gap-2.5">
          <div className="relative w-full sm:w-[240px]">
            <Search size={14} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
            <input className="field pl-8" placeholder="Search" spellCheck={false} value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search filaments" />
          </div>
          <div className="flex min-w-0 flex-1 flex-wrap gap-1.5">
            <button className={`choice ${vendor == null ? 'on' : ''}`} onClick={() => setVendor(null)}>
              All
            </button>
            {vendors.map((v) => (
              <button key={v} className={`choice ${vendor === v ? 'on' : ''}`} onClick={() => setVendor(vendor === v ? null : v)}>
                {v}
              </button>
            ))}
          </div>
        </div>
        {loading && !config ? (
          <div className="grid place-items-center py-10">
            <Spinner size={22} />
          </div>
        ) : !config ? (
          <Empty>No filament list to show.</Empty>
        ) : shown.length === 0 ? (
          <Empty>No filaments match.</Empty>
        ) : (
          <>
            <div className="hidden overflow-x-auto md:block">
              <table className="table">
                <thead>
                  <tr>
                    <th>Vendor</th>
                    <th>Filament</th>
                    <th>Type</th>
                    <th>ID</th>
                    <th>Nozzle</th>
                    <th>Box max</th>
                    <th>Chamber</th>
                    <th>Support</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => (
                    <tr key={r.key}>
                      <td className="text-ink-2">{r.vendor}</td>
                      <td className="font-semibold">{r.name}</td>
                      <td>
                        <span className="chip">{r.type}</span>
                      </td>
                      <td className="code text-ink-2">{r.id}</td>
                      <td className="num whitespace-nowrap">
                        {r.nozzleMin}–{r.nozzleMax} °C
                      </td>
                      <td className="num whitespace-nowrap">{r.boxMax ? `${r.boxMax} °C` : <span className="text-ink-3">No heating</span>}</td>
                      <td className="num whitespace-nowrap">{r.chamber ? `${r.chamber} °C` : <span className="text-ink-3">–</span>}</td>
                      <td>{r.support && <Check size={15} className="text-good" aria-label="Support" />}</td>
                      <td className="whitespace-nowrap text-right">
                        <RowActions busy={busy} onEdit={() => edit(r)} onDuplicate={() => duplicate(r)} onRemove={() => remove(r)} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="grid grid-cols-[minmax(0,1fr)] gap-1.5 md:hidden">
              {shown.map((r) => (
                <div key={r.key} className="flex items-center gap-2 rounded-xl bg-inset px-3 py-2 shadow-[inset_0_0_0_1px_var(--line)]">
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-semibold">
                      {r.vendor} {r.name}
                    </div>
                    <div className="truncate text-[12px] text-ink-3">
                      {[r.type, r.id, `${r.nozzleMin}–${r.nozzleMax} °C`, r.boxMax ? `Box ${r.boxMax} °C` : 'No heating', r.support ? 'Support' : null].filter(Boolean).join(' · ')}
                    </div>
                  </div>
                  <RowActions busy={busy} onEdit={() => edit(r)} onDuplicate={() => duplicate(r)} onRemove={() => remove(r)} />
                </div>
              ))}
            </div>
          </>
        )}
      </Card>
      <input ref={file} type="file" accept={/iPad|iPhone|iPod/.test(navigator.userAgent) ? undefined : '.json'} hidden onChange={(e) => (importFile(e.target.files?.[0]), (e.target.value = ''))} />
      {config && editor && <FilamentEditDialog config={config} initial={editor.draft} previousKey={editor.previousKey} sourceKey={editor.sourceKey} busy={busy} onSave={save} onClose={() => setEditor(null)} />}
      {imported && <FilamentImportDialog items={imported.items} busy={busy} onImport={doImport} onClose={() => setImported(null)} />}
    </div>
  )
}

const INVALID = 'The filament list qidi_filament_config/filament-0.4.json has a JSON error. Fix it in the configuration editor.'
const UNAVAILABLE = "Valve Oil UI can't read the filament list qidi_filament_config/filament-0.4.json."

function RowActions({ busy, onEdit, onDuplicate, onRemove }: { busy: boolean; onEdit: () => void; onDuplicate: () => void; onRemove: () => void }) {
  return (
    <span className="inline-flex">
      <button className="iconbtn" disabled={busy} onClick={onEdit} title="Edit" aria-label="Edit">
        <Pencil size={14} />
      </button>
      <button className="iconbtn" disabled={busy} onClick={onDuplicate} title="Duplicate" aria-label="Duplicate">
        <Copy size={14} />
      </button>
      <button className="iconbtn" disabled={busy} onClick={onRemove} title="Remove" aria-label="Remove">
        <Trash2 size={14} />
      </button>
    </span>
  )
}

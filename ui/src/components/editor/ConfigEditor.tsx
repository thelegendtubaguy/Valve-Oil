import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowLeft, BookOpen, Download, Lock, RotateCw, Save, Search } from 'lucide-react'
import { Spinner, confirm } from '../ui'
import { attempt, toast } from '../toast'
import { client } from '../../state/printer'
import { downloadFile, readText, useDirectory, writeText } from '../../state/files'
import { baseName, configService, isLocked, parentOf, rootProps } from '../files/fs'
import { useJob, isActive } from '../../state/job'
import { useTheme } from '../../lib/theme'
import type { EditorHandle } from './codemirror'
import { t } from '../../i18n'

// ---- unsaved changes ----
// The editor is a route (#/files/edit/...), and the browser can't be stopped from leaving a hash route, so a dirty
// editor keeps its text here when it unmounts. Leaving asks first; staying goes back and the text comes back with it.

const drafts = new Map<string, string>()
let guard: { key: string; hash: string } | null = null
const dirty = new Set<string>()

if (typeof window !== 'undefined') {
  window.addEventListener('hashchange', async () => {
    const g = guard
    if (!g || location.hash === g.hash) return
    const leave = await confirmDiscard()
    if (leave) {
      drafts.delete(g.key)
      dirty.delete(g.key)
      if (guard === g) guard = null
    } else location.hash = g.hash
  })
  window.addEventListener('beforeunload', (e) => {
    if (dirty.size) e.preventDefault()
  })
}

function confirmDiscard() {
  return confirm({ title: t('editor.unsaved'), body: t('editor.unsaved_body'), confirm: t('editor.discard'), danger: true })
}

// ---- services a config file can restart (Fluidd's getConfigMapByFilename serviceSupported) ----
type SystemInfo = { available_services?: string[]; instance_ids?: Record<string, string>; service_state?: Record<string, unknown> }
let services: Promise<SystemInfo | null> | null = null
function useServices() {
  const [info, setInfo] = useState<SystemInfo | null | undefined>(undefined)
  useEffect(() => {
    services ??= client
      .call<{ system_info: SystemInfo }>('machine.system_info')
      .then((r) => r.system_info)
      .catch(() => ((services = null), null))
    services.then(setInfo)
  }, [])
  return (name: string) => {
    // Klipper and Moonraker are always there; without system info, offer just those.
    if (!info) return name === 'klipper' || name === 'moonraker'
    return (!!info.instance_ids && name in info.instance_ids) || (info.available_services ?? []).includes(name) || name in (info.service_state ?? {})
  }
}

const SERVICE_NAMES: Record<string, string> = { moonraker: 'Moonraker', crowsnest: 'Crowsnest', webcamd: 'webcamd' }

type Props = { root: string; path: string; onClose: () => void }

/** The editor as a screen: full screen on phones, a tall card on larger screens. */
export function ConfigEditor(props: Props) {
  // On larger screens the card fills the window below wherever it starts (banners above it come and go).
  const box = useRef<HTMLDivElement>(null)
  const [height, setHeight] = useState<number | undefined>()
  useLayoutEffect(() => {
    const fit = () => box.current && setHeight(Math.max(420, innerHeight - box.current.getBoundingClientRect().top - 18))
    fit()
    const ro = new ResizeObserver(fit)
    ro.observe(document.body)
    window.addEventListener('resize', fit)
    return () => (ro.disconnect(), window.removeEventListener('resize', fit))
  }, [])
  return (
    <div ref={box} style={{ ['--editor-h' as string]: height ? `${height}px` : undefined }} className="fixed inset-0 z-[45] flex flex-col bg-canvas md:static md:z-auto md:h-[var(--editor-h,calc(100dvh-120px))] md:overflow-hidden md:rounded-[14px] md:bg-surface md:shadow-[var(--shadow-card)]">
      <EditorBody {...props} route />
    </div>
  )
}

/** The editor over the current screen, for file browsers without an editor route. */
export function EditorDialog(props: Props) {
  return (
    <div className="dialog-backdrop p-0! md:p-4!">
      <div className="flex h-full w-full flex-col overflow-hidden bg-surface md:h-[calc(100dvh-32px)] md:max-w-[1280px] md:rounded-2xl" style={{ boxShadow: 'var(--shadow-overlay)' }} role="dialog" aria-modal="true">
        <EditorBody {...props} />
      </div>
    </div>
  )
}

function EditorBody({ root, path, onClose, route }: Props & { route?: boolean }) {
  const key = `${root}/${path}`
  const name = baseName(path)
  const { dir } = useDirectory(root, parentOf(path))
  const entry = dir?.files.find((f) => f.path === path)
  const locked = isLocked(root, path)
  const readOnly = rootProps(root).readonly || locked || entry?.permissions === 'r'
  const service = configService(name)
  const supported = useServices()
  const job = useJob()
  const printing = isActive(job.state)
  const [dark] = useTheme()

  const host = useRef<HTMLDivElement>(null)
  const editor = useRef<EditorHandle | null>(null)
  const saved = useRef('')
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading')
  const [error, setError] = useState('')
  const [changed, setChanged] = useState(false)
  const [saving, setSaving] = useState(false)
  const changedRef = useRef(false)
  changedRef.current = changed

  const markDirty = (on: boolean) => {
    setChanged(on)
    if (on) {
      dirty.add(key)
      if (route) guard = { key, hash: location.hash }
    } else {
      dirty.delete(key)
      drafts.delete(key)
      if (guard?.key === key) guard = null
    }
  }

  // Load the file, then the editor; a kept draft replaces the text.
  useEffect(() => {
    let live = true
    let handle: EditorHandle | null = null
    setState('loading')
    Promise.all([readText(root, path), import('./codemirror')])
      .then(([text, cm]) => {
        if (!live || !host.current) return
        saved.current = text
        const draft = drafts.get(key)
        handle = cm.createEditor(host.current, {
          doc: draft ?? text,
          filename: name,
          readOnly,
          dark: document.documentElement.classList.contains('dark'),
          onChange: (t) => (t !== saved.current) !== changedRef.current && markDirty(t !== saved.current),
          onSave: () => saveRef.current(false),
        })
        editor.current = handle
        if (draft != null && draft !== text) markDirty(true)
        setState('ready')
        if (!readOnly && window.matchMedia('(pointer: fine)').matches) handle.focus()
      })
      .catch((e) => {
        if (!live) return
        setError(e instanceof Error ? e.message : String(e))
        setState('error')
      })
    return () => {
      live = false
      if (handle && changedRef.current) drafts.set(key, handle.getText())
      handle?.destroy()
      editor.current = null
    }
    // readOnly is settled before the text loads in practice; reloading on its change would drop edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  useEffect(() => editor.current?.setDark(dark === 'dark'), [dark])

  const restartable = !!service && supported(service.service) && !readOnly
  const save = async (restart: boolean) => {
    if (readOnly || saving || !editor.current) return
    const text = editor.current.getText()
    setSaving(true)
    const ok = await attempt(async () => (await writeText(root, path, text), true))
    setSaving(false)
    if (ok === undefined) return
    saved.current = text
    markDirty(false)
    if (!restart || !service) return toast(t('editor.saved', { name }))
    if (service.service === 'klipper') {
      toast(t('editor.saved_restarting_firmware', { name }))
      attempt(() => client.call('printer.firmware_restart'))
    } else {
      toast(t('editor.saved_restarting', { name, service: SERVICE_NAMES[service.service] ?? service.service }))
      attempt(() => client.call('machine.services.restart', { service: service.service }))
    }
    onClose()
  }
  const saveRef = useRef(save)
  saveRef.current = save

  const close = async () => {
    if (changedRef.current && !(await confirmDiscard())) return
    markDirty(false)
    onClose()
  }

  useEffect(() => {
    if (route) return
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && !document.querySelector('.cm-search') && close()
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  })

  return (
    <>
      <div className="flex flex-none items-center gap-1.5 border-b border-line bg-surface px-2 py-2 md:px-3">
        <button className="iconbtn" onClick={close} aria-label={t('common.close')} title={t('common.close')}>
          <ArrowLeft size={18} />
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <b className="truncate text-[14.5px]">{name}</b>
            {changed && <span className="h-2 w-2 flex-none rounded-full bg-accent" title={t('editor.unsaved')} aria-label={t('editor.unsaved')} />}
            {readOnly && (
              <span className="pill tone-muted flex-none px-2! py-0.5! text-[11.5px]!">
                <Lock size={11} /> {t('editor.read_only')}
              </span>
            )}
          </div>
          <div className="truncate text-[12px] text-ink-3">{`${root}/${path}`}</div>
        </div>
        <button className="iconbtn" onClick={() => editor.current?.toggleSearch()} aria-label={t('editor.search')} title={t('editor.search_shortcut')} disabled={state !== 'ready'}>
          <Search size={17} />
        </button>
        <button className="iconbtn hidden sm:grid" onClick={() => downloadFile(root, path)} aria-label={t('editor.download')} title={t('editor.download')}>
          <Download size={17} />
        </button>
        {service?.link && (
          <a className="iconbtn hidden sm:grid" href={service.link} target="_blank" rel="noreferrer" aria-label={t('editor.reference')} title={t('editor.reference')}>
            <BookOpen size={17} />
          </a>
        )}
        {!readOnly && (
          <>
            <button className={`btn ${restartable ? '' : 'btn-primary'} btn-sm h-8`} onClick={() => save(false)} disabled={state !== 'ready' || saving} title={t('editor.save_shortcut')}>
              {saving ? <Spinner size={13} /> : <Save size={14} />} <span className="hidden sm:inline">{t('common.save')}</span>
            </button>
            {restartable && (
              <button
                className="btn btn-primary btn-sm h-8"
                onClick={() => save(true)}
                disabled={state !== 'ready' || saving || printing}
                title={printing ? t('editor.not_while_printing') : service.service === 'klipper' ? t('editor.save_then_firmware_restart') : t('editor.save_then_restart', { service: service.service })}
              >
                <RotateCw size={14} /> <span className="hidden sm:inline">{t('editor.save_and_restart')}</span>
                <span className="sm:hidden">{t('editor.restart')}</span>
              </button>
            )}
          </>
        )}
      </div>
      {locked && <div className="flex-none border-b border-line bg-inset px-4 py-2 text-[12.5px] text-ink-2">{t('editor.locked')}</div>}
      <div className="relative min-h-0 flex-1">
        <div ref={host} className="absolute inset-0" />
        {state === 'loading' && (
          <div className="absolute inset-0 grid place-items-center">
            <Spinner size={22} />
          </div>
        )}
        {state === 'error' && <div className="absolute inset-0 grid place-items-center p-6 text-center text-ink-2">{error}</div>}
      </div>
    </>
  )
}

import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { CircleAlert, CircleCheck, Info, TriangleAlert, X } from 'lucide-react'
import { t } from '../i18n'

const dialogStack: HTMLElement[] = []
// Everything Tab can land on inside a dialog, for keeping Tab within it.
const FOCUSABLE = 'a[href], area[href], button, input, select, textarea, summary, iframe, audio[controls], video[controls], [contenteditable]:not([contenteditable="false"]), [tabindex]'

export function Card({ title, icon, aside, children, className = '' }: { title?: ReactNode; icon?: ReactNode; aside?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {title && (
        <h3 className="card-title">
          {icon}
          {title}
          {aside && <span className="aside">{aside}</span>}
        </h3>
      )}
      {children}
    </section>
  )
}

/**
 * A button that runs its action only after being held, for actions that are hard to undo.
 * Works with mouse, touch, and keyboard (hold Space or Enter).
 */
export function HoldButton({ onHold, children, holdMs = 1200, className = '', disabled }: { onHold: () => void; children: ReactNode; holdMs?: number; className?: string; disabled?: boolean }) {
  const [holding, setHolding] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const start = () => {
    if (disabled || timer.current) return
    setHolding(true)
    timer.current = setTimeout(() => {
      timer.current = null
      setHolding(false)
      onHold()
    }, holdMs)
  }
  const stop = () => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    setHolding(false)
  }
  useEffect(() => stop, [])
  return (
    <button
      type="button"
      disabled={disabled}
      className={`btn hold btn-danger ${holding ? 'holding' : ''} ${className}`}
      style={{ ['--hold-ms' as string]: `${holdMs}ms` }}
      onPointerDown={start}
      onPointerUp={stop}
      onPointerLeave={stop}
      onPointerCancel={stop}
      onContextMenu={(e) => e.preventDefault()}
      onKeyDown={(e) => (e.key === ' ' || e.key === 'Enter') && !e.repeat && (e.preventDefault(), start())}
      onKeyUp={(e) => (e.key === ' ' || e.key === 'Enter') && stop()}
    >
      <span className="hold-fill" />
      <span>{children}</span>
    </button>
  )
}

export function Dialog({ open, onClose, title, children, footer, size }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; size?: 'wide' | 'full' }) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLElement | null>(null)
  const onCloseRef = useRef(onClose)
  const titleId = useId()
  onCloseRef.current = onClose
  // Capture the opener before React commits an autoFocus field inside this dialog.
  if (open && !triggerRef.current && document.activeElement instanceof HTMLElement) triggerRef.current = document.activeElement

  useEffect(() => {
    if (!open) return
    const dialog = dialogRef.current
    if (!dialog) return
    dialogStack.push(dialog)
    const focusable = () => Array.from(dialog.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => !el.matches(':disabled') && el.getAttribute('tabindex') !== '-1' && !el.hasAttribute('hidden') && el.getClientRects().length > 0)
    if (!dialog.contains(document.activeElement)) (focusable()[0] ?? dialog).focus()
    const onKey = (e: KeyboardEvent) => {
      if (dialogStack[dialogStack.length - 1] !== dialog) return
      if (e.key === 'Escape') {
        e.preventDefault()
        onCloseRef.current()
        return
      }
      // A control that uses Tab itself (the code editor's indent) has already handled it.
      if (e.key !== 'Tab' || e.defaultPrevented) return
      const controls = focusable()
      if (!controls.length) {
        e.preventDefault()
        dialog.focus()
        return
      }
      const first = controls[0]
      const last = controls[controls.length - 1]
      if (!dialog.contains(document.activeElement) || (e.shiftKey ? document.activeElement === first : document.activeElement === last)) {
        e.preventDefault()
        const target = e.shiftKey ? last : first
        target.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      dialogStack.splice(dialogStack.lastIndexOf(dialog), 1)
      // StrictMode re-runs this effect with the dialog still on the page; only a real close returns focus to the opener.
      queueMicrotask(() => {
        if (dialog.isConnected) return
        const trigger = triggerRef.current
        triggerRef.current = null
        if (trigger?.isConnected) trigger.focus()
      })
    }
  }, [open])

  if (!open) return null
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) { e.preventDefault(); onClose() } }}>
      <div ref={dialogRef} className={`dialog ${size ?? ''}`} role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1}>
        <div className="flex items-center gap-3 border-b border-line px-5 py-4">
          <div id={titleId} className="min-w-0 flex-1 text-[15px] font-semibold">{title}</div>
          <button className="iconbtn" onClick={onClose} aria-label={t('common.close')}>
            <X size={17} />
          </button>
        </div>
        <div className={size === 'full' ? 'min-h-0 flex-1 overflow-auto px-5 py-4' : 'px-5 py-4'}>{children}</div>
        {footer && <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line px-5 py-3">{footer}</div>}
      </div>
    </div>
  )
}

export function Toggle({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label?: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className="relative inline-block h-6 w-[34px] flex-none rounded-full border-0 transition-colors disabled:opacity-45"
      style={{ background: checked ? 'var(--accent)' : 'var(--line-strong)' }}
    >
      <i className="absolute top-1 h-4 w-4 rounded-full bg-white transition-all" style={{ left: checked ? 16 : 2 }} />
    </button>
  )
}

/** A tinted message with its tone's icon, and a dismiss button when it can be closed. */
export function Notice({ tone, icon, onClose, children, className = '' }: { tone: 'info' | 'warn' | 'bad' | 'good'; icon?: ReactNode; onClose?: () => void; children: ReactNode; className?: string }) {
  const cls = { info: 'tone-accent', warn: 'tone-warn', bad: 'tone-bad', good: 'tone-good' }[tone]
  const Icon = { info: Info, warn: TriangleAlert, bad: CircleAlert, good: CircleCheck }[tone]
  return (
    <div className={`${cls} flex items-start gap-2.5 rounded-lg px-3 py-2 ${className}`}>
      <span className="mt-0.5 flex-none">{icon ?? <Icon size={15} />}</span>
      <div className="min-w-0 flex-1 text-ink [overflow-wrap:anywhere]">{children}</div>
      {onClose && (
        <button className="iconbtn -my-1.5 -mr-2 h-7 w-7 flex-none" onClick={onClose} aria-label={t('common.dismiss')}>
          <X size={15} />
        </button>
      )}
    </div>
  )
}

/** A key and its value on one line, as Fluidd's status labels. */
export function KV({ k, children, className = '', vClassName = '' }: { k: ReactNode; children: ReactNode; className?: string; vClassName?: string }) {
  return (
    <div className={`row-k ${className}`}>
      <span className="k">{k}</span>
      <span className={`v ${vClassName}`}>{children}</span>
    </div>
  )
}

/** A label over its field. */
export function Field({ label, children, className = 'gap-1.5' }: { label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={`grid ${className}`}>
      <span className="lbl">{label}</span>
      {children}
    </label>
  )
}

/** A dialog's Cancel button. */
export function CancelButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="btn btn-ghost" onClick={onClick}>
      {t('common.cancel')}
    </button>
  )
}

/** A spinner in the middle of the space a list or panel will fill. */
export function Loading({ className = 'py-10', size = 20 }: { className?: string; size?: number }) {
  return (
    <div className={`grid place-items-center ${className}`}>
      <Spinner size={size} />
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line-strong p-5 text-center text-ink-3">{children}</div>
}

// ---------------------------------------------------------------------------------------------------------------
// Shared controls in the touchscreen's language. Screens build on these so the whole app looks and behaves alike.

/** The title at the top of a screen, with tabs or actions after it. */
export function PageHeader({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <h1>{title}</h1>
      {children && <div className="flex min-w-0 flex-1 flex-wrap items-center justify-end gap-2">{children}</div>}
    </div>
  )
}

/** Small uppercase heading over a group of controls. */
export function SectionLabel({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`section-label ${className}`}>{children}</div>
}

/** Segmented control. Options with an href render as links (screen navigation); the rest as buttons. */
export function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: ReactNode; href?: string; disabled?: boolean }[]; onChange?: (v: T) => void; label?: string }) {
  const isNavigation = options.length > 0 && options.every((o) => o.href)
  const controls = options.map((o) =>
    o.href ? (
      <a key={o.value} href={o.href} aria-current={o.value === value ? 'page' : undefined} className={o.value === value ? 'on' : ''}>
        {o.label}
      </a>
    ) : (
      <button key={o.value} type="button" aria-pressed={o.value === value} disabled={o.disabled} className={o.value === value ? 'on' : ''} onClick={() => onChange?.(o.value)}>
        {o.label}
      </button>
    ),
  )
  return isNavigation ? <nav className="seg" aria-label={label}>{controls}</nav> : <div className="seg" role="group" aria-label={label}>{controls}</div>
}

/** A row of pills where one (or, with multi, several) is picked. */
export function Choices<T extends string | number>({ value, options, onChange, disabled }: { value: T | null; options: { value: T; label: ReactNode }[]; onChange: (v: T) => void; disabled?: boolean }) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button key={String(o.value)} type="button" disabled={disabled} className={`choice ${o.value === value ? 'on' : ''}`} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

/**
 * − value + with the value editable in place. onChange gets the new value, clamped; the screen sends it to the
 * printer. Format and parse handle units and decimals.
 */
export function Stepper({
  value,
  onChange,
  step = 1,
  min = -Infinity,
  max = Infinity,
  digits = 0,
  unit,
  disabled,
  label,
}: {
  value: number
  onChange: (v: number) => void
  step?: number
  min?: number
  max?: number
  digits?: number
  unit?: string
  disabled?: boolean
  label?: string
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const clamp = (v: number) => Math.min(max, Math.max(min, Number(v.toFixed(Math.max(digits, 4)))))
  const commit = () => {
    if (draft == null) return
    const v = Number(draft)
    setDraft(null)
    if (draft.trim() !== '' && Number.isFinite(v)) onChange(clamp(v))
  }
  return (
    <div className="flex items-center gap-1.5">
      <button type="button" className="pm" disabled={disabled || value <= min} onClick={() => onChange(clamp(value - step))} aria-label={label ? t('common.lower_named', { name: label }) : t('common.lower')}>
        −
      </button>
      <div className="relative min-w-0 flex-1">
        <input
          className="field num h-[34px] text-center text-[16px] font-bold"
          inputMode="decimal"
          aria-label={label}
          disabled={disabled}
          value={draft ?? value.toFixed(digits)}
          onFocus={(e) => (setDraft(value.toFixed(digits)), e.target.select())}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
            if (e.key === 'Escape') (setDraft(null), (e.target as HTMLInputElement).blur())
          }}
        />
        {unit && <span className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-[12px] font-semibold text-ink-3">{unit}</span>}
      </div>
      <button type="button" className="pm" disabled={disabled || value >= max} onClick={() => onChange(clamp(value + step))} aria-label={label ? t('common.raise_named', { name: label }) : t('common.raise')}>
        +
      </button>
    </div>
  )
}

export function Spinner({ size = 16, className = '' }: { size?: number; className?: string }) {
  return (
    <span
      className={`spin inline-block flex-none rounded-full ${className}`}
      style={{ width: size, height: size, border: `${Math.max(2, size / 7)}px solid var(--line-strong)`, borderTopColor: 'var(--accent)' }}
      aria-hidden
    />
  )
}

/** A button that opens a small menu below it. Items close the menu when picked. */
export function MenuButton({ button, items, align = 'right', className = 'iconbtn', label }: { button: ReactNode; items: ({ label: ReactNode; icon?: ReactNode; onSelect: () => void; danger?: boolean; disabled?: boolean } | null | false)[]; align?: 'left' | 'right'; className?: string; label?: string }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  // An item's dialog takes its opener from the focused element, and the item itself goes when the menu closes.
  const buttonRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (e: Event) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', esc)
    return () => (window.removeEventListener('pointerdown', close), window.removeEventListener('keydown', esc))
  }, [open])
  return (
    <div className="relative inline-flex" ref={ref}>
      <button ref={buttonRef} type="button" className={className} aria-haspopup="menu" aria-expanded={open} aria-label={label} onClick={() => setOpen((o) => !o)}>
        {button}
      </button>
      {open && (
        <div className="menu top-[calc(100%+4px)]" style={align === 'right' ? { right: 0 } : { left: 0 }} role="menu">
          {items.filter(Boolean).map((it, i) => {
            const item = it as Exclude<typeof it, null | false>
            return (
              <button key={i} type="button" role="menuitem" disabled={item.disabled} className={item.danger ? 'danger' : ''} onClick={() => (buttonRef.current?.focus(), setOpen(false), item.onSelect())}>
                {item.icon}
                {item.label}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ---- confirm and prompt, as promises: `if (await confirm({...})) ...` ----
type Ask = { title: ReactNode; body?: ReactNode; confirm?: string; danger?: boolean; input?: { label: string; value: string; placeholder?: string } }
type Pending = Ask & { resolve: (v: string | boolean | null) => void }
let openAsk: ((p: Pending | null) => void) | null = null

function ask(a: Ask) {
  return new Promise<string | boolean | null>((resolve) => {
    if (!openAsk) return resolve(a.input ? null : false)
    openAsk({ ...a, resolve })
  })
}
/** Asks before doing something; resolves true when confirmed. */
export const confirm = (a: Omit<Ask, 'input'>) => ask(a) as Promise<boolean>
/** Asks for a line of text; resolves null when cancelled. */
export const prompt = (a: Omit<Ask, 'input'> & { input: Ask['input'] }) => ask(a) as Promise<string | null>

/** Mounted once by the app shell; renders confirm() and prompt() dialogs. */
export function AskHost() {
  const [p, setP] = useState<Pending | null>(null)
  const [text, setText] = useState('')
  useEffect(() => {
    openAsk = (next) => (setP(next), setText(next?.input?.value ?? ''))
    return () => {
      openAsk = null
    }
  }, [])
  if (!p) return null
  const done = (v: string | boolean | null) => (p.resolve(v), setP(null))
  const cancel = () => done(p.input ? null : false)
  return (
    <Dialog
      open
      onClose={cancel}
      title={p.title}
      footer={
        <>
          <button className="btn btn-ghost" onClick={cancel}>
            {t('common.cancel')}
          </button>
          <button className={`btn ${p.danger ? 'btn-danger' : 'btn-primary'}`} disabled={!!p.input && !text.trim()} onClick={() => done(p.input ? text.trim() : true)}>
            {p.confirm ?? t('common.ok')}
          </button>
        </>
      }
    >
      {p.body && <div className="text-ink-2">{p.body}</div>}
      {p.input && (
        <label className={`grid gap-1.5 ${p.body ? 'mt-3' : ''}`}>
          <span className="lbl">{p.input.label}</span>
          <input
            className="field"
            autoFocus
            value={text}
            placeholder={p.input.placeholder}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && text.trim() && done(text.trim())}
          />
        </label>
      )}
    </Dialog>
  )
}

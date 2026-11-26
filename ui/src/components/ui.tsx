import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, type RefObject } from 'react'
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
/**
 * A finished hold often changes what's under the pointer, as Cancel turning into Print again, and letting go would
 * click that. Drops the click that comes with the pointer's release.
 */
function swallowReleaseClick() {
  const swallow = (e: Event) => (e.stopPropagation(), e.preventDefault())
  const done = () => {
    window.removeEventListener('pointerup', done, true)
    window.removeEventListener('pointercancel', done, true)
    // The click follows the pointerup in the same task.
    setTimeout(() => window.removeEventListener('click', swallow, true), 0)
  }
  window.addEventListener('click', swallow, true)
  window.addEventListener('pointerup', done, true)
  window.addEventListener('pointercancel', done, true)
}

/**
 * A button that acts when held for `holdMs`. With `onTap`, letting go before then (a tap or click, or Enter or Space)
 * calls it instead, for a confirm dialog, so a press that doesn't know about holding still does something.
 */
export function HoldButton({ onHold, onTap, children, holdMs = 1200, className = '', disabled }: { onHold: () => void; onTap?: () => void; children: ReactNode; holdMs?: number; className?: string; disabled?: boolean }) {
  const [holding, setHolding] = useState(false)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const start = (pointer: boolean) => {
    if (disabled || timer.current) return
    setHolding(true)
    timer.current = setTimeout(() => {
      timer.current = null
      setHolding(false)
      if (pointer) swallowReleaseClick()
      onHold()
    }, holdMs)
  }
  const stop = () => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = null
    setHolding(false)
  }
  // Let go on the button: a tap if the hold hadn't finished. Leaving the button or a cancelled pointer is neither.
  const release = () => {
    const early = timer.current != null
    stop()
    if (early) onTap?.()
  }
  useEffect(() => stop, [])
  return (
    <button
      type="button"
      disabled={disabled}
      className={`btn hold btn-danger ${holding ? 'holding' : ''} ${className}`}
      style={{ ['--hold-ms' as string]: `${holdMs}ms` }}
      onPointerDown={() => start(true)}
      onPointerUp={release}
      onPointerLeave={stop}
      onPointerCancel={stop}
      onContextMenu={(e) => e.preventDefault()}
      onKeyDown={(e) => (e.key === ' ' || e.key === 'Enter') && !e.repeat && (e.preventDefault(), start(false))}
      onKeyUp={(e) => (e.key === ' ' || e.key === 'Enter') && release()}
    >
      <span className="hold-fill" />
      <span>{children}</span>
    </button>
  )
}

/**
 * Makes an open surface modal for the keyboard: focus moves into it, Tab stays inside, Escape closes it, and focus
 * goes back to what opened it. Only the topmost of stacked surfaces takes keys. `opener` names what opened it when
 * that isn't the focused element, as after a mouse press in Safari, which doesn't focus buttons.
 */
export function useModalFocus(open: boolean, surfaceRef: RefObject<HTMLElement | null>, onClose: () => void, opener?: RefObject<HTMLElement | null>) {
  const triggerRef = useRef<HTMLElement | null>(null)
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  // Capture the opener before React commits an autoFocus field inside the surface.
  if (open && !triggerRef.current) triggerRef.current = opener?.current ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null)

  useEffect(() => {
    if (!open) return
    const surface = surfaceRef.current
    if (!surface) return
    dialogStack.push(surface)
    const focusable = () => Array.from(surface.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => !el.matches(':disabled') && el.getAttribute('tabindex') !== '-1' && !el.hasAttribute('hidden') && el.getClientRects().length > 0)
    if (!surface.contains(document.activeElement)) (focusable()[0] ?? surface).focus()
    const onKey = (e: KeyboardEvent) => {
      if (dialogStack[dialogStack.length - 1] !== surface) return
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
        surface.focus()
        return
      }
      const first = controls[0]
      const last = controls[controls.length - 1]
      if (!surface.contains(document.activeElement) || (e.shiftKey ? document.activeElement === first : document.activeElement === last)) {
        e.preventDefault()
        const target = e.shiftKey ? last : first
        target.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      dialogStack.splice(dialogStack.lastIndexOf(surface), 1)
      // StrictMode re-runs this effect with the surface still on the page; only a real close returns focus to the opener.
      queueMicrotask(() => {
        if (surface.isConnected) return
        const trigger = triggerRef.current
        triggerRef.current = null
        if (trigger?.isConnected) trigger.focus()
      })
    }
  }, [open])
}

export function Dialog({ open, onClose, title, children, footer, size }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; size?: 'wide' | 'full' }) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const titleId = useId()
  useModalFocus(open, dialogRef, onClose)

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
  const menuRef = useRef<HTMLDivElement>(null)
  const focusEdge = useRef<'first' | 'last'>('first')
  const buttons = () => Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not(:disabled)') ?? [])
  const openMenu = (edge: 'first' | 'last' = 'first') => {
    focusEdge.current = edge
    setOpen(true)
  }
  const closeMenu = (restoreFocus = true) => {
    setOpen(false)
    if (restoreFocus) queueMicrotask(() => buttonRef.current?.focus())
  }
  useEffect(() => {
    if (!open) return
    const close = (e: Event) => ref.current && !ref.current.contains(e.target as Node) && setOpen(false)
    const esc = (e: globalThis.KeyboardEvent) => e.key === 'Escape' && closeMenu()
    window.addEventListener('pointerdown', close)
    window.addEventListener('keydown', esc)
    return () => (window.removeEventListener('pointerdown', close), window.removeEventListener('keydown', esc))
  }, [open])
  useEffect(() => {
    if (!open) return
    const items = buttons()
    items[focusEdge.current === 'last' ? items.length - 1 : 0]?.focus()
  }, [open])
  const onMenuKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = buttons()
    if (!items.length) return
    const current = items.indexOf(document.activeElement as HTMLButtonElement)
    let next: number | null = null
    if (e.key === 'ArrowDown') next = (current + 1) % items.length
    else if (e.key === 'ArrowUp') next = (current - 1 + items.length) % items.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = items.length - 1
    if (next !== null) {
      e.preventDefault()
      items[next]?.focus()
    }
  }
  return (
    <div
      className="relative inline-flex"
      ref={ref}
      // Close when focus moves on to something else (Tab). Safari and Firefox on macOS drop focus to the page when an
      // item is pressed, before its click lands, so a blur to nothing leaves closing to the outside pointerdown.
      onBlur={(e) => {
        const next = e.relatedTarget
        if (next instanceof Node && !ref.current?.contains(next)) setOpen(false)
      }}
    >
      <button
        ref={buttonRef}
        type="button"
        className={className}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={label}
        onClick={() => (open ? closeMenu(false) : openMenu())}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault()
            openMenu(e.key === 'ArrowUp' ? 'last' : 'first')
          }
        }}
      >
        {button}
      </button>
      {open && (
        <div
          ref={menuRef}
          className="menu top-[calc(100%+4px)]"
          style={align === 'right' ? { right: 0 } : { left: 0 }}
          role="menu"
          aria-label={label}
          onKeyDown={onMenuKeyDown}
        >
          {items.filter(Boolean).map((it, i) => {
            const item = it as Exclude<typeof it, null | false>
            return (
              <button
                key={i}
                type="button"
                role="menuitem"
                disabled={item.disabled}
                className={item.danger ? 'danger' : ''}
                onClick={() => {
                  buttonRef.current?.focus()
                  setOpen(false)
                  item.onSelect()
                }}
              >
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
type Ask = {
  title: ReactNode
  body?: ReactNode
  confirm?: string
  /** The dismiss button's label, when "Cancel" would be unclear, as beside "Cancel print". */
  dismiss?: string
  danger?: boolean
  input?: { label: string; value: string; placeholder?: string }
  /** Recheck live state immediately before resolving a consequential action. */
  validate?: () => string | null
}
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
  const [validationError, setValidationError] = useState<string | null>(null)
  useEffect(() => {
    openAsk = (next) => (setP(next), setText(next?.input?.value ?? ''), setValidationError(null))
    return () => {
      openAsk = null
    }
  }, [])
  if (!p) return null
  const done = (v: string | boolean | null) => (p.resolve(v), setP(null))
  const cancel = () => done(p.input ? null : false)
  const submit = () => {
    const error = p.validate?.() ?? null
    if (error) return setValidationError(error)
    done(p.input ? text.trim() : true)
  }
  return (
    <Dialog
      open
      onClose={cancel}
      title={p.title}
      footer={
        <>
          <button className="btn btn-ghost" onClick={cancel}>
            {p.dismiss ?? t('common.cancel')}
          </button>
          <button className={`btn ${p.danger ? 'btn-danger' : 'btn-primary'}`} disabled={!!p.input && !text.trim()} onClick={submit}>
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
            onChange={(e) => {
              setText(e.target.value)
              setValidationError(null)
            }}
            onKeyDown={(e) => e.key === 'Enter' && text.trim() && submit()}
          />
        </label>
      )}
      {validationError && (
        <div role="alert" className="mt-3 rounded-md border border-bad/30 bg-bad/10 px-3 py-2 text-sm text-bad">
          {validationError}
        </div>
      )}
    </Dialog>
  )
}

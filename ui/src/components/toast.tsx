import { CircleAlert, CircleCheck, X } from 'lucide-react'
import { createStore, useStore } from '../lib/store'

type Toast = { id: number; text: string; tone: 'error' | 'ok' }
const toasts = createStore<Toast[]>([])
let nextId = 0

export function toast(text: string, tone: Toast['tone'] = 'ok') {
  const id = ++nextId
  toasts.set((t) => [...t.slice(-3), { id, text, tone }])
  setTimeout(() => dismiss(id), tone === 'error' ? 8000 : 3500)
}
function dismiss(id: number) {
  toasts.set((t) => t.filter((x) => x.id !== id))
}

/** Runs an async action and reports a failure as a toast. */
export async function attempt<T>(fn: () => Promise<T>): Promise<T | undefined> {
  try {
    return await fn()
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e), 'error')
    return undefined
  }
}

export function Toasts() {
  const list = useStore(toasts, (t) => t)
  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-20 z-70 flex flex-col items-center gap-2 px-4 md:bottom-6">
      {list.map((t) => (
        <div key={t.id} className="pointer-events-auto flex max-w-[520px] items-start gap-2.5 rounded-xl bg-surface px-3.5 py-2.5" style={{ boxShadow: 'var(--shadow-overlay)' }}>
          {t.tone === 'error' ? <CircleAlert size={17} className="mt-px flex-none text-bad" /> : <CircleCheck size={17} className="mt-px flex-none text-good" />}
          <span className="min-w-0 flex-1 break-words">{t.text}</span>
          <button className="iconbtn -my-1 -mr-2 h-7 w-7" onClick={() => dismiss(t.id)} aria-label="Dismiss">
            <X size={15} />
          </button>
        </div>
      ))}
    </div>
  )
}

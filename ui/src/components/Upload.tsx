import { useRef, useState, type ReactNode } from 'react'
import { CircleAlert, CircleCheck, Upload as UploadIcon, X } from 'lucide-react'
import { cancelUpload, clearUploads, uploadFiles, useUploads, type FileWithPath } from '../state/files'
import { filesFromDrop, filesWithPath, hasFiles } from './files/drop'
import { GCODE_EXTENSIONS } from './files/fs'
import { fmtBytes } from '../lib/format'
import { Spinner } from './ui'

const ACCEPT = GCODE_EXTENSIONS.join(',')

/** Uploads into a root's folder, with progress shown by <UploadsPanel>. */
export function useUploader(root = 'gcodes', dir = '') {
  const list = useUploads()
  const mine = list.filter((u) => u.root === root && (u.state === 'waiting' || u.state === 'sending'))
  const total = mine.reduce((a, u) => a + u.size, 0)
  const progress = mine.length ? mine.reduce((a, u) => a + u.loaded, 0) / Math.max(1, total) : null
  const upload = (files: FileList | File[] | FileWithPath[], print = false) => uploadFiles(root, dir, [...files], print)
  return { progress, upload }
}

/** Drop target and file picker for G-code. */
export function DropZone({ children, className = '' }: { children?: ReactNode; className?: string }) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const { progress, upload } = useUploader()
  return (
    <button
      type="button"
      className={`grid place-items-center gap-1.5 rounded-xl border-[1.5px] border-dashed bg-transparent p-4 text-center text-ink-3 ${over ? 'border-accent bg-accent-tint' : 'border-line-strong'} ${className}`}
      onClick={() => input.current?.click()}
      onDragOver={(e) => hasFiles(e.dataTransfer) && (e.preventDefault(), setOver(true))}
      onDragLeave={() => setOver(false)}
      onDrop={async (e) => {
        if (!hasFiles(e.dataTransfer)) return
        e.preventDefault()
        e.stopPropagation()
        setOver(false)
        upload(await filesFromDrop(e.dataTransfer))
      }}
    >
      <UploadIcon size={22} />
      {progress != null ? (
        <span className="num font-semibold text-ink-2">Uploading {Math.round(progress * 100)}%</span>
      ) : (
        (children ?? (
          <>
            <b className="text-ink-2">Drop G-code or .gcode.3mf</b>
            <span>or click to upload</span>
          </>
        ))
      )}
      <input ref={input} type="file" accept={ACCEPT} multiple hidden onChange={(e) => (e.target.files && upload(filesWithPath(e.target.files)), (e.target.value = ''))} />
    </button>
  )
}

/** Upload progress, bottom right, while anything uploads; failures stay until dismissed. */
export function UploadsPanel() {
  const list = useUploads()
  if (!list.length) return null
  const active = list.filter((u) => u.state === 'waiting' || u.state === 'sending').length
  return (
    <div className="fixed right-3 bottom-20 z-50 w-[min(360px,calc(100vw-24px))] rounded-xl bg-surface p-3 md:right-5 md:bottom-5" style={{ boxShadow: 'var(--shadow-overlay)' }}>
      <div className="mb-2 flex items-center gap-2">
        <UploadIcon size={15} className="text-accent-ink" />
        <b className="flex-1">{active ? `Uploading ${active} ${active === 1 ? 'file' : 'files'}` : 'Uploads'}</b>
        {!active && (
          <button className="iconbtn h-7 w-7" onClick={clearUploads} aria-label="Dismiss">
            <X size={15} />
          </button>
        )}
      </div>
      <div className="grid max-h-[40vh] gap-1.5 overflow-auto">
        {list.map((u) => (
          <div key={u.id} className="grid gap-1">
            <div className="flex items-center gap-2 text-[12.5px]">
              {u.state === 'done' ? <CircleCheck size={14} className="flex-none text-good" /> : u.state === 'failed' ? <CircleAlert size={14} className="flex-none text-bad" /> : u.state === 'sending' ? <Spinner size={13} /> : <span className="w-[14px] flex-none" />}
              <span className="min-w-0 flex-1 truncate" title={u.name}>
                {u.name}
              </span>
              <span className="num flex-none text-ink-3">{u.state === 'sending' ? `${Math.round((u.loaded / Math.max(1, u.size)) * 100)}%` : u.state === 'waiting' ? fmtBytes(u.size) : u.state === 'cancelled' ? 'Cancelled' : u.state === 'failed' ? 'Failed' : ''}</span>
              {(u.state === 'waiting' || u.state === 'sending') && (
                <button className="iconbtn h-6 w-6" onClick={() => cancelUpload(u.id)} aria-label={`Cancel ${u.name}`}>
                  <X size={13} />
                </button>
              )}
            </div>
            {u.state === 'sending' && (
              <div className="bar h-1">
                <i style={{ width: `${(u.loaded / Math.max(1, u.size)) * 100}%` }} />
              </div>
            )}
            {u.error && <span className="pl-[22px] text-[12px] text-bad">{u.error}</span>}
          </div>
        ))}
      </div>
    </div>
  )
}

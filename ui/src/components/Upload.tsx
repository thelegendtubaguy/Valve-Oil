import { useRef, useState, type ReactNode } from 'react'
import { Upload as UploadIcon } from 'lucide-react'
import { client } from '../state/printer'
import { toast } from './toast'

const ACCEPT = '.gcode,.g,.gco,.3mf'

export function useUploader() {
  const [progress, setProgress] = useState<number | null>(null)
  const upload = async (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      if (!/\.(gcode|g|gco|3mf)$/i.test(f.name)) {
        toast(`${f.name} isn't G-code or a sliced .gcode.3mf.`, 'error')
        continue
      }
      setProgress(0)
      try {
        await client.upload(f, '', setProgress)
        toast(`Uploaded ${f.name}`)
      } catch (e) {
        toast(e instanceof Error ? e.message : String(e), 'error')
      }
      setProgress(null)
    }
  }
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
      onDragOver={(e) => (e.preventDefault(), setOver(true))}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        upload(e.dataTransfer.files)
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
      <input ref={input} type="file" accept={ACCEPT} multiple hidden onChange={(e) => e.target.files && upload(e.target.files)} />
    </button>
  )
}

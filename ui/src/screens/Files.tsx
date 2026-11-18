import { lazy, Suspense, useState } from 'react'
import { PageHeader, Segmented, Spinner } from '../components/ui'
import { FileBrowser } from '../components/files/FileBrowser'
import { PrintDialog } from '../components/PrintDialog'
import { parentOf } from '../components/files/fs'
import { href, navigate, useSubPath } from '../lib/router'
import { usePrinter } from '../state/printer'

// Both are heavy and only needed on their own routes.
const ConfigEditor = lazy(() => import('../components/editor/ConfigEditor').then((m) => ({ default: m.ConfigEditor })))
const GcodePreview = lazy(() => import('./GcodePreview').then((m) => ({ default: m.GcodePreview })))

// The roots this screen browses, as Fluidd's Jobs and Configure pages do. Logs live on the System screen.
const ROOTS = [
  { value: 'gcodes', label: 'G-code' },
  { value: 'config', label: 'Configuration' },
  { value: 'config_examples', label: 'Config examples' },
  { value: 'docs', label: 'Klipper docs' },
]

const segs = (path: string) => (path ? path.split('/') : [])

/**
 * Routes: #/files (G-code), #/files/<root>/<folder...>, #/files/edit/<root>/<path...> for the editor, and
 * #/files/preview/<path...> for the G-code preview.
 */
export function FilesScreen() {
  const sub = useSubPath()
  const registered = usePrinter((s) => s.server?.registered_directories ?? [])
  const roots = registered.length ? ROOTS.filter((r) => r.value === 'gcodes' || registered.includes(r.value)) : ROOTS
  const [printing, setPrinting] = useState<string | null>(null)

  const loading = (
    <div className="grid place-items-center py-16">
      <Spinner size={22} />
    </div>
  )

  if (sub[0] === 'preview')
    return (
      <Suspense fallback={loading}>
        <GcodePreview path={sub.slice(1).join('/')} />
      </Suspense>
    )

  if (sub[0] === 'edit' && sub[1]) {
    const root = sub[1]
    const path = sub.slice(2).join('/')
    return (
      <Suspense fallback={loading}>
        <ConfigEditor key={`${root}/${path}`} root={root} path={path} onClose={() => navigate('files', root, ...segs(parentOf(path)))} />
      </Suspense>
    )
  }

  const root = roots.some((r) => r.value === sub[0]) ? sub[0] : 'gcodes'
  const path = root === sub[0] ? sub.slice(1).join('/') : ''

  // min-w-0: the app's screen grid would otherwise grow to the tabs' and table's width on phones.
  return (
    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-3.5">
      <PageHeader title="Files">
        <Segmented value={root} label="File roots" options={roots.map((r) => ({ ...r, href: href('files', r.value) }))} />
      </PageHeader>
      <FileBrowser
        key={root}
        root={root}
        path={path}
        dirHref={(p) => href('files', root, ...segs(p))}
        editHref={(p) => href('files', 'edit', root, ...segs(p))}
        previewHref={(p) => href('files', 'preview', ...segs(p))}
        onPrint={setPrinting}
      />
      {printing && <PrintDialog filename={printing} onClose={() => setPrinting(null)} />}
    </div>
  )
}

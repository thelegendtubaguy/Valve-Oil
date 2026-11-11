import { useMemo, useState } from 'react'
import { Folder, Search } from 'lucide-react'
import { Card, Empty } from '../components/ui'
import { FileCard } from '../components/FileCard'
import { DropZone } from '../components/Upload'
import { PrintDialog } from '../components/PrintDialog'
import { useFiles, useHistory, type HistoryJob } from '../state/files'
import { displayName } from '../lib/format'

type Sort = 'recent' | 'name' | 'size'

export function FilesScreen() {
  const files = useFiles()
  const jobs = useHistory(500)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<Sort>('recent')
  const [open, setOpen] = useState<string | null>(null)

  const byFile = useMemo(() => {
    const m = new Map<string, { last: HistoryJob; count: number }>()
    for (const j of jobs ?? []) {
      const e = m.get(j.filename)
      if (!e) m.set(j.filename, { last: j, count: j.status === 'completed' ? 1 : 0 })
      else if (j.status === 'completed') e.count++
    }
    return m
  }, [jobs])

  const list = useMemo(() => {
    if (!files) return []
    const q = query.trim().toLowerCase()
    const out = files.filter((f) => !q || f.path.toLowerCase().includes(q))
    const lastUsed = (p: string, mod: number) => Math.max(byFile.get(p)?.last.start_time ?? 0, mod)
    if (sort === 'name') out.sort((a, b) => displayName(a.path).localeCompare(displayName(b.path), undefined, { numeric: true }))
    else if (sort === 'size') out.sort((a, b) => b.size - a.size)
    else out.sort((a, b) => lastUsed(b.path, b.modified) - lastUsed(a.path, a.modified))
    return out
  }, [files, query, sort, byFile])

  return (
    <Card title="Files" icon={<Folder size={14} />} aside={files ? `${files.length} files` : undefined}>
      <div className="mb-3.5 flex flex-wrap items-center gap-2">
        <label className="relative min-w-[200px] flex-1">
          <Search size={15} className="absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
          <input className="field pl-8" placeholder="Search files" value={query} onChange={(e) => setQuery(e.target.value)} />
        </label>
        <div className="flex gap-0.5 rounded-[9px] bg-canvas p-[3px] shadow-[inset_0_0_0_1px_var(--line)]">
          {(['recent', 'name', 'size'] as Sort[]).map((s) => (
            <button key={s} onClick={() => setSort(s)} className={`rounded-[7px] border-0 px-2.5 py-1 font-medium ${sort === s ? 'bg-surface text-ink shadow-[var(--shadow-btn)]' : 'bg-transparent text-ink-2'}`}>
              {s === 'recent' ? 'Recent' : s === 'name' ? 'Name' : 'Size'}
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
        <DropZone className="aspect-[4/3] sm:aspect-auto" />
        {list.map((f) => (
          <FileCard key={f.path} path={f.path} last={byFile.get(f.path)?.last} count={byFile.get(f.path)?.count ?? 0} onOpen={() => setOpen(f.path)} />
        ))}
      </div>
      {files && list.length === 0 && <Empty>{query ? 'No files match.' : 'No files yet. Upload one, or send it from your slicer.'}</Empty>}
      {open && <PrintDialog filename={open} onClose={() => setOpen(null)} />}
    </Card>
  )
}

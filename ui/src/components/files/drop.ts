// Files from a drop or a folder picker, each with the sub-folder it came from, so folder uploads keep their layout.
// Ported from the Fluidd fork's src/util/file-system-entry.ts.

import type { FileWithPath } from '../../state/files'

/** True when a drag carries files from the desktop rather than rows dragged inside the page. */
export function hasFiles(dt: DataTransfer | null): boolean {
  if (!dt) return false
  return dt.types.includes('Files')
}

const fileOf = (entry: FileSystemFileEntry) => new Promise<File>((resolve, reject) => entry.file(resolve, reject))

// readEntries returns at most 100 entries per call in Chromium, so read until it comes back empty.
async function readAll(dir: FileSystemDirectoryEntry): Promise<FileSystemEntry[]> {
  const reader = dir.createReader()
  const out: FileSystemEntry[] = []
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject))
    if (!batch.length) return out
    out.push(...batch)
  }
}

export async function filesFromDrop(dt: DataTransfer): Promise<FileWithPath[]> {
  const entries = [...dt.items].map((i) => i.webkitGetAsEntry?.()).filter((e): e is FileSystemEntry => !!e)
  if (!entries.length) return filesWithPath(dt.files)
  const out: FileWithPath[] = []
  const stack = entries.map((entry) => ({ entry, path: '' }))
  for (let item = stack.pop(); item; item = stack.pop()) {
    try {
      if (item.entry.isFile) out.push({ file: await fileOf(item.entry as FileSystemFileEntry), path: item.path })
      else if (item.entry.isDirectory) {
        const path = item.path ? `${item.path}/${item.entry.name}` : item.entry.name
        for (const entry of await readAll(item.entry as FileSystemDirectoryEntry)) stack.push({ entry, path })
      }
    } catch {
      // An unreadable entry (permissions, a broken link) is skipped; the rest still upload.
    }
  }
  return out
}

/** Files from an <input type=file>, with the folder each came from when a whole folder was picked. */
export function filesWithPath(files: FileList | File[]): FileWithPath[] {
  return [...files].map((file) => ({ file, path: file.webkitRelativePath && file.webkitRelativePath !== file.name ? file.webkitRelativePath.slice(0, -file.name.length - 1) : '' }))
}

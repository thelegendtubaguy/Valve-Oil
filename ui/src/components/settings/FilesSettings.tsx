import { useState } from 'react'
import { FilePen, Folder, Plus, X } from 'lucide-react'
import { Choices } from '../ui'
import { Row, SettingsCard, ToggleRow } from './common'
import { useUiSetting } from './fluidd'

export function FilesSettings() {
  const [sortOrder, setSortOrder] = useUiSetting('general.textSortOrder')
  const [dragDrop, setDragDrop] = useUiSetting('general.filesAndFoldersDragAndDrop')
  const [confirmClose, setConfirmClose] = useUiSetting('editor.confirmDirtyEditorClose')
  const [extensions, setExtensions] = useUiSetting('editor.autoEditExtensions')

  return (
    <>
      <p className="m-0 text-[12.5px] text-ink-3">Shared with Fluidd: a change here shows there too.</p>
      <SettingsCard title="File browser" icon={<Folder size={14} />}>
        <Row title="Text sort order" sub="How file names sort: as text, by a leading number, or as version numbers.">
          <Choices
            value={sortOrder}
            onChange={setSortOrder}
            options={[
              { value: 'default', label: 'Default' },
              { value: 'numeric-prefix', label: 'Numeric prefix' },
              { value: 'version', label: 'Version' },
            ]}
          />
        </Row>
        <ToggleRow title="Drag-and-drop for files and folders" sub="Move files by dragging them onto a folder." checked={dragDrop} onChange={setDragDrop} />
      </SettingsCard>

      <SettingsCard title="File editor" icon={<FilePen size={14} />}>
        <ToggleRow title="Require confirm when closing the editor with unsaved changes" checked={confirmClose} onChange={setConfirmClose} />
        <Row title="Extensions to automatically open in edit mode" wide>
          <ExtensionList value={extensions} onChange={setExtensions} />
        </Row>
      </SettingsCard>
    </>
  )
}

function ExtensionList({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const [draft, setDraft] = useState('')
  const ext = draft.trim().toLowerCase().replace(/^\.?/, '.')
  const valid = /^\.[a-z0-9_-]+$/.test(ext) && !value.includes(ext)
  const add = () => {
    if (!valid) return
    onChange([...value, ext])
    setDraft('')
  }
  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap gap-1.5">
        {value.map((v) => (
          <span key={v} className="chip code pr-1!">
            {v}
            <button type="button" className="iconbtn h-5! w-5!" onClick={() => onChange(value.filter((x) => x !== v))} aria-label={`Remove ${v}`}>
              <X size={12} />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-1.5">
        <input className="field code h-[30px] py-1" placeholder=".gcode" aria-label="Add an extension" value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} />
        <button type="button" className="btn btn-sm" disabled={!valid} onClick={add}>
          <Plus size={13} /> Add
        </button>
      </div>
    </div>
  )
}

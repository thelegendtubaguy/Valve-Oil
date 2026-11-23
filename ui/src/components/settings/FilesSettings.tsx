import { useState } from 'react'
import { FilePen, Folder, Plus, X } from 'lucide-react'
import { Choices } from '../ui'
import { Row, SettingsCard, ToggleRow } from './common'
import { useUiSetting } from './fluidd'
import { t } from '../../i18n'

export function FilesSettings() {
  const [sortOrder, setSortOrder] = useUiSetting('general.textSortOrder')
  const [dragDrop, setDragDrop] = useUiSetting('general.filesAndFoldersDragAndDrop')
  const [confirmClose, setConfirmClose] = useUiSetting('editor.confirmDirtyEditorClose')
  const [extensions, setExtensions] = useUiSetting('editor.autoEditExtensions')

  return (
    <>
      <p className="m-0 text-[12.5px] text-ink-3">{t('settings.common.shared_with_fluidd')}</p>
      <SettingsCard title={t('settings.files.browser')} icon={<Folder size={14} />}>
        <Row title={t('settings.files.sort_order')} sub={t('settings.files.sort_order_sub')}>
          <Choices
            value={sortOrder}
            onChange={setSortOrder}
            options={[
              { value: 'default', label: t('settings.files.sort_default') },
              { value: 'numeric-prefix', label: t('settings.files.sort_numeric') },
              { value: 'version', label: t('settings.files.sort_version') },
            ]}
          />
        </Row>
        <ToggleRow title={t('settings.files.drag_drop')} sub={t('settings.files.drag_drop_sub')} checked={dragDrop} onChange={setDragDrop} />
      </SettingsCard>

      <SettingsCard title={t('settings.files.editor')} icon={<FilePen size={14} />}>
        <ToggleRow title={t('settings.files.confirm_close')} checked={confirmClose} onChange={setConfirmClose} />
        <Row title={t('settings.files.auto_edit')} wide>
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
            <button type="button" className="iconbtn h-6! w-6!" onClick={() => onChange(value.filter((x) => x !== v))} aria-label={t('settings.common.remove_named', { name: v })}>
              <X size={12} />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-1.5">
        <input className="field code h-[30px] py-1" placeholder=".gcode" aria-label={t('settings.files.add_extension')} value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && add()} />
        <button type="button" className="btn btn-sm" disabled={!valid} onClick={add}>
          <Plus size={13} /> {t('common.add')}
        </button>
      </div>
    </div>
  )
}

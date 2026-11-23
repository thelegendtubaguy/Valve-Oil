import { FilePen, Folder } from 'lucide-react'
import { Choices } from '../ui'
import { ChipList, Row, SettingsCard, UiToggleRow } from './common'
import { useUiSetting } from './fluidd'
import { t } from '../../i18n'

// A file extension as typed, with its dot and in lower case, when it is one.
const parseExtension = (draft: string) => {
  const ext = draft.trim().toLowerCase().replace(/^\.?/, '.')
  return /^\.[a-z0-9_-]+$/.test(ext) ? ext : null
}

export function FilesSettings() {
  const [sortOrder, setSortOrder] = useUiSetting('general.textSortOrder')
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
        <UiToggleRow path="general.filesAndFoldersDragAndDrop" title={t('settings.files.drag_drop')} sub={t('settings.files.drag_drop_sub')} />
      </SettingsCard>

      <SettingsCard title={t('settings.files.editor')} icon={<FilePen size={14} />}>
        <UiToggleRow path="editor.confirmDirtyEditorClose" title={t('settings.files.confirm_close')} />
        <Row title={t('settings.files.auto_edit')} wide>
          <ChipList kind="code" value={extensions} onChange={setExtensions} parse={parseExtension} placeholder=".gcode" label={t('settings.files.add_extension')} />
        </Row>
      </SettingsCard>
    </>
  )
}

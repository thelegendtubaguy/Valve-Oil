import { describe, expect, it } from 'vitest'
import { columnsFor, configService, copyName, isFiltered, isLocked, isReadonly, mergeColumns, sortEntries, splitName, validName, type FsEntry } from './fs'

const f = (name: string, more: Partial<FsEntry> = {}): FsEntry => ({ kind: 'file', name, path: name, modified: 0, size: 0, permissions: 'rw', ...more })
const d = (name: string, more: Partial<FsEntry> = {}): FsEntry => ({ kind: 'dir', name, path: name, modified: 0, size: 4096, permissions: 'rw', ...more })

describe('fluidd.cfg stays read-only', () => {
  it('locks config/fluidd.cfg only', () => {
    expect(isLocked('config', 'fluidd.cfg')).toBe(true)
    expect(isLocked('config', 'sub/fluidd.cfg')).toBe(false)
    expect(isLocked('gcodes', 'fluidd.cfg')).toBe(false)
    expect(isReadonly('config', f('fluidd.cfg'))).toBe(true)
    expect(isReadonly('config', f('printer.cfg'))).toBe(false)
  })
  it('treats read-only roots and files as read-only', () => {
    expect(isReadonly('config_examples', f('example-corexy.cfg'))).toBe(true)
    expect(isReadonly('docs', f('README.md'))).toBe(true)
    expect(isReadonly('config', f('x.cfg', { permissions: 'r' }))).toBe(true)
  })
})

describe('Fluidd file filters', () => {
  it('hides what each filter names', () => {
    expect(isFiltered(f('.moonraker.conf.bkp'), ['moonraker_backup_files'])).toBe(true)
    expect(isFiltered(f('printer-20261117_062049.cfg'), ['klipper_backup_files'])).toBe(true)
    expect(isFiltered(f('printer.cfg'), ['klipper_backup_files'])).toBe(false)
    expect(isFiltered(d('.git'), ['hidden_files'])).toBe(true)
    expect(isFiltered(f('upload.mru'), ['moonraker_temporary_upload_files'])).toBe(true)
    expect(isFiltered(f('klippy.log.2026-11-16'), ['rolled_log_files'])).toBe(true)
    expect(isFiltered(f('klippy.log.1'), ['rolled_log_files'])).toBe(true)
    expect(isFiltered(f('a.gcode', { meta: { filename: 'a.gcode', print_start_time: 1 } }), ['print_start_time'])).toBe(true)
    expect(isFiltered(f('a.gcode', { meta: { filename: 'a.gcode', print_start_time: null } }), ['print_start_time'])).toBe(false)
  })
})

describe('names', () => {
  it('keeps .gcode.3mf whole', () => {
    expect(splitName('part.gcode.3mf')).toEqual(['part', '.gcode.3mf'])
    expect(splitName('printer.cfg')).toEqual(['printer', '.cfg'])
    expect(splitName('.moonraker.conf.bkp')).toEqual(['.moonraker.conf', '.bkp'])
    expect(splitName('Makefile')).toEqual(['Makefile', ''])
  })
  it('picks a free copy name', () => {
    expect(copyName('part.gcode.3mf', [])).toBe('part copy.gcode.3mf')
    expect(copyName('part.gcode', ['part copy.gcode'])).toBe('part copy 2.gcode')
  })
  it('refuses names Moonraker would take as paths', () => {
    expect(validName('a/b')).not.toBeNull()
    expect(validName('..')).not.toBeNull()
    expect(validName('  ')).not.toBeNull()
    expect(validName('ok.cfg')).toBeNull()
  })
})

describe('config services (Fluidd CONFIG_SERVICE_MAP)', () => {
  it('matches by name, then prefix, then suffix', () => {
    expect(configService('moonraker.conf')?.service).toBe('moonraker')
    expect(configService('crowsnest.conf')?.service).toBe('crowsnest')
    expect(configService('klipper-macros-qd/start_end.cfg')?.service).toBe('klipper')
    expect(configService('mooncord.json')?.service).toBe('MoonCord')
    expect(configService('drying.conf')).toBeNull()
  })
})

describe('columns', () => {
  const shown = (cols: { value: string; visible: boolean }[]) => cols.map((c) => [c.value, c.visible])

  it('takes the order and visibility of saved headers that cover every column', () => {
    const merged = mergeColumns(columnsFor('config'), [{ value: 'size', visible: false }, { value: 'modified' }])
    expect(shown(merged)).toEqual([
      ['size', false],
      ['modified', true],
    ])
  })

  it('hides and shows the columns Fluidd saved one at a time, leaving the rest as they were', () => {
    // Fluidd saves only the column that was ticked or unticked.
    expect(shown(mergeColumns(columnsFor('config'), [{ value: 'size', visible: false }]))).toEqual([
      ['modified', true],
      ['size', false],
    ])
    const defaults = columnsFor('gcodes')
    const merged = mergeColumns(defaults, [
      { value: 'size', visible: false },
      { value: 'slicer_version', visible: true },
    ])
    expect(merged.filter((c, i) => c.visible !== defaults[i].visible).map((c) => [c.value, c.visible])).toEqual([
      ['slicer_version', true],
      ['size', false],
    ])
    expect(merged.map((c) => c.value)).toEqual(defaults.map((c) => c.value))
  })

  it('keeps every column when a saved list of the right length names one the table lacks', () => {
    const merged = mergeColumns(columnsFor('config'), [{ value: 'size', visible: false }, { value: 'gone', visible: true }])
    expect(shown(merged)).toEqual([
      ['modified', true],
      ['size', false],
    ])
  })
})

describe('sorting', () => {
  it('puts folders first and empty values last', () => {
    const list = [f('b.gcode', { meta: { filename: 'b', estimated_time: 50 } }), d('zz'), f('a.gcode'), f('c.gcode', { meta: { filename: 'c', estimated_time: 900 } })]
    expect(sortEntries(list, 'estimated_time', true).map((e) => e.name)).toEqual(['zz', 'c.gcode', 'b.gcode', 'a.gcode'])
    expect(sortEntries(list, 'estimated_time', false).map((e) => e.name)).toEqual(['zz', 'b.gcode', 'c.gcode', 'a.gcode'])
  })
  it('sorts names naturally and numeric strings as numbers', () => {
    // QIDI's Moonraker reports layer_height as a string.
    const lh = (v: string) => v as unknown as number
    const list = [f('part10.gcode', { meta: { filename: 'x', layer_height: lh('0.28') } }), f('part2.gcode', { meta: { filename: 'y', layer_height: lh('0.12') } })]
    expect(sortEntries(list, 'name', false).map((e) => e.name)).toEqual(['part2.gcode', 'part10.gcode'])
    expect(sortEntries(list, 'layer_height', false).map((e) => e.name)).toEqual(['part2.gcode', 'part10.gcode'])
  })
})

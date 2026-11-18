import { describe, expect, it } from 'vitest'
import {
  cellText,
  HISTORY_COLUMNS,
  historyColumns,
  jobColors,
  lastFrame,
  mergeColumns,
  moveJob,
  multiplyFilenames,
  queueTotals,
  renderLength,
  rollup,
  sortJobs,
  filterJobs,
  statusBreakdown,
  stringArray,
  timelapseItems,
  timelapseTitle,
  toggleColumn,
  TIMELAPSE_DEFAULTS,
  type Job,
  type TimelapseSettings,
} from './logic'

const job = (over: Partial<Job>): Job => ({
  job_id: '000001',
  filename: 'cube.gcode',
  status: 'completed',
  start_time: 1000,
  end_time: 2000,
  print_duration: 900,
  total_duration: 1000,
  filament_used: 1200,
  exists: true,
  metadata: {},
  ...over,
})

describe('stringArray (the fork getStringArray)', () => {
  it.each([
    ['PLA;PETG', ['PLA', 'PETG']],
    ['"PLA";"PETG"', ['PLA', 'PETG']],
    ['["PLA","PETG"]', ['PLA', 'PETG']],
    ['["PLA",', ['["PLA",']],
    ['PLA', ['PLA']],
  ])('reads %s', (value, expected) => {
    expect(stringArray(value)).toEqual(expected)
  })
})

describe('history columns', () => {
  it('adds one column per auxiliary data field before modified and size', () => {
    const cols = historyColumns([job({ auxiliary_data: [{ provider: 'sensor', name: 'energy', value: 1.234, description: 'printer_energy', units: 'kWh' }] })])
    const keys = cols.map((c) => c.key)
    expect(keys.slice(-3)).toEqual(['auxiliary_data.energy', 'metadata.modified', 'metadata.size'])
    expect(cols.find((c) => c.key === 'auxiliary_data.energy')?.label).toBe('Printer energy')
  })

  it('shows auxiliary data with its units', () => {
    const j = job({ auxiliary_data: [{ provider: 'sensor', name: 'energy', value: 1.234, description: 'Energy', units: 'kWh' }] })
    expect(cellText(j, { key: 'auxiliary_data.energy', label: '', kind: 'aux' })).toBe('1.23 kWh')
  })

  it('splits filament names and types into lists, as the fork getter does', () => {
    const j = job({ metadata: { filename: 'cube.gcode', filament_type: 'PLA;PETG', filament_name: 'A;B' } })
    expect(cellText(j, HISTORY_COLUMNS.find((c) => c.key === 'metadata.filament_type')!)).toEqual(['PLA', 'PETG'])
  })

  it('keeps the stored order when every column is stored, as Fluidd does', () => {
    const cols = [
      { key: 'a', label: 'A', kind: 'text' as const },
      { key: 'b', label: 'B', kind: 'text' as const, visible: false },
    ]
    expect(mergeColumns(cols, [{ value: 'b', visible: true }, { value: 'a', visible: false }]).map((c) => [c.key, c.visible])).toEqual([
      ['b', true],
      ['a', false],
    ])
    expect(mergeColumns(cols, [{ value: 'b', visible: true }]).map((c) => [c.key, c.visible])).toEqual([
      ['a', undefined],
      ['b', true],
    ])
  })

  it('stores a toggle by replacing or adding the column entry', () => {
    expect(toggleColumn(undefined, 'user', true)).toEqual([{ value: 'user', visible: true }])
    expect(toggleColumn([{ value: 'user', visible: true }, { value: 'status' }], 'user', false)).toEqual([{ value: 'user', visible: false }, { value: 'status' }])
  })
})

describe('jobColors', () => {
  it('prefers filament_colors, then QIDI preset colors for the extruders used', () => {
    expect(jobColors({ filament_colors: ['#FF0000'] })).toEqual(['#FF0000'])
    expect(jobColors({ preset_colours: ['#111111', '#222222', '#333333'], used_extruders: [0, 2] })).toEqual(['#111111', '#333333'])
    expect(jobColors(undefined)).toEqual([])
  })
})

describe('filterJobs and sortJobs', () => {
  const jobs = [
    job({ job_id: '1', filename: 'b.gcode', status: 'completed', print_duration: 50 }),
    job({ job_id: '2', filename: 'a.gcode', status: 'klippy_shutdown', print_duration: 10 }),
    job({ job_id: '3', filename: 'c.gcode', status: 'cancelled', print_duration: 30, metadata: { filename: 'c.gcode', slicer: 'OrcaSlicer' } }),
  ]

  it('filters by status group and searches names and slicer', () => {
    expect(filterJobs(jobs, '', 'failed').map((j) => j.job_id)).toEqual(['2'])
    expect(filterJobs(jobs, 'orca', 'all').map((j) => j.job_id)).toEqual(['3'])
  })

  it('sorts numbers and names, with empty values last', () => {
    expect(sortJobs(jobs, 'print_duration', true).map((j) => j.job_id)).toEqual(['1', '3', '2'])
    expect(sortJobs(jobs, 'filename', false).map((j) => j.job_id)).toEqual(['2', '1', '3'])
    expect(sortJobs(jobs, 'metadata.slicer', true).map((j) => j.job_id)[0]).toBe('3')
  })
})

describe('statistics', () => {
  it('averages the totals per job', () => {
    const r = rollup({ total_jobs: 4, total_time: 400, total_print_time: 200, total_filament_used: 1000, longest_job: 150, longest_print: 100 })
    expect([r.total_avg, r.print_avg, r.filament_avg]).toEqual([100, 50, 250])
    expect(rollup({ total_jobs: 0, total_time: 0, total_print_time: 0, total_filament_used: 0, longest_job: 0, longest_print: 0 }).print_avg).toBe(0)
  })

  it('counts jobs by how they ended', () => {
    const b = statusBreakdown([job({ status: 'completed' }), job({ status: 'error' }), job({ status: 'server_exit' }), job({ status: 'in_progress' })])
    expect(Object.fromEntries(b.map((x) => [x.group, x.count]))).toEqual({ completed: 1, cancelled: 0, failed: 2, in_progress: 1 })
  })
})

describe('job queue', () => {
  const q = [
    { filename: 'a.gcode', job_id: '1', time_added: 0, time_in_queue: 0 },
    { filename: 'b.gcode', job_id: '2', time_added: 0, time_in_queue: 0 },
    { filename: 'c.gcode', job_id: '3', time_added: 0, time_in_queue: 0 },
  ]

  it('totals filament and time from file metadata, counting unknown files', () => {
    const meta = (f: string) => (f === 'c.gcode' ? null : { filename: f, filament_total: 100, filament_weight_total: 3, estimated_time: 60 })
    expect(queueTotals(q, meta)).toEqual({ filamentLength: 200, filamentWeight: 6, time: 120, withoutFile: 1 })
  })

  it('moves a job and multiplies a selection', () => {
    expect(moveJob(q, 2, 0)).toEqual(['c.gcode', 'a.gcode', 'b.gcode'])
    expect(moveJob(q, 0, 5)).toEqual(['b.gcode', 'c.gcode', 'a.gcode'])
    expect(multiplyFilenames(['a', 'b'], 2)).toEqual(['a', 'b', 'a', 'b'])
  })
})

describe('timelapse', () => {
  const s = { ...TIMELAPSE_DEFAULTS } as TimelapseSettings

  it('estimates the rendered length as Fluidd does', () => {
    expect(renderLength(295, s)).toBe('10s')
    expect(renderLength(1795, s)).toBe('1m 0s')
    expect(renderLength(95, { ...s, variable_fps: true, targetlength: 10 })).toBe('10s')
    expect(renderLength(5, { ...s, variable_fps: true, targetlength: 10, variable_fps_min: 5 })).toBe('2s')
  })

  it('reads the frame count, or the number in the last frame name', () => {
    expect(lastFrame({ framecount: 4, lastframefile: 'frame000004.jpg' })?.uniqueCount).toBe(4)
    expect(lastFrame({ lastframefile: 'frame000012.jpg' })?.uniqueCount).toBe(12)
    expect(lastFrame(null)).toBeNull()
  })

  it('pairs each video with its preview image and keeps orphan images and archives', () => {
    const items = timelapseItems([
      { path: 'timelapse_cube.gcode_20261021_0551.mp4', modified: 3, size: 1 },
      { path: 'timelapse_cube.gcode_20261021_0551.jpg', modified: 3, size: 1 },
      { path: 'timelapse_old.gcode.3mf_20260910_0657.jpg', modified: 1, size: 1 },
      { path: 'timelapse_cube.gcode_20261021_0551_frames.zip', modified: 2, size: 1 },
    ])
    expect(items.map((i) => [i.kind, i.poster?.path ?? null])).toEqual([
      ['video', 'timelapse_cube.gcode_20261021_0551.jpg'],
      ['archive', null],
      ['image', 'timelapse_old.gcode.3mf_20260910_0657.jpg'],
    ])
  })

  it('names a timelapse after its print file and time', () => {
    const t = timelapseTitle('timelapse_Orca Head_plate_1.gcode.3mf_20261028_0357.mp4')
    expect(t.title).toBe('Orca Head_plate_1')
    expect(new Date(t.when! * 1000).getMonth()).toBe(9)
    expect(timelapseTitle('timelapse_Assembly.gcode.3mf_20261117_0536_frames.zip').title).toBe('Assembly')
    expect(timelapseTitle('clip.mp4')).toEqual({ title: 'clip', when: null })
  })
})

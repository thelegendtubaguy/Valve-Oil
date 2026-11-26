import { describe, expect, it, vi } from 'vitest'

// files.ts reaches Moonraker through the printer's client, which needs a page; these lookups don't use it.
vi.mock('./printer', () => ({ client: {}, useConnected: () => false, usePrinter: () => 0 }))
import { fileHistory, type HistoryJob } from './files'

const job = (job_id: string, filename: string, start_time: number, status: string): HistoryJob => ({ job_id, filename, start_time, status, end_time: start_time + 10, print_duration: 10, total_duration: 10, filament_used: 0 })

describe('fileHistory', () => {
  // Newest first, as server.history.list returns them with order desc.
  const jobs = [job('3', 'cube.gcode', 300, 'completed'), job('2', 'part.gcode', 200, 'cancelled'), job('1', 'part.gcode', 100, 'completed')]

  it('gives a file its last job and completed prints', () => {
    const of = fileHistory(jobs)
    expect(of('part.gcode', 50)).toEqual({ last: jobs[1], count: 1 })
    expect(of('cube.gcode', 250)?.last.job_id).toBe('3')
    expect(of('other.gcode', 0)).toBeUndefined()
  })

  it("leaves out jobs of an earlier file of the same name, deleted and uploaded again", () => {
    const of = fileHistory(jobs)
    expect(of('part.gcode', 250)).toBeUndefined()
    expect(of('part.gcode', 150)).toEqual({ last: jobs[1], count: 0 })
  })

  it('has nothing before history loads', () => {
    expect(fileHistory(null)('part.gcode', 0)).toBeUndefined()
  })
})

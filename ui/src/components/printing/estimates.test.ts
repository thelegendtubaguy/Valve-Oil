import { describe, it, expect } from 'vitest'
import { fileLeft, printLayer, printLayers, slicerLeft } from './estimates'
import type { Job } from '../../state/job'

const job = (j: Partial<Job>): Job => ({
  state: 'printing', filename: 'a.gcode', printDuration: 600, totalDuration: 700, filamentUsed: 0, message: '', currentLayer: null, totalLayers: null,
  fileProgress: 0.25, displayProgress: 0, displayMessage: null, speedFactor: 1, extrudeFactor: 1, zOffset: 0, ...j,
})

describe('print estimates', () => {
  it('extrapolates the time left from file progress', () => {
    expect(fileLeft(job({}))).toBe(1800)
    expect(fileLeft(job({ fileProgress: 0.005 }))).toBeNull()
  })
  it('takes the slicer estimate less the time printed', () => {
    expect(slicerLeft(job({}), { filename: 'a', estimated_time: 3000 })).toBe(2400)
    expect(slicerLeft(job({}), null)).toBeNull()
  })
})

describe('layers', () => {
  it('prefers print_stats info', () => {
    expect(printLayers(job({ totalLayers: 57 }), { filename: 'a', object_height: 10, layer_height: 0.2, first_layer_height: 0.2 })).toBe(57)
    expect(printLayer(job({ currentLayer: 4 }), null, 3)).toBe(4)
  })
  it('works layers out from the file heights and nozzle height otherwise', () => {
    const meta = { filename: 'a', object_height: 10, layer_height: 0.2, first_layer_height: 0.2 }
    expect(printLayers(job({}), meta)).toBe(50)
    expect(printLayer(job({}), meta, 1.0)).toBe(5)
  })
})

import { describe, it, expect } from 'vitest'
import tltgPrinterWarnings from '../tltg-printer-warnings'

const inputShaperDefaults = {
  id: 'input_shaper_defaults',
  level: 'warning',
  title: 'No input shaper calibration is saved',
  message: "Using QIDI's default input shaper."
}

const inputShaperOff = {
  id: 'input_shaper_off',
  level: 'error',
  title: 'Input shaping is off',
  message: 'Acceleration after homing is limited to 10000 mm/s².'
}

describe('tltgPrinterWarnings', () => {
  it.each([
    ['absent', undefined],
    ['null', null],
    ['without warnings', {}],
    ['with non-list warnings', { warnings: 'input_shaper_off' }],
    ['with no warnings', { warnings: [] }]
  ])('shows nothing when the object is %s', (_, state) => {
    expect(tltgPrinterWarnings(state)).toEqual([])
  })

  it('keeps every well-formed warning in order, whatever its id', () => {
    const future = { id: 'some_future_check', level: 'warning', title: 'Something else', message: '' }

    expect(tltgPrinterWarnings({ warnings: [inputShaperOff, inputShaperDefaults, future] }))
      .toEqual([inputShaperOff, inputShaperDefaults, future])
  })

  it.each([
    ['not an object', 'input_shaper_off'],
    ['missing an id', { ...inputShaperOff, id: undefined }],
    ['with an empty id', { ...inputShaperOff, id: '' }],
    ['with an unknown level', { ...inputShaperOff, level: 'info' }],
    ['missing a title', { ...inputShaperOff, title: undefined }],
    ['with an empty title', { ...inputShaperOff, title: '' }],
    ['with a non-text message', { ...inputShaperOff, message: 42 }]
  ])('skips an entry %s and keeps the rest', (_, entry) => {
    expect(tltgPrinterWarnings({ warnings: [entry, inputShaperDefaults] }))
      .toEqual([inputShaperDefaults])
  })

  it.each([
    ['keeps', 'https://github.com/thelegendtubaguy/valve-oil/issues/new?template=printer-support.yml&board=box1', true],
    ['drops', 'https://example.com/thelegendtubaguy/valve-oil/issues/new?x=1', false],
    ['drops', 'javascript:alert(1)', false],
    ['drops', 42, false]
  ])("%s a board warning's issue link %s", (_, url, kept) => {
    const board = { id: 'box_firmware_unsupported_box1', level: 'warning', title: "Box 1 can't get the Valve Oil Box firmware yet.", message: 'Its bootloader is not supported yet.' }

    expect(tltgPrinterWarnings({ warnings: [{ ...board, url }] })).toEqual([kept ? { ...board, url } : board])
  })

  it('shows a repeated id once', () => {
    expect(tltgPrinterWarnings({ warnings: [inputShaperOff, { ...inputShaperOff, title: 'Again' }] }))
      .toEqual([inputShaperOff])
  })
})

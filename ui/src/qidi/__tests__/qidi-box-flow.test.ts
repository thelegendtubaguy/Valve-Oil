import { describe, it, expect } from 'vitest'
import { qidiColorNearBackground } from '../qidi-box'
import { qidiBoxFlowConnections, qidiBoxFlowLayout, qidiBoxFlowPath } from '../qidi-box-flow'

describe('qidiBoxFlowLayout', () => {
  it('puts every unit above the path when they fit in one row', () => {
    expect(qidiBoxFlowLayout(612, 2)).toEqual({ spoolSize: 44, narrow: false, compact: false, top: [1, 2, 0], bottom: [] })
  })

  it('splits the Boxes above and below the path, with the external spool below', () => {
    expect(qidiBoxFlowLayout(612, 4)).toEqual({ spoolSize: 44, narrow: false, compact: false, top: [1, 2], bottom: [3, 4, 0] })
    expect(qidiBoxFlowLayout(392, 3)).toEqual({ spoolSize: 32, narrow: true, compact: false, top: [1, 2], bottom: [3, 0] })
  })

  it('falls back to the compact layout when a row cannot fit', () => {
    expect(qidiBoxFlowLayout(312, 4)).toMatchObject({ compact: true, narrow: true, top: [1, 2, 3, 4, 0], bottom: [] })
    expect(qidiBoxFlowLayout(0, 1)).toMatchObject({ compact: true })
  })

  it('shows the external spool alone without Boxes', () => {
    expect(qidiBoxFlowLayout(290, 0)).toMatchObject({ compact: false, top: [0], bottom: [] })
  })
})

describe('qidiBoxFlowConnections', () => {
  it('draws the path guide only when nothing is loaded', () => {
    expect(qidiBoxFlowConnections(null, null, false)).toEqual([{ key: 'guide', via: ['hub', 'th', 'ex', 'noz'], dir: 'h', slot: null }])
  })

  it('draws the loaded slot from its inlet to the nozzle', () => {
    expect(qidiBoxFlowConnections(1, 1, false).map(({ key, via, slot }) => ({ key, via, slot }))).toEqual([
      { key: 'guide', via: ['hub', 'th', 'ex', 'noz'], slot: null },
      { key: 'hub', via: ['inlet:1', 'hub'], slot: 1 },
      { key: 'toolhead', via: ['hub', 'th', 'ex', 'noz'], slot: 1 }
    ])
  })

  it('stops a parked slot at the hub', () => {
    expect(qidiBoxFlowConnections(5, null, false).map(c => c.key)).toEqual(['guide', 'hub'])
  })

  it('routes the external spool straight to the toolhead', () => {
    expect(qidiBoxFlowConnections(null, 16, false).map(({ key, via }) => ({ key, via }))).toEqual([
      { key: 'guide', via: ['hub', 'th', 'ex', 'noz'] },
      { key: 'external', via: ['ext', 'th'] },
      { key: 'toolhead', via: ['th', 'ex', 'noz'] }
    ])
  })

  it('drops the slot strands in the compact layout', () => {
    expect(qidiBoxFlowConnections(1, 1, true).map(c => c.key)).toEqual(['guide', 'toolhead'])
    expect(qidiBoxFlowConnections(null, 16, true).map(c => c.key)).toEqual(['guide', 'toolhead'])
  })
})

describe('qidiBoxFlowPath', () => {
  it('draws straight lines between aligned points', () => {
    expect(qidiBoxFlowPath([[0, 10], [50, 10], [90, 10]], 'h')).toBe('M0 10 L50 10 L90 10')
  })

  it('curves vertically between offset points', () => {
    expect(qidiBoxFlowPath([[100, 0], [20, 40]], 'v')).toBe('M100 0 C100 20 20 20 20 40')
  })

  it('curves horizontally between offset points', () => {
    expect(qidiBoxFlowPath([[0, 0], [40, 20]], 'h')).toBe('M0 0 C20 0 20 20 40 20')
  })

  it('draws nothing for a single point', () => {
    expect(qidiBoxFlowPath([[1, 1]], 'h')).toBe('')
  })
})

describe('qidiColorNearBackground', () => {
  it.each([
    ['#1F1F1F', true, true],
    ['#1F1F1F', false, false],
    ['#F2F2F2', false, true],
    ['#F2F2F2', true, false],
    ['#FF362D', true, false],
    [null, true, false]
  ])('%s on dark=%s is %s', (color, dark, expected) => {
    expect(qidiColorNearBackground(color, dark)).toBe(expected)
  })
})

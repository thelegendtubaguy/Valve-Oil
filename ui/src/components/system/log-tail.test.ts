import { describe, expect, it } from 'vitest'
import { MAX_LINES, tailText } from './log-tail'

describe('tailText', () => {
  it('keeps a whole log whole', () => {
    expect(tailText('a\nb\n', 0, 4)).toEqual({ lines: ['a', 'b'], shown: 4, total: 4, truncated: false })
  })
  it('drops the partial first line of a tail', () => {
    expect(tailText('ial line\nnext\nlast\n', 100, 119)).toEqual({ lines: ['next', 'last'], shown: 19, total: 119, truncated: true })
  })
  it('caps very long logs to the newest lines', () => {
    const t = tailText(Array.from({ length: MAX_LINES + 5 }, (_, i) => `l${i}`).join('\n'), 0, 1)
    expect(t.lines.length).toBe(MAX_LINES)
    expect(t.lines[0]).toBe('l5')
    expect(t.truncated).toBe(true)
  })
})

import { describe, expect, it } from 'vitest'
import { split } from '../shlex'

// Expected values are what the `shlex` package's split() returns, which Fluidd's parser uses.
describe('split', () => {
  it.each([
    ['NAME=a CENTER=1,2 POLYGON=[[1,2],[3,4]]', ['NAME=a', 'CENTER=1,2', 'POLYGON=[[1,2],[3,4]]']],
    [`NAME="a b" X='c d'`, ['NAME=a b', 'X=c d']],
    ['A="x\\"y" B=a\\ b', ['A=x"y', 'B=a b']],
    ['  lead  trail  ', ['lead', 'trail']],
    ['E=""', ['E=']],
  ])('splits %s', (input, words) => {
    expect(split(input)).toEqual(words)
  })

  it.each(['NAME="open', 'x\\'])('rejects %s', (input) => {
    expect(() => split(input)).toThrow()
  })
})

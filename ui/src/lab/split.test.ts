import { describe, expect, it } from 'vitest'
import { canCut, parts } from './split'

describe('splitting at several points', () => {
  const lines = [{ speaker: 'A', start: 10, end: 20 }, { speaker: 'B', start: 25, end: 60 }, { speaker: 'A', start: 700, end: 710 },
    { speaker: 'C', start: 1300, end: 1400 }, { speaker: null, start: 1500, end: 1510 }, { speaker: 'A', start: 1600, end: 1750 }]

  it('lists the parts in order with who talks most in each', () => {
    expect(parts([1200, 600], 1800, lines)).toEqual([
      { start: 0, end: 600, speakers: ['B', 'A'] },
      { start: 600, end: 1200, speakers: ['A'] },
      { start: 1200, end: 1800, speakers: ['A', 'C'] }])
    expect(parts([], 1800, lines)).toHaveLength(1)
  })

  it('keeps cuts 30 s from the ends and from each other', () => {
    expect(canCut(29, 1800, [])).toBe(false)
    expect(canCut(1775, 1800, [])).toBe(false)
    expect(canCut(620, 1800, [600])).toBe(false)
    expect(canCut(630, 1800, [600])).toBe(true)
  })
})

import { describe, expect, it } from 'vitest'
import { markedLines, markedText } from './marks'

describe('the marked moments', () => {
  it('says how many and flags the lines they followed', () => {
    expect(markedText(1)).toBe('You marked this moment')
    expect(markedText(3)).toBe('You marked 3 moments')
    const marks = [{ id: 'a', at: 5, source: 'recorder' as const, line: 2 }, { id: 'b', at: 9, source: 'phone' as const, line: null },
      { id: 'c', at: 9.5, source: 'phone' as const, line: 2 }]
    expect([...markedLines(marks)]).toEqual([2])
  })
})

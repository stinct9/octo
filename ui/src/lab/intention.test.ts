import { describe, expect, it } from 'vitest'
import { happenedText } from './intention'

describe('what the review said of an intention', () => {
  it('says it in words, or why there is nothing yet', () => {
    expect(happenedText('partly', true)).toBe('Partly')
    expect(happenedText(null, true)).toBe("The review couldn't say")
    expect(happenedText(undefined, false)).toBe('The review will say whether it happened')
  })
})

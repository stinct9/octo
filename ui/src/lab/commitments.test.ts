import { describe, expect, it } from 'vitest'
import { agreedText, marks } from './commitments'
import type { Commitment } from './types'

const at = (d: number) => new Date(2026, 9, d, 10).getTime() / 1000
const c = (by: string, what: string, due: string | null, mine: boolean, day = 19): Commitment => ({
  id: what, session_id: 's', at: at(day), mine, by, to: mine ? 'Asha' : 'Sam', by_person: null, to_person: null, what,
  quote: '', line: 0, start: 0, end: 1, due, state: null, ticktick: null,
})

describe('Copy what we agreed', () => {
  it('lists both sides with their dates', () => {
    expect(agreedText([c('Sam', 'Send Asha the deck', '2026-10-22', true), c('Asha', 'Review the budget', null, false)],
      'What we agreed, Mon 19 Oct')).toBe('What we agreed, Mon 19 Oct\n- Sam: Send Asha the deck, by Thu 22 Oct\n- Asha: Review the budget')
  })
  it('says when each was said once they span conversations', () => {
    expect(agreedText([c('Sam', 'Call', null, true, 12), c('Asha', 'Write', null, false)]))
      .toBe('What we agreed\n- Sam: Call (said Mon 12 Oct)\n- Asha: Write (said Mon 19 Oct)')
  })
})

describe('the marks', () => {
  it("are Done / Dropped on yours and They did / They didn't on theirs", () => {
    expect(marks(true).map((m) => m.state)).toEqual(['done', 'dropped'])
    expect(marks(false).map((m) => m.state)).toEqual(['did', 'didnt'])
  })
})

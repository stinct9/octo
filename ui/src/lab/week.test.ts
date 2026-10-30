import { describe, expect, it } from 'vitest'
import { countsLine, focusLine, weekRange } from './week'

describe('your week (B17)', () => {
  it('names the seven days from Monday, across a month too', () => {
    expect(weekRange('2026-10-05')).toBe('5–11 Oct')
    expect(weekRange('2026-09-28')).toBe('28 Sep – 4 Oct')
  })
  it('counts heard, reviewed and acted on', () => {
    expect(countsLine({ heard: 1, reviewed: 0, acted: 2 })).toBe('1 conversation heard · 0 reviewed · 2 acted on')
  })
  it("says the focus's number against the usual, and nothing without one", () => {
    const m = (v: number) => ({ value: v, lo: null, hi: null, conversations: 1 })
    expect(focusLine({ id: 'f', text: 't', measure: 'talk_share', week: m(0.42), usual: m(0.5) }))
      .toBe('Your share of the talk: 42% this week, usually 50%')
    expect(focusLine({ id: 'f', text: 't', measure: 'questions', week: null, usual: m(2) })).toBe('Questions per 10 minutes: nothing to measure this week')
    expect(focusLine({ id: 'f', text: 't', measure: null, week: null, usual: null })).toBeNull()
  })
})

import { describe, expect, it } from 'vitest'
import { sentText } from './ask'

describe('sentText', () => {
  const conv = { session_id: 's', at: 1, with: ['Ravi'], lines: 38, whole: true }
  it('says what went, and why it was picked', () => {
    expect(sentText({ lines: 38, people: ['Ravi'], dates: 'this month', left: 0, conversations: [conv] }))
      .toBe('Sent 38 lines from 1 conversation (Ravi, this month)')
    expect(sentText({ lines: 400, people: [], dates: null, left: 3, conversations: [conv, conv] }))
      .toBe("Sent 400 lines from 2 conversations; 3 more conversations matched but didn't fit")
  })
  it('says when nothing went', () => {
    expect(sentText({ lines: 0, people: ['Priya'], dates: null, left: 0, conversations: [] }))
      .toBe('Nothing sent: no conversation matched (Priya)')
  })
})

import { describe, expect, it } from 'vitest'
import { alsoLine, leansLine, minutesLeft, resultLine, stateLine, type Target } from './filler'

const t = (o: Partial<Target>): Target => ({ target: 'kind of', phrases: ['kind of'], his: 70, others: 116, leans: 2.2,
  status: 'growing', need: 100, weeks_left: 1.3, training: null, ...o })

describe('filler cue targets (B29)', () => {
  it('says each filler’s state in plain words', () => {
    expect(stateLine(t({}))).toBe('Not enough yet: 70 examples so far · enough in about a week')
    expect(stateLine(t({ his: 19, weeks_left: 13.5 }))).toBe('Not enough yet: 19 examples so far · enough in about 3 months')
    expect(stateLine(t({ status: 'ready', his: 453 }))).toBe('453 examples · ready to train')
    expect(stateLine(t({ status: 'ordinary', his: 132 }))).toBe('132 examples · mostly an ordinary word for you')
    expect(stateLine(t({ status: 'on' }))).toBe('On the recorder')
    expect(stateLine(t({ status: 'none', his: 0 }))).toBe('Not enough yet: none so far')
  })
  it('names the hesitations as one sound and says who says it more', () => {
    expect(alsoLine(t({ target: 'uh', phrases: ['ah', 'uh', 'um'] }))).toBe('uh, um and the like (one sound)')
    expect(alsoLine(t({}))).toBeNull()
    expect(leansLine(t({}))).toBe('You say it 2.2× as often as the people you talk with')
    expect(leansLine(t({ leans: 0.6 }))).toBe('The people you talk with say it more than you')
  })
  it('estimates the minutes left from the tests done', () => {
    const run = { started_at: 0, running: true, step: 'testing' as const, done: 3, trainings: 9 }
    expect(minutesLeft(run, 360)).toBe(15)  // 2 min a test, 6 to go, and ~3 to finish
    expect(minutesLeft({ ...run, done: 0 }, 360)).toBeNull()
  })
  it('says what a finished training found', () => {
    expect(resultLine('uh', { started_at: 0, caught: 0.61, candidate: true })).toContain('caught 61% of your “uh”s')
    expect(resultLine('uh', { started_at: 0, caught: 0.25, candidate: false, before: null })).toContain('at least 1 in 3')
    expect(resultLine('you know', { started_at: 0, caught: 0.64, before: 0.62, candidate: false })).toContain('Not better yet')
    expect(resultLine('uh', { started_at: 0, cancelled: true })).toBe('Training was cancelled.')
    expect(resultLine('uh', { started_at: 0, running: true })).toBeNull()
  })
})

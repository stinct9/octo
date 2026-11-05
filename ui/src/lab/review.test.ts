import { describe, expect, it } from 'vitest'
import { answerItems, entries, heldCounts, itemQuote, itemText, newToYouEntries } from './review'
import type { Insights, Quote } from './types'

const q = (line: number, speaker: string, start = line * 10): Quote => ({ line, start, speaker, text: `t${line}`, uncertain: false })
const finding = (principle: number, quotes: Quote[], tryNext: string | null = null, held: 'held' | 'partly' | 'missed' | null = null) =>
  ({ principle, title: `P${principle}`, observation: '', quotes, unverified_quotes: 0, flags: [], try_next_time: tryNext, held })
const review: Insights = {
  kind: 'review', model: 'm', rubric: 'r', skipped: null, no_moment: [], dropped: [], beneath: [], seconds: 0,
  one_thing: { headline: 'Tell Asha early', why: 'w', quotes: [q(2, 'A')], unverified_quotes: 0, flags: [], next: 0 },
  reading: [{ person: 'Kiran', headline: 'h', observation: '', quotes: [q(0, 'B')], unverified_quotes: 0, flags: [] }],
  between: [{ headline: 'Left open', observation: '', quotes: [q(1, 'B'), q(2, 'A')], unverified_quotes: 0, flags: [] }],
  conduct: [finding(3, [q(1, 'B'), q(4, 'A')], null, 'partly'), finding(8, [q(4, 'A')], null, 'held')],
  next: [{ title: 'Do it', detail: '', why: '', due: null }],
}

describe('the quote an item is heard by', () => {
  it("is the owner's own words on a principle, else the first quote", () => {
    const isMe = (sp: string | null) => sp === 'A'
    expect(itemQuote(review.conduct[0].quotes, true, isMe)?.line).toBe(4)
    expect(itemQuote(review.between?.[0].quotes, false, isMe)?.line).toBe(1)
    expect(itemQuote([q(1, 'B')], true, isMe)?.line).toBe(1)  // none of his: the first
    expect(itemQuote([], true, isMe)).toBeNull()
  })
})

describe('entries', () => {
  it('follow the page: the one thing, the steps, then the full review', () => {
    expect(entries(review).map((e) => e.id)).toEqual(['o', 'n0', 'r0', 'l0', 'p3', 'p8'])
    expect(entries({ ...review, one_thing: null, conduct: [finding(2, [], 'x')] }).map((e) => e.id)).toEqual(['n0', 'r0', 'l0', 'p2', 't2'])
    expect(entries({ ...review, skipped: 'too short' })).toEqual([])
  })
  it('the one question lists what the review says first and the steps last, without its questions', () => {
    const withQuestion = { ...review, beneath: [{ question: 'Was she waiting for you?', quotes: [] }] }
    expect(newToYouEntries(withQuestion).map((e) => e.id)).toEqual(['o', 'r0', 'l0', 'p3', 'p8', 'n0'])
    expect(newToYouEntries(review)[0]).toEqual({ id: 'o', group: 'one', eyebrow: 'The one thing', title: 'Tell Asha early' })
  })
  it('a step ticked on its own row counts in the answer as it did in the list (review 5 Nov, item 4)', () => {
    expect(answerItems(review, new Set(['n0', 'p3']))).toEqual(['p3', 'n0'])
    expect(answerItems(review, new Set(['n0']))).toEqual(['n0'])
    expect(answerItems(review, new Set(['x9']))).toEqual([])  // nothing the review doesn't have
  })
  it('practise starts from the item\'s own words', () => {
    expect(itemText(review, 'o')).toBe('Tell Asha early')
    expect(itemText(review, 'p8')).toBe('P8')
    expect(itemText(review, 'n0')).toBe('Do it')
  })
})

describe('held', () => {
  it('counts each judgement, in a fixed order, leaving out zeros', () => {
    expect(heldCounts(review.conduct)).toEqual([['held', 1], ['partly', 1]])
    expect(heldCounts([finding(1, [])])).toEqual([])  // reviews before 6 Oct have none
  })
})

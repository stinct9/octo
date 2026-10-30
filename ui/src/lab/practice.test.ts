import { describe, expect, it } from 'vitest'
import { cantRecord, lastText, recordType, takesSpread } from './practice'
import { parse } from './router'
import type { DrillRow } from './types'

const row = (o: Partial<DrillRow>): DrillRow => ({ measure: 'fillers', value: 3, usual: 4, lo: 3.5, hi: 4.5, state: 'low',
  conversations: 7, groups_higher: false, last: 4.25, ...o })

describe('practice (B18)', () => {
  it('records only what the lab can read', () => {
    expect(recordType((t) => t === 'audio/mp4')).toBe('audio/mp4')
    expect(recordType((t) => t.startsWith('audio/webm'))).toBeNull()
  })

  it('keeps the Octo app on the Mac to playback, and says why elsewhere', () => {
    expect(cantRecord({ inMacApp: true, mic: true, type: 'audio/mp4' })).toMatch(/play takes back/)
    expect(cantRecord({ inMacApp: false, mic: false, type: 'audio/mp4' })).toMatch(/microphone/)
    expect(cantRecord({ inMacApp: false, mic: true, type: null })).toMatch(/format/)
    expect(cantRecord({ inMacApp: false, mic: true, type: 'audio/mp4' })).toBeNull()
  })

  it('says a take against the takes before it', () => {
    expect(lastText(row({}))).toBe('last time 4.3')
    expect(lastText(row({ last: null }))).toBe('')
    expect(takesSpread(row({}), 60, 200)).toBe('About usual is 3.5–4.5: the middle half of your 7 takes before this.')
    expect(takesSpread(row({ state: 'no_usual', conversations: 1 }), 60, 200)).toMatch(/there is 1/)
    expect(takesSpread(row({ state: 'short' }), 60, 40)).toMatch(/Under 60 words/)
  })

  it('routes to the page and to one take', () => {
    expect(parse('#/practice')).toEqual({ name: 'practice' })
    expect(parse('#/practice/drl_20261021_ab12cd34')).toEqual({ name: 'practice', take: 'drl_20261021_ab12cd34' })
  })
})

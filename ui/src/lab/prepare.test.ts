import { describe, expect, it } from 'vitest'
import { marked, sendsText, spellings } from './prepare'
import { parse } from './router'
import type { PreparePreview } from './types'

const base: PreparePreview = { person: { id: 'per_t', name: 'Leela' }, names: ['leela'], shared_first: [], short_first: null, lines: 0, left: 0,
  max_lines: 400, with: [], mentions: [] }
const conv = { session_id: 's', at: 0, with: [], lines: 10, whole: true }

describe('prepare', () => {
  it('says what goes, and what was unticked or never there', () => {
    expect(sendsText(base)).toBe('Nothing to send: no conversation with Leela or naming them yet')
    expect(sendsText({ ...base, mentions: [{ ...conv, lines: 0, ticked: false, hits: 1, shown: [] }] }))
      .toBe('Nothing to send: no conversation with Leela or naming them is ticked')
    expect(sendsText({ ...base, lines: 30, left: 1, with: [conv], mentions: [{ ...conv, lines: 20, ticked: true, hits: 2, shown: [] },
      { ...conv, lines: 0, ticked: false, hits: 1, shown: [] }] }))
      .toBe("Sends 30 lines: 1 conversation with Leela, and 1 conversation naming them; 1 more didn't fit")
  })
  it('says it in the past once read', () => {
    expect(sendsText({ ...base, lines: 10, with: [conv] }, true)).toBe('Read 10 lines: 1 conversation with Leela')
  })
  it('marks the name in a line', () => {
    expect(marked("I spoke to Leela's team", ['leela'])).toEqual([{ text: 'I spoke to ', hit: false }, { text: 'Leela', hit: true }, { text: "'s team", hit: false }])
    expect(marked('no one here', ['leela'])).toEqual([{ text: 'no one here', hit: false }])
  })
  it('reads other spellings and its page', () => {
    expect(spellings(' Tam, Tami ,, Tam')).toEqual(['Tam', 'Tami'])
    expect(parse('#/people/per_t/prepare')).toEqual({ name: 'prepare', id: 'per_t' })
    expect(parse('#/people/per_t')).toEqual({ name: 'person', id: 'per_t' })
  })
})

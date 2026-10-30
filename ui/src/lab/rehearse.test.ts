import { describe, expect, it } from 'vitest'
import { guessLine, otherName, sends } from './rehearse'
import { parse } from './router'

describe('rehearsals (B26)', () => {
  it('names the other person, from People or described', () => {
    expect(otherName({ name: 'Kiran', who: null })).toBe('Kiran')
    expect(otherName({ name: null, who: 'a report who is upset' })).toBe('a report who is upset')
  })

  it('says plainly the person is the model\'s guess', () => {
    const r = { person_id: 'p', name: 'Kiran', readings: [{ session_id: 's', at: 1, headline: 'h', observation: 'o', partly: false, note: null }] }
    expect(guessLine(r)).toBe('This is the model\'s version of Kiran, from 1 reading the reviews made of Kiran: a guess, not Kiran. It\'s told to make nothing about them up as fact.')
    expect(guessLine({ ...r, readings: [] })).toContain('with nothing the reviews have read in them yet')
    expect(guessLine({ person_id: null, name: null, readings: [] })).toContain('from your description only')
  })

  it('sends on Enter, not Shift+Enter or while composing', () => {
    expect(sends({ key: 'Enter', shiftKey: false, isComposing: false })).toBe(true)
    expect(sends({ key: 'Enter', shiftKey: true, isComposing: false })).toBe(false)
    expect(sends({ key: 'Enter', shiftKey: false, isComposing: true })).toBe(false)
  })

  it('routes: the list, one, and a new one with someone', () => {
    expect(parse('#/rehearse')).toEqual({ name: 'rehearse' })
    expect(parse('#/rehearse/reh_20261027_ab12cd34')).toEqual({ name: 'rehearse', id: 'reh_20261027_ab12cd34' })
    expect(parse('#/rehearse/with/per_asha')).toEqual({ name: 'rehearse', person: 'per_asha' })
    expect(parse('#/practice/reh_x')).toEqual({ name: 'practice', take: 'reh_x' })  // a take's own route is unchanged
  })
})

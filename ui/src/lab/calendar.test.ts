import { describe, expect, it } from 'vitest'
import { MAX_OFFERS, eventsText, offerLabel, offers } from './calendar'
import type { Invitee, Speaker } from './types'

const inv = (name: string, extra: Partial<Invitee> = {}): Invitee =>
  ({ key: name.toLowerCase(), name, email: null, calendar_name: name, person_id: null, person: null, sure: false, ...extra })
const sp = (name: string | null, state: Speaker['state']): Speaker =>
  ({ letter: 'A', name, state, self: false, alternatives: [], talk_s: 1, share: 1 })

describe('calendar', () => {
  it('offers invitees not already settled here', () => {
    const cal = { events: [], invitees: [inv('Priya'), inv('Ravi')] }
    expect(offers(cal, [sp('priya', 'confirmed'), sp('Ravi', 'suggested')]).map((p) => p.name)).toEqual(['Ravi'])
    expect(offers(null, [])).toEqual([])
  })
  it('offers people Octo knows first, a big meeting cut short', () => {
    const many = Array.from({ length: 20 }, (_, i) => inv(`P${i}`))
    const cal = { events: [], invitees: [...many, inv('Priya', { person: 'Priya', person_id: 'p' })] }
    const got = offers(cal, [])
    expect(got).toHaveLength(MAX_OFFERS)
    expect(got[0].name).toBe('Priya')
  })
  it('asks about a guess until it was kept', () => {
    expect(offerLabel(inv('Priya', { person: 'Priya', person_id: 'p' }))).toBe('Priya?')
    expect(offerLabel(inv('Priya', { person: 'Priya', person_id: 'p', sure: true }))).toBe('Priya')
    expect(offerLabel(inv('Lena'))).toBe('Lena')
  })
  it('says nothing without events', () => {
    expect(eventsText({ events: [], invitees: [] })).toBeNull()
    expect(eventsText({ events: [{ title: 'Sync', start: 0, end: 600 }], invitees: [] })).toMatch(/^In your calendar: Sync, /)
  })
})

// B23: a conversation's calendar (octo/events.py): the events it overlaps, and their invitees offered as speakers.
import type { Invitee, SessionCalendar, Speaker } from './types'

const hm = (t: number) => new Date(t * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })

/** "In your calendar: 1:1 with Priya, 15:00–15:30" */
export function eventsText(cal: SessionCalendar | null | undefined): string | null {
  if (!cal?.events.length) return null
  return 'In your calendar: ' + cal.events.map((e) => `${e.title || '(No title)'}, ${hm(e.start)}–${hm(e.end)}`).join(' · ')
}

export const MAX_OFFERS = 8  // a big meeting's invitees would bury the speaker's own buttons

/** The invitees to offer: not already a settled speaker here; people Octo knows first, MAX_OFFERS at most. */
export function offers(cal: SessionCalendar | null | undefined, speakers: Speaker[]): Invitee[] {
  const taken = new Set(speakers.filter((s) => s.name && (s.state === 'confirmed' || s.state === 'auto')).map((s) => s.name!.toLowerCase()))
  const left = (cal?.invitees ?? []).filter((p) => !taken.has(p.name.toLowerCase()))
  return [...left.filter((p) => p.person_id), ...left.filter((p) => !p.person_id)].slice(0, MAX_OFFERS)
}

/** A guess at someone Octo knows reads as a question until the owner names a speaker with it once. */
export const offerLabel = (p: Invitee) => (p.person && !p.sure ? `${p.name}?` : p.name)
export const offerTitle = (p: Invitee) =>
  [p.calendar_name, p.email].filter(Boolean).join(' · ') + ' in the calendar' + (p.person && !p.sure ? `; maybe ${p.person}` : '')

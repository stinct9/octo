// What the conversation page derives from a review: the quote each item is heard by, every item in
// page order (for the one question, "What here was new to you?"), and how the principles went. Pure,
// so it's tested without a page.
import type { Finding, Held, Insights, Quote } from './types'

/** The quote an item is heard by from its headline row: on a principle, the owner's own words where it
 *  has them (that's what it judges); elsewhere its first quote. Plays its whole line. */
export function itemQuote(quotes: Quote[] | undefined, preferMe: boolean, isMe: (speaker: string | null) => boolean): Quote | null {
  if (!quotes?.length) return null
  return (preferMe && quotes.find((q) => isMe(q.speaker))) || quotes[0]
}

export type EntryGroup = 'one' | 'reading' | 'between' | 'principle' | 'try' | 'step' | 'question'

/** One thing the review says, as the page and the one question name it. */
export interface Entry { id: string; group: EntryGroup; eyebrow: string; title: string }

/** Everything in a review, in the order the page shows it: the one thing, the steps, then the full
 *  review (readings, between the lines, principles and their try-next-time, beneath-the-surface
 *  questions). Item ids are the ones `octo/review.py` rates (o, n0, r0, l0, p8, t8, b0). */
export function entries(ins: Insights | null): Entry[] {
  if (!ins || ins.skipped) return []
  const out: Entry[] = []
  if (ins.one_thing) out.push({ id: 'o', group: 'one', eyebrow: 'The one thing', title: ins.one_thing.headline })
  for (const [i, t] of (ins.next ?? []).entries()) out.push({ id: `n${i}`, group: 'step', eyebrow: 'Step', title: t.title ?? t.action ?? '' })
  for (const [i, r] of (ins.reading ?? []).entries()) out.push({ id: `r${i}`, group: 'reading', eyebrow: r.person, title: r.headline })
  for (const [i, b] of (ins.between ?? []).entries()) out.push({ id: `l${i}`, group: 'between', eyebrow: 'Between the lines', title: b.headline })
  for (const f of ins.conduct) {
    out.push({ id: `p${f.principle}`, group: 'principle', eyebrow: `Principle ${f.principle}`, title: f.title })
    if (f.try_next_time) out.push({ id: `t${f.principle}`, group: 'try', eyebrow: `Within principle ${f.principle}`, title: f.try_next_time })
  }
  for (const [i, b] of ins.beneath.entries()) out.push({ id: `b${i}`, group: 'question', eyebrow: 'A question', title: b.question })
  return out
}

/** What the one question lists: everything but the beneath-the-surface questions (optional, and
 *  questions rather than findings), with what the review says first and the steps last. */
export function newToYouEntries(ins: Insights | null): Entry[] {
  const all = entries(ins).filter((e) => e.group !== 'question')
  return [...all.filter((e) => e.group !== 'step'), ...all.filter((e) => e.group === 'step')]
}

/** The words an item starts a focus from (Practise this): its headline, title or task. */
export function itemText(ins: Insights | null, id: string): string {
  return entries(ins).find((e) => e.id === id)?.title ?? ''
}

export const HELD_LABEL: Record<Held, string> = { held: 'Held', partly: 'Partly', missed: 'Missed', depends: 'Depends' }

/** How the principles went in this review: counts per judgement, in a fixed order, zeros left out. */
export function heldCounts(conduct: Finding[]): [Held, number][] {
  const n: Record<Held, number> = { held: 0, partly: 0, missed: 0, depends: 0 }
  for (const f of conduct) if (f.held) n[f.held] += 1
  return (Object.keys(n) as Held[]).filter((k) => n[k] > 0).map((k) => [k, n[k]])
}

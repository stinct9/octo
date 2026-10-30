// Rehearsals (B26): who the model plays, and the words the page says about it being a guess.
import type { Rehearsal, RehearsalRow } from './types'

/** "Kiran", or "a report who's upset about a rating". */
export function otherName(r: Pick<RehearsalRow, 'name' | 'who'>): string {
  return r.name ?? r.who ?? 'someone'
}

/** Said plainly on every rehearsal: the other person's lines are the model's, from what it was told. */
export function guessLine(r: Pick<Rehearsal, 'person_id' | 'name' | 'readings'>): string {
  if (!r.person_id) return 'The other person is the model\'s, from your description only.'
  const n = r.readings.length
  const from = n ? `from ${n} reading${n === 1 ? '' : 's'} the reviews made of ${r.name}` : 'with nothing the reviews have read in them yet'
  return `This is the model's version of ${r.name}, ${from}: a guess, not ${r.name}. It's told to make nothing about them up as fact.`
}

/** Enter sends; Shift+Enter is a new line; never while an input method is composing. */
export function sends(e: Pick<KeyboardEvent, 'key' | 'shiftKey' | 'isComposing'>): boolean {
  return e.key === 'Enter' && !e.shiftKey && !e.isComposing
}

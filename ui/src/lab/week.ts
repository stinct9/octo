// Your week (B17): the card's words, kept out of the component so they can be tested.
import { dueDay, MEASURE_LABEL, measureValue } from './format'
import type { WeekCard } from './types'

/** "5–11 Oct", or "28 Sep – 4 Oct" across a month. */
export function weekRange(monday: string): string {
  const [y, m, d] = monday.split('-').map(Number)
  const end = new Date(y, m - 1, d + 6)
  const [a, b] = [dueDay(monday), dueDay(`${end.getFullYear()}-${end.getMonth() + 1}-${end.getDate()}`)]
  const [, ad, am] = a.split(' ')
  const [, bd, bm] = b.split(' ')
  return am === bm ? `${ad}–${bd} ${bm}` : `${ad} ${am} – ${bd} ${bm}`
}

/** "3 heard · 2 reviewed · 1 acted on". */
export function countsLine(c: WeekCard['counts']): string {
  return `${c.heard} conversation${c.heard === 1 ? '' : 's'} heard · ${c.reviewed} reviewed · ${c.acted} acted on`
}

/** The focus's number that week against the usual, in neutral words; null when it tracks none or there's nothing to say. */
export function focusLine(f: WeekCard['focus']): string | null {
  if (!f?.measure) return null
  const m = f.measure
  if (!f.week) return `${MEASURE_LABEL[m]}: nothing to measure this week`
  return `${MEASURE_LABEL[m]}: ${measureValue(m, f.week.value)} this week${f.usual ? `, usually ${measureValue(m, f.usual.value)}` : ''}`
}

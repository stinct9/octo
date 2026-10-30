// From above (B37, octo/month.py): the month grid's cells, their tint, and what each says aloud.
import type { FillerRate, MonthDay, MonthView } from './types'

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
export const WEEKDAY_SHORT = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const parts = (iso: string) => iso.split('-').map(Number)
/** 0 Monday … 6 Sunday. */
export const weekday = (iso: string) => { const [y, m, d] = parts(iso); return (new Date(y, m - 1, d).getDay() + 6) % 7 }

export type Cell = (MonthDay & { state: 'day' | 'future' | 'before'; level: number }) | null

/** What the month shows: conversations (tinted by minutes talked) or the owner's fillers (tinted by their rate). */
export type Show = 'talk' | 'fillers'

/** Weeks of seven, Monday first, with blanks before the 1st and after the last day. */
export function grid(m: MonthView, show: Show = 'talk'): Cell[][] {
  const by = (d: MonthDay) => (show === 'talk' ? d.minutes : d.fillers?.rate ?? 0)
  const has = (d: MonthDay) => (show === 'talk' ? d.count > 0 : !!d.fillers)
  const most = Math.max(0, ...m.days.map(by))
  const cells: Cell[] = Array(m.days.length ? weekday(m.days[0].date) : 0).fill(null)
  for (const d of m.days) {
    const state = d.date > m.today ? 'future' : m.first && d.date < m.first ? 'before' : 'day'
    cells.push({ ...d, state, level: has(d) ? Math.max(1, level(by(d), most)) : 0 })
  }
  while (cells.length % 7) cells.push(null)
  return Array.from({ length: cells.length / 7 }, (_, k) => cells.slice(k * 7, k * 7 + 7))
}

/** The tint: 0 for none, then four steps against the month's busiest day. */
export function level(minutes: number, most: number): number {
  return minutes <= 0 || most <= 0 ? 0 : Math.min(4, Math.ceil((minutes / most) * 4))
}

/** "October 2026". */
export function monthTitle(ym: string): string { const [y, m] = parts(ym); return `${MONTHS[m - 1]} ${y}` }
/** "Tuesday 28 October". */
export function dayTitle(iso: string): string { const [, m, d] = parts(iso); return `${WEEKDAYS[weekday(iso)]} ${d} ${MONTHS[m - 1]}` }
/** "52 min", "3 h 5 min". */
export function minutesText(min: number): string {
  const h = Math.floor(min / 60)
  return h ? `${h} h${min % 60 ? ` ${min % 60} min` : ''}` : `${min} min`
}

const n = (k: number, one: string) => `${k} ${one}${k === 1 ? '' : 's'}`
/** What a day says aloud: "Tuesday 28 October: 3 conversations, 52 min, with Asha, Ravi; 1 review to answer". */
/** "2.4 per 100 words (12 in 500)". */
export const fillersText = (f: FillerRate) => `${f.rate} per 100 words (${f.fillers} in ${f.words})`

export function spoken(c: NonNullable<Cell>, show: Show = 'talk'): string {
  if (c.state === 'future') return `${dayTitle(c.date)}: still to come`
  if (!c.count) return `${dayTitle(c.date)}: no conversations`
  if (show === 'fillers') return `${dayTitle(c.date)}: ${c.fillers ? `your fillers ${fillersText(c.fillers)}` : `${n(c.count, 'conversation')}, none on the fillers' line`}`
  const who = c.people.length ? `, with ${c.people.slice(0, 4).join(', ')}${c.people.length > 4 ? ` and ${c.people.length - 4} more` : ''}` : ''
  const left = c.to_answer ? `; ${n(c.to_answer, 'review')} to answer` : ''
  return `${dayTitle(c.date)}: ${n(c.count, 'conversation')}, ${minutesText(c.minutes)}${who}${left}`
}

/** The month or day after `iso` moved by `by` days, as YYYY-MM-DD. */
export function shift(iso: string, by: number): string {
  const [y, m, d] = parts(iso)
  const x = new Date(y, m - 1, d + by)
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`
}

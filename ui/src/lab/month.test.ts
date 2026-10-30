import { describe, expect, it } from 'vitest'
import { grid, level, minutesText, monthTitle, shift, spoken, weekday } from './month'
import { parse } from './router'
import type { FillerRate, MonthView } from './types'

const day = (date: string, count = 0, minutes = 0) => ({ date, count, minutes, reviews: 0, to_answer: 0, people: [] as string[], fillers: null as FillerRate | null })
const oct: MonthView = { month: '2026-10', today: '2026-10-30', first: '2026-10-02', prev: '2026-09', next: '2026-11',
  days: Array.from({ length: 31 }, (_, k) => day(`2026-10-${String(k + 1).padStart(2, '0')}`)),
  totals: { conversations: 0, minutes: 0, reviews: 0, answered: 0, people: 0, fillers: null } }

describe('month', () => {
  it('lays out Monday-first weeks with blanks, and tells days to come and before the lab from quiet ones', () => {
    expect(weekday('2026-10-01')).toBe(3)  // a Thursday
    const g = grid(oct)
    expect(g[0].slice(0, 3)).toEqual([null, null, null])
    expect(g.every((w) => w.length === 7)).toBe(true)
    const cells = g.flat().filter((c) => c)
    expect(cells.find((c) => c!.date === '2026-10-01')!.state).toBe('before')
    expect(cells.find((c) => c!.date === '2026-10-05')!.state).toBe('day')
    expect(cells.find((c) => c!.date === '2026-10-31')!.state).toBe('future')
    // a day with a conversation under half a minute still shows tinted
    const short = grid({ ...oct, days: oct.days.map((d) => d.date === '2026-10-06' ? { ...d, count: 1, minutes: 0 } : d.date === '2026-10-07' ? { ...d, count: 2, minutes: 60 } : d) })
    expect(short.flat().find((c) => c?.date === '2026-10-06')!.level).toBe(1)
  })
  it('tints in four steps against the busiest day, and says each day aloud', () => {
    expect([level(0, 90), level(10, 90), level(45, 90), level(90, 90)]).toEqual([0, 1, 2, 4])
    const c = { ...day('2026-10-28', 3, 52), people: ['Asha', 'Ravi'], to_answer: 1, state: 'day' as const, level: 2 }
    expect(spoken(c)).toBe('Wednesday 28 October: 3 conversations, 52 min, with Asha, Ravi; 1 review to answer')
    expect(spoken({ ...c, count: 0 })).toBe('Wednesday 28 October: no conversations')
    expect(minutesText(185)).toBe('3 h 5 min')
    expect(monthTitle('2026-10')).toBe('October 2026')
    expect(shift('2026-10-31', 1)).toBe('2026-11-01')
  })
  it('shows fillers per 100 words instead, tinted by rate, and says so aloud', () => {
    const f = (rate: number, fillers: number, words: number) => ({ rate, fillers, words })
    const m = { ...oct, days: oct.days.map((d) => d.date === '2026-10-06' ? { ...d, count: 3, minutes: 90, fillers: f(1, 4, 400) }
      : d.date === '2026-10-07' ? { ...d, count: 1, minutes: 10, fillers: f(4, 8, 200) } : d.date === '2026-10-08' ? { ...d, count: 1, minutes: 5 } : d) }
    const cells = grid(m, 'fillers').flat()
    const at = (iso: string) => cells.find((c) => c?.date === iso)!
    expect([at('2026-10-06').level, at('2026-10-07').level, at('2026-10-08').level]).toEqual([1, 4, 0])
    expect(grid(m).flat().find((c) => c?.date === '2026-10-06')!.level).toBe(4)  // conversations: by minutes
    expect(spoken(at('2026-10-07'), 'fillers')).toBe('Wednesday 7 October: your fillers 4 per 100 words (8 in 200)')
    expect(spoken(at('2026-10-08'), 'fillers')).toBe("Thursday 8 October: 1 conversation, none on the fillers' line")
  })
  it('routes a month and a day', () => {
    expect(parse('#/calendar')).toEqual({ name: 'calendar' })
    expect(parse('#/calendar/2026-10')).toEqual({ name: 'calendar', month: '2026-10' })
    expect(parse('#/calendar/2026-10-28')).toEqual({ name: 'calendar', day: '2026-10-28' })
  })
})
